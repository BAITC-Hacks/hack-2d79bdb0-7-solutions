"""Local-only HTTP application. No supplier messages or external AI calls."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime
import csv
import io
import json
import math
import mimetypes
import os
import secrets
import threading
import traceback
import zipfile
import storage
from http.cookies import SimpleCookie
from engine import calculate, num
from importer import import_zip, merge_datasets, validate_normalized
from demo import dataset as demo_dataset
from scenario import simulate

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'
DATA.mkdir(exist_ok=True)
LOCK=threading.RLock()
TOKEN=secrets.token_urlsafe(24)
STATE={}
CLOUD=os.environ.get('APP_ENV')=='cloud'
APP_ORIGIN=os.environ.get('APP_ORIGIN','').rstrip('/')
if CLOUD and not APP_ORIGIN and os.environ.get('RENDER_EXTERNAL_HOSTNAME'):
    APP_ORIGIN='https://'+os.environ['RENDER_EXTERNAL_HOSTNAME']


def allowed_host(host):
    if CLOUD:return bool(APP_ORIGIN) and host==urlparse(APP_ORIGIN).netloc
    return host.split(':')[0] in ('127.0.0.1','localhost')


def cookie_flags():
    return '; Secure' if CLOUD else ''


def read_dataset():
    saved=storage.get(DATA/'app.sqlite3','dataset','workspace')
    if saved is not None:return saved
    # Cloud database is authoritative; never import a deploy's filesystem.
    if CLOUD:return dict(items=[],meta=dict(notes=['Набор ещё не перенесён'],sources=[]))
    path=DATA/'dataset.json'
    ds=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(items=[],meta=dict(notes=['Загрузите ZIP поставщика или откройте демо'],sources=[]))
    if path.exists():storage.put(DATA/'app.sqlite3','dataset','workspace',ds)
    return ds


def atomic_json(path,data):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    temp.replace(path)


def validate_options(options):
    datetime.strptime(options.get('as_of','2026-09-23'),'%Y-%m-%d')
    for key,low,high in [('lead_days',1,365),('review_days',1,180),('safety_days',0,180),('growth_pct',-90,300)]:
        if key in options:
            value=float(options[key])
            if not math.isfinite(value) or not low<=value<=high:
                raise ValueError('Некорректный параметр '+key)
    for value in options.get('stock_overrides',{}).values():
        if not math.isfinite(float(value)) or float(value)<0:
            raise ValueError('Остаток должен быть неотрицательным числом')
    for value in options.get('category_policy',{}).values():
        if not math.isfinite(float(value)) or not 0<=float(value)<=5:
            raise ValueError('Множитель категории должен быть от 0 до 5')
    return options


class Handler(BaseHTTPRequestHandler):
    def session_token(self):
        cookie=SimpleCookie()
        try:cookie.load(self.headers.get('Cookie',''))
        except Exception:return ''
        return cookie['seven_session'].value if 'seven_session' in cookie else ''

    def current_user(self):
        return storage.session(DATA/'app.sqlite3',self.session_token())

    def calculation(self,calc_id,user_id):
        return storage.get(DATA/'app.sqlite3','calculation',str(calc_id),user_id)

    def log_message(self, fmt, *args):
        print(fmt % args)

    def send(self,body,status=200,ctype='application/json; charset=utf-8',headers=None):
        if not isinstance(body,bytes):
            body=json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        try:
            if not allowed_host(self.headers.get('Host','')):
                return self.send({'error':'Доступ разрешён только с локального компьютера'},403)
            path=urlparse(self.path).path
            if path=='/healthz':
                with storage.database(DATA/'app.sqlite3') as db:db.execute('SELECT 1')
                return self.send({'ok':True})
            if path=='/api/config':
                return self.send(dict(registration_enabled=not CLOUD,hosted=CLOUD))
            user=self.current_user() if path.startswith('/api/') else None
            if path.startswith('/api/') and not user:
                return self.send({'error':'Войдите в аккаунт'},401)
            if path=='/api/auth/me':
                return self.send(dict(user={k:user[k] for k in ('id','name','email')},token=user['csrf']))
            if path=='/api/orders':
                return self.send(dict(orders=storage.orders(DATA/'app.sqlite3',user['id'])))
            if path=='/api/status':
                with LOCK:
                    ds=read_dataset()
                return self.send(dict(token=user['csrf'],user={k:user[k] for k in ('id','name','email')},items=len(ds['items']),meta=ds['meta']))
            if path=='/api/template':
                example=demo_dataset()
                return self.send(example,headers={'Content-Disposition':'attachment; filename="normalized-example.json"'})
            if path=='/api/export':
                order_id=parse_qs(urlparse(self.path).query).get('id',[''])[0]
                if not order_id.isalnum(): return self.send({'error':'Некорректный ID'},400)
                order=storage.get(DATA/'app.sqlite3','order',order_id,user['id'])
                if not order:return self.send({'error':'Заказ не найден'},404)
                out=io.StringIO(newline=''); writer=csv.writer(out,delimiter=';')
                writer.writerow(['Набор','Поставщик','Код 1С','Артикул','Наименование','Количество','Ед.','Обоснование','Ответственный','Дата утверждения'])
                def safe(v):
                    text=str(v or '')
                    return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
                for r in order['rows']:
                    writer.writerow([order['mode'],safe(r['supplier']),safe(r['code']),safe(r.get('sku')),safe(r['name']),r['approved_quantity'],safe(r.get('unit')),safe(r['explanation']),safe(order['responsible']),order['created']])
                return self.send(out.getvalue().encode('utf-8-sig'),ctype='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="7-solutions-order-{order_id}.csv"'})
            name='index.html' if path=='/' else path.lstrip('/')
            target=(ROOT/'static'/name).resolve()
            if not target.is_relative_to(ROOT/'static') or not target.is_file(): return self.send({'error':'Не найдено'},404)
            return self.send(target.read_bytes(),ctype=mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
        except Exception:
            traceback.print_exc(); self.send({'error':'Не удалось выполнить запрос'},500)

    def do_POST(self):
        try:
            if not allowed_host(self.headers.get('Host','')):
                return self.send({'error':'Доступ разрешён только с локального компьютера'},403)
            path=urlparse(self.path).path
            if CLOUD and path=='/api/auth/register':
                return self.send({'error':'Регистрация закрыта. Обратитесь к администратору команды.'},403)
            # Same-origin JSON login/registration; cross-origin HTML forms are rejected.
            origin=self.headers.get('Origin')
            permitted_origins=(APP_ORIGIN,) if CLOUD else ('http://'+self.headers.get('Host',''), 'https://'+self.headers.get('Host',''))
            if origin and origin not in permitted_origins:
                return self.send({'error':'Недопустимый источник запроса'},403)
            public_auth=path in ('/api/auth/register','/api/auth/login')
            user=None if public_auth else self.current_user()
            if not public_auth:
                if not user:return self.send({'error':'Войдите в аккаунт'},401)
                if self.headers.get('X-App-Token')!=user['csrf']:
                    return self.send({'error':'Обновите страницу приложения'},403)
            elif not self.headers.get('Content-Type','').startswith('application/json'):
                return self.send({'error':'Требуется JSON'},415)
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=40_000_000: return self.send({'error':'Размер файла должен быть от 1 байта до 40 МБ'},400)
            if public_auth and size>4096:return self.send({'error':'Слишком большой запрос'},400)
            body=self.rfile.read(size)
            if public_auth:
                payload=json.loads(body)
                if not isinstance(payload,dict):raise ValueError('Ожидается JSON-объект')
                if path.endswith('/register'):
                    user=storage.register(DATA/'app.sqlite3',payload.get('email',''),payload.get('name',''),payload.get('password',''))
                else:user=storage.login(DATA/'app.sqlite3',payload.get('email',''),payload.get('password',''))
                session,csrf=storage.create_session(DATA/'app.sqlite3',user['id'])
                return self.send(dict(user=user,token=csrf),headers={'Set-Cookie':f'seven_session={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200'+cookie_flags()})
            if path=='/api/auth/logout':
                storage.logout(DATA/'app.sqlite3',self.session_token())
                return self.send({'ok':True},headers={'Set-Cookie':'seven_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'+cookie_flags()})
            path=urlparse(self.path).path
            if path=='/api/import':
                filename=self.headers.get('X-Filename','')
                ds=validate_normalized(json.loads(body)) if filename.endswith('.json') else import_zip(body,filename)
                with LOCK:
                    existing=read_dataset()
                    suppliers={i['supplier'] for i in ds['items']}
                    if filename.endswith('.json'):
                        merged=ds
                    else:
                        existing['items']=[i for i in existing['items'] if i['supplier'] not in suppliers]
                        # Sources are deduplicated by filename after supplier refresh.
                        merged=merge_datasets([existing,ds])
                        merged['meta']['sources']=list({s['file']:s for s in merged['meta']['sources']}.values())
                    storage.put(DATA/'app.sqlite3','dataset','workspace',merged)
                    storage.clear_calculations(DATA/'app.sqlite3')
                    STATE.clear()
                return self.send(dict(items=len(merged['items']),meta=merged['meta']))
            payload=json.loads(body)
            if not isinstance(payload,dict):raise ValueError('Ожидается JSON-объект')
            if path=='/api/calculate':
                options=validate_options(payload.get('options',{}))
                mode='demo' if payload.get('demo') else 'real'
                with LOCK:
                    ds=demo_dataset() if mode=='demo' else read_dataset()
                    if ds.get('meta',{}).get('demo'):
                        mode='demo'
                    result=calculate(ds,options)
                    calc_id=secrets.token_hex(12)
                    STATE[calc_id]=dict(result=result,mode=mode)
                    storage.put(DATA/'app.sqlite3','calculation',calc_id,STATE[calc_id],user['id'])
                    while len(STATE)>15: STATE.pop(next(iter(STATE)))
                result=dict(result,calculation_id=calc_id,mode=mode)
                return self.send(result)
            if path=='/api/scenario':
                with LOCK:
                    state=self.calculation(payload.get('calculation_id'),user['id'])
                    if not state: return self.send({'error':'Расчёт устарел. Пересчитайте рекомендации'},409)
                    row=next((r for r in state['result']['rows'] if r['id']==payload.get('id')),None)
                    if row is None: raise ValueError('Неизвестный товар сценария')
                    scenario=simulate(row,state['result']['options'],payload.get('shock',{}))
                return self.send(scenario)
            if path=='/api/approve':
                with LOCK:
                    state=self.calculation(payload.get('calculation_id'),user['id'])
                    if not state: return self.send({'error':'Расчёт устарел. Пересчитайте рекомендации'},409)
                    responsible=str(payload.get('responsible','')).strip()
                    if not responsible: raise ValueError('Укажите ответственного сотрудника')
                    chosen=payload.get('lines',{})
                    if not chosen: raise ValueError('Выберите хотя бы одну позицию')
                    rows={r['id']:r for r in state['result']['rows']}
                    approved=[]
                    for key,value in sorted(chosen.items(),key=lambda pair:rows.get(pair[0],{}).get('supplier','')):
                        if key not in rows: raise ValueError('Неизвестная позиция заказа')
                        row=rows[key]
                        if row['blockers']: raise ValueError(row['code']+': '+', '.join(row['blockers']))
                        qty=float(value)
                        if not math.isfinite(qty) or qty<=0 or qty>1e9: raise ValueError('Некорректное количество')
                        if qty<row['minimum'] or abs(qty/row['pack']-round(qty/row['pack']))>1e-7:
                            raise ValueError(row['code']+': нарушена минимальная партия или кратность')
                        approved.append(dict(row,approved_quantity=qty))
                    order_id=secrets.token_hex(8)
                    order=dict(id=order_id,created=datetime.now().isoformat(timespec='seconds'),
                        responsible=responsible,mode=state['mode'],options=state['result']['options'],rows=approved)
                    storage.put(DATA/'app.sqlite3','order',order_id,order,user['id'])
                return self.send(dict(id=order_id,lines=len(approved),url='/api/export?id='+order_id))
            self.send({'error':'Не найдено'},404)
        except (ValueError,KeyError,TypeError,OverflowError,zipfile.BadZipFile) as e:
            self.send({'error':str(e)},400)
        except Exception:
            traceback.print_exc(); self.send({'error':'Не удалось обработать данные. Проверьте формат файла'},500)


if __name__=='__main__':
    if CLOUD:raise SystemExit('Hosted mode requires gunicorn wsgi:application, not the development server.')
    import argparse
    import zipfile
    p=argparse.ArgumentParser(); p.add_argument('--port',type=int,default=8765)
    args=p.parse_args()
    print(f'7-Solutions: http://127.0.0.1:{args.port} — Ctrl+C для остановки',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
