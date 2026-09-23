"""Vercel's WSGI entrypoint. Reuses the existing routes and database.

Schema/user creation and data migration remain offline administration steps.
No local filesystem persistence or public registration on Vercel.
"""
import os

os.environ['APP_ENV']='cloud'
os.environ.setdefault('PGSCHEMA','seven_solutions')
if not os.environ.get('APP_ORIGIN'):
    domain=os.environ.get('VERCEL_PROJECT_PRODUCTION_URL') or os.environ.get('VERCEL_URL')
    if domain:os.environ['APP_ORIGIN']='https://'+domain

import wsgi
from vercel_transport import transport


def app(environ,start_response):
    try:
        wsgi.validate_config()
    except RuntimeError:
        return wsgi.error(start_response,503,'Сервер ещё не настроен. Администратору нужно подключить базу данных.')
    return transport(environ,start_response)
