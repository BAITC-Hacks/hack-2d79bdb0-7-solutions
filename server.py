"""Local recommendations with optional OpenAI explanations. No supplier messages."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime
import csv
import io
import json
import math
import mimetypes
import secrets
import threading
import traceback
import zipfile
from engine import calculate, num
from importer import import_zip, merge_datasets, validate_normalized
from demo import dataset as demo_dataset
import ai_explanations

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'
DATA.mkdir(exist_ok=True)
LOCK=threading.RLock()
TOKEN=secrets.token_urlsafe(24)
STATE={}
AI_LOCK=threading.Lock()


def recommendation_reason(row):
    return (row.get('ai_explanation', '') + ' ' + row['explanation'].split(' Остаток неизвестен:')[0]).strip()


def selected_recommendations(state, payload, maximum=10000):
    ids=payload.get('ids')
    if not isinstance(ids,list) or not 0<len(ids)<=maximum or any(not isinstance(i,str) for i in ids):
        raise ValueError('Выберите позиции рекомендаций')
    if len(set(ids))!=len(ids):
        raise ValueError('Позиции не должны повторяться')
    rows={r['id']:r for r in state['result']['rows'] if r['quantity']>0}
    if any(i not in rows for i in ids):
        raise ValueError('Неизвестная рекомендация')
    return [rows[i] for i in ids]


def read_dataset():
    path=DATA/'dataset.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(items=[],meta=dict(notes=['Загрузите ZIP поставщика или откройте демо'],sources=[]))


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
            if self.headers.get('Host','').split(':')[0] not in ('127.0.0.1','localhost'):
                return self.send({'error':'Доступ разрешён только с локального компьютера'},403)
            path=urlparse(self.path).path
            if path=='/api/status':
                with LOCK:
                    ds=read_dataset()
                return self.send(dict(token=TOKEN,items=len(ds['items']),meta=ds['meta'],ai_configured=bool(ai_explanations.config()[0])))
            if path=='/api/template':
                example=demo_dataset()
                return self.send(example,headers={'Content-Disposition':'attachment; filename="normalized-example.json"'})
            if path=='/api/export':
                order_id=parse_qs(urlparse(self.path).query).get('id',[''])[0]
                if not order_id.isalnum(): return self.send({'error':'Некорректный ID'},400)
                file=DATA/('order-'+order_id+'.json')
                if not file.exists(): return self.send({'error':'Заказ не найден'},404)
                order=json.loads(file.read_text(encoding='utf-8'))
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
            if self.headers.get('Host','').split(':')[0] not in ('127.0.0.1','localhost'):
                return self.send({'error':'Доступ разрешён только с локального компьютера'},403)
            if self.headers.get('X-App-Token')!=TOKEN:
                return self.send({'error':'Обновите страницу приложения'},403)
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=40_000_000: return self.send({'error':'Размер файла должен быть от 1 байта до 40 МБ'},400)
            body=self.rfile.read(size)
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
                    atomic_json(DATA/'dataset.json',merged)
                    STATE.clear()
                return self.send(dict(items=len(merged['items']),meta=merged['meta']))
            payload=json.loads(body)
            if not isinstance(payload,dict):
                raise ValueError('Ожидается объект JSON')
            if path in ('/api/explain','/api/recommendations/export'):
                with LOCK:
                    state=STATE.get(payload.get('calculation_id'))
                    if not state: return self.send({'error':'Пересчитайте рекомендации'},409)
                    chosen=selected_recommendations(state,payload,25 if path=='/api/explain' else 10000)
                    if path=='/api/recommendations/export':
                        out=io.StringIO(newline=''); writer=csv.writer(out,delimiter=';')
                        writer.writerow(['Артикул','Поставщик','Рекомендуемое количество','Ед.','Обоснование','Срочность','Набор','Статус'])
                        def safe(value):
                            text=str(value or '')
                            return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
                        for row in sorted(chosen,key=lambda r:(r['supplier'],r['sku'] or r['code'])):
                            writer.writerow([safe(row.get('sku') or row['code']),safe(row['supplier']),row['quantity'],
                                safe(row['unit']),safe(recommendation_reason(row)),
                                'Срочно' if row['shortage_day'] is not None else 'Планово',state['mode'],'Рекомендация'])
                        return self.send(out.getvalue().encode('utf-8-sig'),ctype='text/csv; charset=utf-8')
                    pending=[dict(r) for r in chosen if not r.get('ai_explanation')]
                if not AI_LOCK.acquire(blocking=False):
                    return self.send({'error':'Обоснования уже формируются. Повторите позже.'},429)
                try:
                    explanations,status=ai_explanations.explain(pending)
                finally:
                    AI_LOCK.release()
                with LOCK:
                    if STATE.get(payload.get('calculation_id')) is not state:
                        return self.send({'error':'Пересчитайте рекомендации'},409)
                    for row in chosen:
                        if row['id'] in explanations: row['ai_explanation']=explanations[row['id']]
                    return self.send(dict(status=status,explanations={r['id']:r.get('ai_explanation','') for r in chosen}))
            if path=='/api/calculate':
                options=validate_options(payload.get('options',{}))
                options['exclude_outliers']=True
                mode='demo' if payload.get('demo') else 'real'
                with LOCK:
                    ds=demo_dataset() if mode=='demo' else read_dataset()
                    if ds.get('meta',{}).get('demo'):
                        mode='demo'
                    result=calculate(ds,options)
                    calc_id=secrets.token_hex(12)
                    STATE[calc_id]=dict(result=result,mode=mode)
                    while len(STATE)>15: STATE.pop(next(iter(STATE)))
                result=dict(result,calculation_id=calc_id,mode=mode)
                return self.send(result)
            if path=='/api/approve':
                with LOCK:
                    state=STATE.get(payload.get('calculation_id'))
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
                    atomic_json(DATA/('order-'+order_id+'.json'),order)
                return self.send(dict(id=order_id,lines=len(approved),url='/api/export?id='+order_id))
            self.send({'error':'Не найдено'},404)
        except (ValueError,KeyError,TypeError,OverflowError,zipfile.BadZipFile) as e:
            self.send({'error':str(e)},400)
        except Exception:
            traceback.print_exc(); self.send({'error':'Не удалось обработать данные. Проверьте формат файла'},500)


if __name__=='__main__':
    import argparse
    import zipfile
    p=argparse.ArgumentParser(); p.add_argument('--port',type=int,default=8765)
    args=p.parse_args()
    print(f'7-Solutions: http://127.0.0.1:{args.port} — Ctrl+C для остановки',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
