"""Opt-in real PostgreSQL test. Uses a unique isolated schema, never public data."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import cloud_admin
import storage


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'TEST_DATABASE_URL not set')
class PostgresPersistence(unittest.TestCase):
    def test_migration_login_ownership_upsert_and_retry(self):
        from psycopg import sql
        url=os.environ['TEST_DATABASE_URL']
        connect=storage.postgres_connect
        rootcert=os.environ.get('PGSSLROOTCERT')
        schema='seven_test_'+uuid.uuid4().hex
        with connect(url) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))

        @contextmanager
        def isolated(connection_url):
            tls={'PGSSLROOTCERT':rootcert} if rootcert else {}
            with patch.dict(os.environ,tls),connect(connection_url) as db:
                db.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
                yield db

        try:
            with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=True):
                path=Path(folder)/'app.sqlite3'
                user=storage.register(path,'migrated@example.test','Migrated','SyntheticPass123!')
                storage.put(path,'dataset','workspace',{'items':[],'meta':{}})
                storage.put(path,'order','kept',{'id':'kept','rows':[]},user['id'])
                users,docs=cloud_admin.read_snapshot(path)
                with patch.object(storage,'postgres_connect',isolated):
                    report=cloud_admin.migrate(url,users,docs)
                    self.assertEqual(report['orders'],1)
                    with self.assertRaises(ValueError):cloud_admin.migrate(url,users,docs)
                    with patch.dict(os.environ,{'DATABASE_URL':url}):
                        logged=storage.login(None,'migrated@example.test','SyntheticPass123!')
                        self.assertEqual(logged['id'],user['id'])
                        token,_=storage.create_session(None,user['id'])
                        self.assertEqual(storage.session(None,token)['id'],user['id'])
                        other=storage.register(None,'other@example.test','Other','SyntheticPass123!')
                        self.assertGreater(other['id'],user['id'])
                        self.assertIsNone(storage.get(None,'order','kept',other['id']))
                        self.assertEqual(storage.get(None,'order','kept',user['id'])['id'],'kept')
                        with self.assertRaises(ValueError):storage.register(None,'other@example.test','Other','SyntheticPass123!')
                        storage.put(None,'dataset','workspace',{'items':[{'code':'000_1'}]})
                        self.assertEqual(storage.get(None,'dataset','workspace')['items'][0]['code'],'000_1')
                        large={'items':[{'code':'000_1','name':'Кабель'}]*3000}
                        storage.put(None,'dataset','workspace',large)
                        self.assertEqual(storage.get(None,'dataset','workspace'),large)
                        storage.logout(None,token)
                        self.assertIsNone(storage.session(None,token))
        finally:
            # Only this test's newly generated schema is removed. public is untouched.
            with connect(url) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
