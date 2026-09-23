import csv
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import server
from demo import dataset


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


if __name__=='__main__':unittest.main()
