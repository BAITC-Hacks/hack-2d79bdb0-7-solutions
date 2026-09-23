import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import server
import storage


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.old=server.DATA;server.DATA=Path(cls.tmp.name)
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.url=f'http://127.0.0.1:{cls.http.server_port}'
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join();server.DATA=cls.old;cls.tmp.cleanup()

    def request(self,path,data=None,cookie='',csrf='',origin=None):
        headers={'Content-Type':'application/json','Cookie':cookie,'X-App-Token':csrf}
        if origin:headers['Origin']=origin
        try:
            response=urlopen(Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=headers))
        except HTTPError as e:response=e
        with response:return response.status,json.load(response),response.headers

    def signup(self,name):
        status,data,headers=self.request('/api/auth/register',dict(email=name+'@example.test',name=name,password='TestOnlyPass123!'))
        self.assertEqual(status,200)
        return data,headers['Set-Cookie'].split(';')[0]

    def test_register_login_logout_and_private_status(self):
        self.assertEqual(self.request('/api/status')[0],401)
        data,cookie=self.signup('first')
        self.assertEqual(self.request('/api/auth/me',cookie=cookie)[1]['user']['name'],'first')
        self.assertEqual(self.request('/api/auth/logout',{},cookie,data['token'])[0],200)
        self.assertEqual(self.request('/api/status',cookie=cookie)[0],401)
        status,data,headers=self.request('/api/auth/login',dict(email='FIRST@example.test',password='TestOnlyPass123!'))
        self.assertEqual(status,200)
        self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        with storage.database(server.DATA/'app.sqlite3') as db:
            row=db.execute('SELECT * FROM users WHERE email=?',('first@example.test',)).fetchone()
            self.assertNotEqual(row['password_hash'],'TestOnlyPass123!')
            self.assertEqual(len(row['password_hash']),64)

    def test_password_validation_and_cross_origin(self):
        self.assertEqual(self.request('/api/auth/register',dict(email='bad@example.test',name='Bad',password='123'))[0],400)
        self.assertEqual(self.request('/api/auth/login',{},origin='https://attacker.example')[0],403)

    def test_orders_persist_and_other_user_cannot_access(self):
        a,ca=self.signup('owner');b,cb=self.signup('other')
        _,cal,_=self.request('/api/calculate',{'demo':True,'options':{}},ca,a['token'])
        server.STATE.clear()  # Emulate empty process memory: calculations are in SQLite.
        payload=dict(calculation_id=cal['calculation_id'],responsible='Owner',lines={'demo:0':250})
        self.assertEqual(self.request('/api/approve',payload,cb,b['token'])[0],409)
        status,order,_=self.request('/api/approve',payload,ca,a['token']);self.assertEqual(status,200)
        self.assertEqual(self.request('/api/orders',cookie=ca)[1]['orders'][0]['id'],order['id'])
        self.assertEqual(self.request('/api/orders',cookie=cb)[1]['orders'],[])
        self.assertEqual(self.request(order['url'],cookie=cb)[0],404)

    def test_wrong_password_rate_limit(self):
        self.signup('limited')
        for _ in range(5):
            self.assertEqual(self.request('/api/auth/login',dict(email='limited@example.test',password='wrong'))[0],400)
        status,data,_=self.request('/api/auth/login',dict(email='limited@example.test',password='TestOnlyPass123!'))
        self.assertEqual(status,400);self.assertIn('15 минут',data['error'])


if __name__=='__main__':unittest.main()
