import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import cloud_admin
import server
import storage
import wsgi


class HostedRoutes(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patch=patch.multiple(server,DATA=Path(self.temp.name),CLOUD=True,APP_ORIGIN='https://team.example.test')
        self.patch.start();self.addCleanup(self.patch.stop)
        self.env=patch.dict(os.environ,{},clear=True)
        self.env.start();self.addCleanup(self.env.stop)
        self.user=storage.register(server.DATA/'app.sqlite3','owner@example.test','Owner','SyntheticPass123!')

    def request(self,path,method='GET',payload=None,cookie='',token='',origin=None,host='team.example.test',size=None):
        body=json.dumps(payload).encode() if payload is not None else b''
        env={'REQUEST_METHOD':method,'PATH_INFO':path.split('?')[0],
             'QUERY_STRING':path.partition('?')[2],'HTTP_HOST':host,
             'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(body) if size is None else size),
             'HTTP_COOKIE':cookie,'HTTP_X_APP_TOKEN':token,'wsgi.input':io.BytesIO(body)}
        if origin:env['HTTP_ORIGIN']=origin
        response=[]
        result=b''.join(wsgi.application(env,lambda status,headers:response.extend([status,dict(headers)])))
        return int(response[0].split()[0]),result,response[1]

    def login(self):
        status,body,headers=self.request('/api/auth/login','POST',{'email':'owner@example.test','password':'SyntheticPass123!'})
        self.assertEqual(status,200)
        return json.loads(body)['token'],headers['Set-Cookie'].split(';')[0],headers

    def test_private_workspace_closed_registration_and_secure_session(self):
        self.assertEqual(self.request('/api/status')[0],401)
        config=json.loads(self.request('/api/config')[1]);self.assertFalse(config['registration_enabled'])
        self.assertEqual(self.request('/api/auth/register','POST',{})[0],403)
        token,cookie,headers=self.login()
        self.assertIn('; Secure',headers['Set-Cookie']);self.assertIn('Strict-Transport-Security',headers)
        self.assertEqual(self.request('/api/status',cookie=cookie)[0],200)
        self.assertEqual(self.request('/api/calculate','POST',{'demo':True},cookie,token)[0],200)
        self.assertEqual(self.request('/api/auth/logout','POST',{},cookie,token)[0],200)
        self.assertEqual(self.request('/api/status',cookie=cookie)[0],401)

    def test_host_origin_csrf_limits_and_methods(self):
        self.assertEqual(self.request('/api/config',host='evil.test')[0],403)
        token,cookie,_=self.login()
        self.assertEqual(self.request('/api/calculate','POST',{'demo':True},cookie,token,origin='http://team.example.test')[0],403)
        self.assertEqual(self.request('/api/calculate','POST',{'demo':True},cookie,'wrong')[0],403)
        self.assertEqual(self.request('/api/import','POST',size=40_000_001)[0],413)
        self.assertEqual(self.request('/api/auth/login','POST',size=4097)[0],413)
        self.assertEqual(self.request('/api/auth/login','POST',size=100)[0],400)
        self.assertEqual(self.request('/api/config','DELETE')[0],405)
        self.assertEqual(self.request('/api/config','HEAD')[1],b'')

    def test_approval_export_and_ownership_through_wsgi(self):
        token,cookie,_=self.login()
        _,body,_=self.request('/api/calculate','POST',{'demo':True},cookie,token)
        calc=json.loads(body)
        status,body,_=self.request('/api/approve','POST',{
            'calculation_id':calc['calculation_id'],'responsible':'Owner','lines':{'demo:0':250}},cookie,token)
        self.assertEqual(status,200);order=json.loads(body)
        status,csv,headers=self.request(order['url'],cookie=cookie)
        self.assertEqual(status,200);self.assertTrue(csv.startswith(b'\xef\xbb\xbf'))
        self.assertIn('text/csv',headers['Content-Type'])
        other=storage.register(server.DATA/'app.sqlite3','other@example.test','Other','SyntheticPass123!')
        foreign,_=storage.create_session(server.DATA/'app.sqlite3',other['id'])
        self.assertEqual(self.request(order['url'],cookie='seven_session='+foreign)[0],404)

    def test_cloud_configuration_fails_closed(self):
        with self.assertRaises(RuntimeError):wsgi.create_app()
        with patch.dict(os.environ,{'DATABASE_URL':'postgresql://placeholder'}):
            self.assertIs(wsgi.create_app(),wsgi.application)
            with patch.object(server,'APP_ORIGIN','http://team.example.test'):
                with self.assertRaises(RuntimeError):wsgi.create_app()


class MigrationSnapshot(unittest.TestCase):
    def test_backup_preserves_users_orders_and_legacy_without_sessions(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=True):
            root=Path(folder);source=root/'app.sqlite3'
            user=storage.register(source,'example@example.test','Example','SyntheticPass123!')
            storage.create_session(source,user['id'])
            storage.put(source,'order','known',{'rows':[]},user['id'])
            storage.put(source,'calculation','stale',{'rows':[]},user['id'])
            (root/'dataset.json').write_text('{"items":[],"meta":{}}')
            (root/'order-old.json').write_text('{"id":"old","rows":[]}')
            backup=cloud_admin.snapshot(source,root/'backups')
            users,docs=cloud_admin.read_snapshot(backup)
            self.assertEqual(users[0]['id'],user['id'])
            self.assertEqual({d['kind'] for d in docs},{'dataset','order','legacy_order'})
            self.assertEqual(next(d for d in docs if d['kind']=='order')['user_id'],user['id'])
            self.assertIsNone(next(d for d in docs if d['kind']=='legacy_order')['user_id'])
            storage.put(source,'order','later',{'rows':[]},user['id'])
            self.assertEqual(cloud_admin.read_snapshot(backup),(users,docs))

    def test_orphan_order_blocks_migration(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=True):
            path=Path(folder)/'app.sqlite3'
            storage.put(path,'order','orphan',{},999)
            with self.assertRaises(ValueError):cloud_admin.read_snapshot(path)
