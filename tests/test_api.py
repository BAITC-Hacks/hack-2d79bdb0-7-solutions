import csv
import io
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import server
from demo import dataset
from test_importer import synthetic_zip


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.old=server.DATA
        server.DATA=Path(cls.tmp.name)
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.url=f'http://127.0.0.1:{cls.http.server_port}'
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join()
        server.DATA=cls.old;cls.tmp.cleanup()

    def post(self,path,payload,token=True,headers=None):
        h={'Content-Type':'application/json',**(headers or {})}
        if token:h['X-App-Token']=server.TOKEN
        request=Request(self.url+path,data=json.dumps(payload).encode(),headers=h)
        try:
            with urlopen(request) as r:return r.status,json.load(r)
        except HTTPError as e:return e.code,json.load(e)

    def test_approval_then_csv_export(self):
        status,cal=self.post('/api/calculate',{'demo':True,'options':{}})
        self.assertEqual(status,200)
        row=next(r for r in cal['rows'] if r['quantity']>0)
        status,order=self.post('/api/approve',dict(calculation_id=cal['calculation_id'],responsible='QA',lines={row['id']:row['quantity']}))
        self.assertEqual(status,200)
        with urlopen(self.url+order['url']) as response:
            lines=list(csv.reader(io.StringIO(response.read().decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(lines),2)
        self.assertEqual(lines[1][0],'demo')
        self.assertEqual(float(lines[1][5]),row['quantity'])
        self.assertEqual(lines[1][8],'QA')

    def test_approval_enforces_pack_on_server(self):
        _,cal=self.post('/api/calculate',{'demo':True,'options':{}})
        status,_=self.post('/api/approve',dict(calculation_id=cal['calculation_id'],responsible='QA',lines={'demo:0':11}))
        self.assertEqual(status,400)

    def test_missing_stock_cannot_be_approved(self):
        ds=dataset();ds['items'][0].update(stock=None,stock_kind='missing')
        server.atomic_json(server.DATA/'dataset.json',ds)
        _,cal=self.post('/api/calculate',{'options':{}})
        status,_=self.post('/api/approve',dict(calculation_id=cal['calculation_id'],responsible='QA',lines={'demo:0':10}))
        self.assertEqual(status,400)

    def test_import_invalidates_old_calculation(self):
        _,cal=self.post('/api/calculate',{'demo':True,'options':{}})
        status,_=self.post('/api/import',dataset(),headers={'X-Filename':'test.json'})
        self.assertEqual(status,200)
        status,_=self.post('/api/approve',dict(calculation_id=cal['calculation_id'],responsible='QA',lines={'demo:0':10}))
        self.assertEqual(status,409)

    def test_mutation_requires_local_token(self):
        status,_=self.post('/api/calculate',{'demo':True},token=False)
        self.assertEqual(status,403)

    def test_invalid_options_are_rejected(self):
        status,_=self.post('/api/calculate',{'demo':True,'options':{'lead_days':-1}})
        self.assertEqual(status,400)

    def test_outliers_always_enabled_in_dashboard_api(self):
        _,cal=self.post('/api/calculate',{'demo':True,'options':{'exclude_outliers':False}})
        self.assertTrue(cal['options']['exclude_outliers'])
        self.assertEqual(next(r for r in cal['rows'] if r['code']=='DEMO-001')['quantity'],250)

    def test_recommendation_export_uses_server_values_and_does_not_approve(self):
        _,cal=self.post('/api/calculate',{'demo':True})
        row=next(r for r in cal['rows'] if r['quantity']>0)
        before=set(server.DATA.glob('order-*.json'))
        req=Request(self.url+'/api/recommendations/export',data=json.dumps({
            'calculation_id':cal['calculation_id'],'ids':[row['id']],'quantity':999999}).encode(),
            headers={'X-App-Token':server.TOKEN})
        with urlopen(req) as response:
            rows=list(csv.reader(io.StringIO(response.read().decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(rows),2)
        self.assertEqual(float(rows[1][2]),row['quantity'])
        self.assertEqual(rows[1][-1],'Рекомендация')
        self.assertEqual(set(server.DATA.glob('order-*.json')),before)
        status,_=self.post('/api/recommendations/export',{'calculation_id':cal['calculation_id'],'ids':['unknown']})
        self.assertEqual(status,400)

    def test_ai_cached_and_does_not_change_quantity(self):
        _,cal=self.post('/api/calculate',{'demo':True})
        row=next(r for r in cal['rows'] if r['quantity']>0)
        payload={'calculation_id':cal['calculation_id'],'ids':[row['id']]}
        with patch.object(server.ai_explanations,'explain',return_value=({row['id']:'Пополнение поддержит спрос.'},'ready')):
            status,answer=self.post('/api/explain',payload)
        self.assertEqual(status,200)
        self.assertIn(row['id'],answer['explanations'])
        saved=next(r for r in server.STATE[cal['calculation_id']]['result']['rows'] if r['id']==row['id'])
        self.assertEqual(saved['quantity'],row['quantity'])
        with patch.object(server.ai_explanations,'explain',return_value=({},'ready')) as call:
            self.post('/api/explain',payload)
            self.assertEqual(call.call_args.args[0],[])

    def test_zip_reconciliation_survives_supplier_refresh(self):
        for supplier, quantity in [('IEK', 8), ('Systeme Electric', 15), ('IEK', 10)]:
            request = Request(self.url+'/api/import', data=synthetic_zip(supplier, quantity),
                              headers={'X-App-Token': server.TOKEN, 'X-Filename': 'synthetic.zip'})
            with urlopen(request) as response:
                self.assertEqual(response.status, 200)
        with urlopen(self.url+'/api/status') as response:
            reports = json.load(response)['meta']['reconciliation']
        self.assertEqual(reports['IEK']['rows'][0]['delta'], -2)
        self.assertEqual(reports['Systeme Electric']['rows'][0]['delta'], -7)
        _, calculation = self.post('/api/calculate', {'options': {}})
        self.assertIn('rows', calculation)


if __name__=='__main__':unittest.main()
