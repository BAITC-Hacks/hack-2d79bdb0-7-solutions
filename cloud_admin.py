"""Offline administration. Never expose this module as an HTTP endpoint.

Read a PostgreSQL credential from DATABASE_URL or a local ignored URL file.
No credentials, email addresses or document bodies are printed by this CLI.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import storage


def snapshot(source,backup_dir):
    source=Path(source).resolve()
    if not source.is_file():raise ValueError('Source SQLite file does not exist')
    folder=Path(backup_dir)/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder.mkdir(parents=True,exist_ok=False)
    target=folder/'app.sqlite3'
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as original:
        with closing(sqlite3.connect(target)) as backup:
            original.backup(backup)
            if backup.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('SQLite integrity check failed')
    for path in [source.parent/'dataset.json',*source.parent.glob('order-*.json')]:
        if path.is_file():shutil.copy2(path,folder/path.name)
    return target


def read_snapshot(source):
    """Read an immutable backup, retaining ownership and exact serialized bodies."""
    source=Path(source).resolve()
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        users=[dict(row) for row in db.execute('SELECT * FROM users ORDER BY id')]
        documents=[dict(row) for row in db.execute(
            "SELECT * FROM documents WHERE kind IN ('dataset','order') ORDER BY kind,id")]
    owners={u['id'] for u in users}
    for doc in documents:
        storage.decode_document(doc['body'])
        if doc['kind']=='order' and doc['user_id'] not in owners:
            raise ValueError('An order has no valid owner; resolve locally before migration')
    if not any(d['kind']=='dataset' and d['id']=='workspace' for d in documents):
        legacy=source.parent/'dataset.json'
        if legacy.exists():
            body=legacy.read_text(encoding='utf-8');json.loads(body)
            documents.append(dict(kind='dataset',id='workspace',user_id=None,body=body,created=legacy.stat().st_mtime))
    for path in source.parent.glob('order-*.json'):
        body=path.read_text(encoding='utf-8');item=json.loads(body)
        # Original app didn't record an account owner. Preserve privately without
        # inventing one or making these records visible to all accounts.
        documents.append(dict(kind='legacy_order',id=path.stem,user_id=None,body=body,created=path.stat().st_mtime))
    return users,documents


def migrate(url,users,documents,compress_documents=False):
    if compress_documents:
        documents=[dict(doc,body=storage.encode_document(storage.decode_document(doc['body']),compress=True)) for doc in documents]
    storage.initialize_postgres(url)
    with storage.postgres_connect(url) as db:
        db.execute('LOCK TABLE users,sessions,documents,login_failures IN ACCESS EXCLUSIVE MODE')
        for table in ('users','sessions','documents','login_failures'):
            if db.execute(f'SELECT count(*) AS count FROM {table}').fetchone()['count']:
                raise ValueError('Destination must be empty; existing cloud data was not changed')
        for row in users:
            db.execute('INSERT INTO users(id,email,name,salt,password_hash,created) VALUES(%s,%s,%s,%s,%s,%s)',
                       tuple(row[k] for k in ('id','email','name','salt','password_hash','created')))
        for row in documents:
            db.execute('INSERT INTO documents(kind,id,user_id,body,created) VALUES(%s,%s,%s,%s,%s)',
                       tuple(row[k] for k in ('kind','id','user_id','body','created')))
        # Compare every imported field before committing. Failure rolls back.
        actual_users=list(db.execute('SELECT * FROM users ORDER BY id'))
        actual_docs=list(db.execute('SELECT * FROM documents ORDER BY kind,id'))
        if actual_users!=sorted(users,key=lambda r:r['id']) or actual_docs!=sorted(documents,key=lambda r:(r['kind'],r['id'])):
            raise ValueError('Migration verification failed; transaction rolled back')
        db.execute("SELECT setval(pg_get_serial_sequence('users','id'),%s,%s)",
                   (max((u['id'] for u in users),default=1),bool(users)))
    return dict(users=len(users),documents=len(documents),sessions_migrated=0,
                orders=sum(d['kind']=='order' for d in documents),
                legacy_orders_archived=sum(d['kind']=='legacy_order' for d in documents))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url-file',type=Path,help='Private file containing only the database URL, never commit it')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init')
    transfer=sub.add_parser('migrate')
    transfer.add_argument('--source',type=Path,default=Path('data/app.sqlite3'))
    transfer.add_argument('--backup-dir',type=Path,default=Path('data/backups'))
    transfer.add_argument('--compress-documents',action='store_true',help='Losslessly compress large documents for serverless databases')
    sub.add_parser('create-user')
    args=parser.parse_args()
    url=args.url_file.read_text(encoding='utf-8-sig').strip() if args.url_file else os.environ.get('DATABASE_URL','')
    if not url.startswith(('postgresql://','postgres://')):
        raise ValueError('Provide DATABASE_URL or --url-file with a PostgreSQL connection URL')
    if args.command=='init':storage.initialize_postgres(url);print('Database schema initialized')
    elif args.command=='migrate':
        backup=snapshot(args.source,args.backup_dir)
        users,documents=read_snapshot(backup)
        print(json.dumps(migrate(url,users,documents,compress_documents=args.compress_documents)))
        print('Verified private local backup:',backup)
    else:
        storage.initialize_postgres(url)
        os.environ['DATABASE_URL']=url
        email=input('Team member email: ');name=input('Team member name: ')
        password=getpass.getpass('New application password: ')
        if password!=getpass.getpass('Repeat password: '):raise ValueError('Passwords differ')
        user=storage.register(None,email,name,password)
        print('Created team user ID:',user['id'])


if __name__=='__main__':
    try:main()
    except Exception as exc:
        # Driver exceptions may embed connection info or private row contents.
        print('Operation failed; no partial migration was committed. Error type:',type(exc).__name__,file=sys.stderr)
        sys.exit(1)
