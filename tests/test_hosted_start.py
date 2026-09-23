"""Offline tests of first-user provisioning with a real transactional database."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import hosted_start
import storage


class SQLAdapter:
    def __init__(self,db):self.db=db
    def execute(self,query,args=()):return self.db.execute(query.replace('%s','?'),args)


class FirstOwnerTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.path=Path(folder.name)/'app.sqlite3'
        self.env=patch.dict(os.environ,{},clear=True)
        self.env.start();self.addCleanup(self.env.stop)
        self.settings={'FIRST_USER_EMAIL':'owner@example.test','FIRST_USER_PASSWORD':'SyntheticOnly123!',
                       'FIRST_USER_NAME':'Owner'}

    def bootstrap(self,settings):
        with storage.database(self.path) as db:
            return hosted_start.bootstrap_owner(SQLAdapter(db),settings)

    def test_first_account_can_login_password_is_hashed(self):
        self.assertTrue(self.bootstrap(self.settings))
        user=storage.login(self.path,self.settings['FIRST_USER_EMAIL'],self.settings['FIRST_USER_PASSWORD'])
        self.assertEqual(user['name'],'Owner')
        with storage.database(self.path) as db:
            row=db.execute('SELECT * FROM users').fetchone()
            self.assertNotEqual(row['password_hash'],self.settings['FIRST_USER_PASSWORD'])
            self.assertEqual(len(row['password_hash']),64)

    def test_redeploy_without_secrets_preserves_account_and_documents(self):
        self.bootstrap(self.settings)
        user=storage.login(self.path,self.settings['FIRST_USER_EMAIL'],self.settings['FIRST_USER_PASSWORD'])
        storage.put(self.path,'dataset','workspace',{'items':[{'code':'000_1'}]})
        storage.put(self.path,'order','saved',{'id':'saved'},user['id'])
        self.assertFalse(self.bootstrap({}))
        self.assertFalse(self.bootstrap({**self.settings,'FIRST_USER_PASSWORD':'Replacement123!'}))
        self.assertEqual(storage.login(self.path,self.settings['FIRST_USER_EMAIL'],self.settings['FIRST_USER_PASSWORD']),user)
        self.assertEqual(storage.get(self.path,'dataset','workspace')['items'][0]['code'],'000_1')
        self.assertEqual(storage.get(self.path,'order','saved',user['id']),{'id':'saved'})
        with storage.database(self.path) as db:self.assertEqual(db.execute('SELECT count(*) AS count FROM users').fetchone()['count'],1)

    def test_missing_or_invalid_secrets_do_not_create_account(self):
        for settings in ({},{**self.settings,'FIRST_USER_PASSWORD':'short'},
                         {**self.settings,'FIRST_USER_EMAIL':'not-email'}):
            with self.subTest(settings=list(settings)):
                with self.assertRaises(ValueError):self.bootstrap(settings)
                with storage.database(self.path) as db:
                    self.assertEqual(db.execute('SELECT count(*) AS count FROM users').fetchone()['count'],0)

    def test_bad_cloud_config_does_not_initialize_database(self):
        import server
        with patch.object(server,'CLOUD',False),patch.object(storage,'initialize_postgres') as initialize:
            with self.assertRaises(RuntimeError):hosted_start.prepare()
            initialize.assert_not_called()
