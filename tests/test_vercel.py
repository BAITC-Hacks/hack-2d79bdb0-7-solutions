import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import demo
import server
import storage
import vercel_transport as gateway


class VercelTransportTests(unittest.TestCase):
    def setUp(self):
        self.system_root=os.environ.get('SYSTEMROOT','')
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        env=patch.dict(os.environ,{'VERCEL':'1'},clear=True)
        env.start();self.addCleanup(env.stop)
        config=patch.multiple(server,DATA=Path(folder.name),CLOUD=True,APP_ORIGIN='https://team.example.test')
        config.start();self.addCleanup(config.stop)
        user=storage.register(server.DATA/'app.sqlite3','test@example.test','Tester','SyntheticPass123!')
        self.cookie,self.csrf=storage.create_session(server.DATA/'app.sqlite3',user['id'])

    def request(self,path,body=None,encoding='',authenticated=True,accept='gzip'):
        env={'REQUEST_METHOD':'GET' if body is None else 'POST','PATH_INFO':path,
             'HTTP_HOST':'team.example.test','HTTP_ORIGIN':'https://team.example.test',
             'CONTENT_LENGTH':str(len(body or b'')),'CONTENT_TYPE':'application/json',
             'HTTP_X_FILENAME':'dataset.json','HTTP_X_UPLOAD_ENCODING':encoding,
             'HTTP_ACCEPT_ENCODING':accept,'wsgi.input':io.BytesIO(body or b'')}
        if authenticated:env.update(HTTP_COOKIE='seven_session='+self.cookie,HTTP_X_APP_TOKEN=self.csrf)
        result=[]
        wire=b''.join(gateway.transport(env,lambda status,headers:result.extend([status,dict(headers)])))
        status,headers=result
        decoded=gzip.decompress(wire) if headers.get('Content-Encoding')=='gzip' else wire
        return int(status.split()[0]),decoded,headers

    def test_compressed_import_preserves_data_and_requires_login(self):
        dataset=demo.dataset()
        packed=gzip.compress(json.dumps(dataset).encode())
        self.assertEqual(self.request('/api/import',packed,'gzip',authenticated=False)[0],401)
        status,_,_=self.request('/api/import',packed,'gzip')
        self.assertEqual(status,200)
        saved=storage.get(server.DATA/'app.sqlite3','dataset','workspace')
        self.assertEqual(len(saved['items']),len(dataset['items']))
        self.assertEqual(saved['items'][0]['code'],dataset['items'][0]['code'])

    def test_compressed_calculation_approval_and_csv(self):
        status,body,headers=self.request('/api/calculate',b'{"demo":true}')
        self.assertEqual(status,200);self.assertEqual(headers['Content-Encoding'],'gzip')
        calc=json.loads(body)
        payload=json.dumps({'calculation_id':calc['calculation_id'],'responsible':'Tester','lines':{'demo:0':250}}).encode()
        status,body,_=self.request('/api/approve',payload)
        self.assertEqual(status,200)
        self.assertTrue(json.loads(body)['url'].startswith('/api/export?id='))

    def test_rejects_corrupt_or_excessively_expanded_upload(self):
        self.assertEqual(self.request('/api/import',b'broken','gzip')[0],400)
        with patch.object(gateway,'IMPORT_LIMIT',64):
            self.assertEqual(self.request('/api/import',gzip.compress(b'a'*65),'gzip')[0],413)
        self.assertEqual(self.request('/api/calculate',gzip.compress(b'{}'),'gzip')[0],400)

    def test_rejects_wire_limit_without_reading_body(self):
        stream=io.BytesIO(b'not read')
        statuses=[]
        gateway.transport({'CONTENT_LENGTH':str(gateway.WIRE_LIMIT+1),'wsgi.input':stream},lambda status,headers:statuses.append(status))
        self.assertTrue(statuses[0].startswith('413'));self.assertEqual(stream.tell(),0)

    def test_respects_disabled_gzip_and_reports_upload_limit(self):
        status,body,headers=self.request('/api/config',accept='gzip;q=0')
        self.assertEqual(status,200);self.assertNotIn('Content-Encoding',headers)
        self.assertEqual(json.loads(body)['max_upload_bytes'],gateway.WIRE_LIMIT)
        self.assertFalse(gateway.accepts_gzip('br, gzip;q=0'));self.assertTrue(gateway.accepts_gzip('br, gzip'))

    def test_large_uncompressed_response_fails_explicitly(self):
        with patch.object(gateway,'WIRE_LIMIT',2000):
            self.assertEqual(self.request('/api/calculate',b'{"demo":true}',accept='identity')[0],413)

    def test_hosted_import_does_not_write_application_directory(self):
        script="from unittest.mock import patch; from pathlib import Path\nwith patch.object(Path,'mkdir',side_effect=PermissionError('read-only')):\n import server\n"
        result=subprocess.run([sys.executable,'-c',script],env={**os.environ,'APP_ENV':'cloud','SYSTEMROOT':self.system_root},capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_private_schema_rejects_sql_or_unsafe_identifiers(self):
        with patch.dict(os.environ,{'PGSCHEMA':'seven_solutions'}):
            self.assertEqual(storage.postgres_schema(),'seven_solutions')
        for invalid in ('public; DROP TABLE users','a"b','a.b',''):
            with patch.dict(os.environ,{'PGSCHEMA':invalid}):
                with self.assertRaises(ValueError):storage.postgres_schema()
