"""WSGI transport for the same tested routes, served by Gunicorn on Render.

No socket or standard-library HTTP server is started by this adapter.
"""
import io
import json
from email.message import Message
from http import HTTPStatus
import os
from urllib.parse import urlparse
import server


class WSGIHandler(server.Handler):
    def __init__(self,environ,body):
        self.command=environ['REQUEST_METHOD']
        self.path=environ.get('PATH_INFO','/')
        if environ.get('QUERY_STRING'):self.path+='?'+environ['QUERY_STRING']
        self.headers=Message()
        for key,value in environ.items():
            if key.startswith('HTTP_'):self.headers[key[5:].replace('_','-')]=value
        self.headers['Content-Type']=environ.get('CONTENT_TYPE','')
        self.headers['Content-Length']=str(len(body))
        self.rfile=io.BytesIO(body)
        self.wfile=io.BytesIO()
        self.response_headers=[]
        self.status=500

    def send_response(self,status,message=None):self.status=status
    def send_header(self,key,value):self.response_headers.append((key,value))
    def end_headers(self):pass


def error(start_response,status,message):
    body=json.dumps({'error':message},ensure_ascii=False).encode()
    start_response(f'{status} {HTTPStatus(status).phrase}',[
        ('Content-Type','application/json; charset=utf-8'),('Cache-Control','no-store'),
        ('Content-Length',str(len(body))),('X-Content-Type-Options','nosniff')])
    return [body]


def application(environ,start_response):
    method=environ.get('REQUEST_METHOD','GET')
    if method not in ('GET','HEAD','POST'):
        return error(start_response,405,'Метод не поддерживается')
    # Reject oversized requests before allocating/reading their bodies.
    try:size=int(environ.get('CONTENT_LENGTH') or '0')
    except ValueError:return error(start_response,400,'Некорректная длина запроса')
    limit=4096 if environ.get('PATH_INFO','').startswith('/api/auth/') else 40_000_000
    if not 0<=size<=limit:return error(start_response,413,'Запрос слишком большой')
    if not server.allowed_host(environ.get('HTTP_HOST','')):
        return error(start_response,403,'Недопустимый адрес сервера')
    body=environ['wsgi.input'].read(size) if method=='POST' else b''
    if method=='POST' and len(body)!=size:return error(start_response,400,'Неполный запрос')
    handler=WSGIHandler(environ,body)
    if method=='POST':handler.do_POST()
    else:handler.do_GET()
    headers=handler.response_headers
    if server.CLOUD:headers.append(('Strict-Transport-Security','max-age=31536000'))
    start_response(f'{handler.status} {HTTPStatus(handler.status).phrase}',headers)
    return [b'' if method=='HEAD' else handler.wfile.getvalue()]


def validate_config():
    if not server.CLOUD:raise RuntimeError('Set APP_ENV=cloud for hosted WSGI.')
    parsed=urlparse(server.APP_ORIGIN)
    if parsed.scheme!='https' or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise RuntimeError('Set APP_ORIGIN to the exact HTTPS origin, without a path.')
    if not os.environ.get('DATABASE_URL','').startswith(('postgresql://','postgres://')):
        raise RuntimeError('Hosted mode requires a PostgreSQL DATABASE_URL; SQLite fallback is forbidden.')


def create_app():
    validate_config()
    return application
