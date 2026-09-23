"""Bounded compression for Vercel's 4.5 MB request/response limit.

Keep a margin for runtime encoding overhead. The client transparently decodes
HTTP gzip responses; imports use a custom header to avoid proxy decompression.
"""
import gzip
import io
import zlib
import wsgi

WIRE_LIMIT=3_000_000
IMPORT_LIMIT=40_000_000


def accepts_gzip(value):
    for entry in value.lower().split(','):
        coding,*params=entry.strip().split(';')
        if coding!='gzip':continue
        try:
            quality=next((float(p.strip()[2:]) for p in params if p.strip().startswith('q=')),1)
        except ValueError:return False
        return quality>0
    return False


def transport(environ,start_response):
    try:size=int(environ.get('CONTENT_LENGTH') or '0')
    except ValueError:return wsgi.error(start_response,400,'Некорректная длина запроса')
    if not 0<=size<=WIRE_LIMIT:
        return wsgi.error(start_response,413,'Файл слишком большой для загрузки на Vercel. Используйте компактный JSON или перенос базы.')
    encoding=environ.get('HTTP_X_UPLOAD_ENCODING','')
    if encoding:
        if encoding!='gzip' or environ.get('PATH_INFO')!='/api/import' or environ.get('REQUEST_METHOD')!='POST':
            return wsgi.error(start_response,400,'Сжатие разрешено только для импорта')
        packed=environ['wsgi.input'].read(size)
        if len(packed)!=size:return wsgi.error(start_response,400,'Неполный запрос')
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
                unpacked=stream.read(IMPORT_LIMIT+1)
        except (OSError,EOFError,zlib.error):
            return wsgi.error(start_response,400,'Повреждённый сжатый файл')
        if len(unpacked)>IMPORT_LIMIT:return wsgi.error(start_response,413,'После распаковки файл превышает 40 МБ')
        environ=dict(environ,CONTENT_LENGTH=str(len(unpacked)))
        environ['wsgi.input']=io.BytesIO(unpacked)
        environ.pop('HTTP_X_UPLOAD_ENCODING',None)
    captured=[]
    def capture(status,headers):captured.extend([status,headers])
    body=b''.join(wsgi.application(environ,capture))
    status,headers=captured
    if len(body)>1024 and accepts_gzip(environ.get('HTTP_ACCEPT_ENCODING','')):
        packed=gzip.compress(body,compresslevel=6,mtime=0)
        if len(packed)<len(body):
            body=packed
            headers=[(k,v) for k,v in headers if k.lower() not in ('content-length','vary')]
            headers.extend([('Content-Encoding','gzip'),('Vary','Accept-Encoding'),('Content-Length',str(len(body)))])
    if len(body)>WIRE_LIMIT:
        return wsgi.error(start_response,413,'Результат слишком большой для Vercel. Уменьшите набор данных для расчёта.')
    start_response(status,headers)
    return [body]
