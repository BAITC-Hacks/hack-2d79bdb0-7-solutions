"""One private team workspace: SQLite locally, PostgreSQL on hosted deployments."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager

SCHEMA='''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL, created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
        csrf TEXT NOT NULL, expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS documents(kind TEXT NOT NULL, id TEXT NOT NULL, user_id INTEGER,
        body TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(kind,id));
    CREATE TABLE IF NOT EXISTS login_failures(email TEXT NOT NULL, created REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS documents_owner ON documents(kind,user_id,created);
    CREATE INDEX IF NOT EXISTS failures_email ON login_failures(email,created);
'''


class PostgresConnection:
    def __init__(self, connection):
        self.connection=connection

    def execute(self, query, params=()):
        # All SQL is static application code, never user-provided SQL.
        return self.connection.execute(query.replace('?', '%s'), params)


@contextmanager
def postgres_connect(url):
    import certifi
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=15,
                         sslmode='verify-full', sslrootcert=certifi.where()) as connection:
        # Neon pooler disallows startup options. Apply timeout inside transaction.
        connection.execute("SET LOCAL statement_timeout = '30s'")
        yield connection


def initialize_postgres(url):
    # Explicit startup/migration step, never DDL per HTTP request.
    schema=SCHEMA.replace('id INTEGER PRIMARY KEY', 'id SERIAL PRIMARY KEY').replace('REAL', 'DOUBLE PRECISION')
    with postgres_connect(url) as db:
        for statement in schema.split(';'):
            if statement.strip():db.execute(statement)


@contextmanager
def database(path):
    url=os.environ.get('DATABASE_URL')
    if url:
        with postgres_connect(url) as connection:
            yield PostgresConnection(connection)
        return
    connection=sqlite3.connect(path, timeout=20)
    connection.row_factory=sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    connection.executescript(SCHEMA)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def password_hash(password,salt):
    return hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600_000).hex()


def register(path,email,name,password):
    email=str(email).strip().lower();name=str(name).strip()
    if len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):
        raise ValueError('Введите корректный email')
    if not 2<=len(name)<=80:
        raise ValueError('Имя должно содержать от 2 до 80 символов')
    if not isinstance(password,str) or not 8<=len(password)<=256:
        raise ValueError('Пароль должен содержать от 8 до 256 символов')
    salt=secrets.token_hex(16)
    hashed=password_hash(password,salt)
    try:
        with database(path) as db:
            cursor=db.execute('INSERT INTO users(email,name,salt,password_hash,created) VALUES(?,?,?,?,?) RETURNING id',
                              (email,name,salt,hashed,time.time()))
            return dict(id=cursor.fetchone()['id'],email=email,name=name)
    except Exception as exc:
        if isinstance(exc,sqlite3.IntegrityError) or getattr(exc,'sqlstate',None)=='23505':
            raise ValueError('Этот email уже зарегистрирован. Войдите в аккаунт.') from None
        raise


def login(path,email,password):
    email=str(email).strip().lower()
    if not isinstance(password,str) or len(password)>256:
        raise ValueError('Неверный email или пароль')
    now=time.time()
    with database(path) as db:
        db.execute('DELETE FROM login_failures WHERE created<?',(now-900,))
        failed=db.execute('SELECT count(*) AS count FROM login_failures WHERE email=?',(email,)).fetchone()['count']
        user=db.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
    if failed>=5:
        raise ValueError('Слишком много попыток. Повторите через 15 минут.')
    hashed=password_hash(password,user['salt'] if user else '00'*16)
    if not user or not hmac.compare_digest(hashed,user['password_hash']):
        with database(path) as db:
            db.execute('INSERT INTO login_failures VALUES(?,?)',(email,now))
        raise ValueError('Неверный email или пароль')
    with database(path) as db:
        db.execute('DELETE FROM login_failures WHERE email=?',(email,))
    return {k:user[k] for k in ('id','email','name')}


def create_session(path,user_id):
    token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(24)
    with database(path) as db:
        db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user_id,csrf,time.time()+43200))
    return token,csrf


def session(path,token):
    if not token:return None
    with database(path) as db:
        row=db.execute('''SELECT users.id,users.email,users.name,sessions.csrf FROM sessions
            JOIN users ON users.id=sessions.user_id WHERE token_hash=? AND expires>?''',
            (hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
    return dict(row) if row else None


def logout(path,token):
    with database(path) as db:
        db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))


def put(path,kind,key,body,user_id=None):
    with database(path) as db:
        db.execute('''INSERT INTO documents VALUES(?,?,?,?,?) ON CONFLICT(kind,id)
                   DO UPDATE SET user_id=excluded.user_id,body=excluded.body,created=excluded.created''',
                   (kind,key,user_id,json.dumps(body,ensure_ascii=False,allow_nan=False),time.time()))
        if kind=='calculation':
            db.execute("DELETE FROM documents WHERE kind='calculation' AND user_id=? AND id NOT IN (SELECT id FROM documents WHERE kind='calculation' AND user_id=? ORDER BY created DESC LIMIT 15)",(user_id,user_id))


def get(path,kind,key,user_id=None):
    with database(path) as db:
        row=db.execute('SELECT body,user_id FROM documents WHERE kind=? AND id=?',(kind,key)).fetchone()
    if not row or (user_id is not None and row['user_id']!=user_id):return None
    return json.loads(row['body'])


def clear_calculations(path):
    with database(path) as db:db.execute("DELETE FROM documents WHERE kind='calculation'")


def orders(path,user_id):
    with database(path) as db:
        rows=db.execute("SELECT body FROM documents WHERE kind='order' AND user_id=? ORDER BY created DESC LIMIT 100",(user_id,)).fetchall()
    return [dict(id=o['id'],created=o['created'],mode=o['mode'],responsible=o['responsible'],
                 lines=len(o['rows']),suppliers=sorted({r['supplier'] for r in o['rows']}))
            for row in rows for o in [json.loads(row['body'])]]
