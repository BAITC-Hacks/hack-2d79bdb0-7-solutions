"""Validated cloud startup and one-time team owner provisioning.

Secrets are set in the host's environment, never passed in process arguments.
An existing database/account is never replaced or reset on redeploy.
"""
import os
import sys
import storage


def bootstrap_owner(connection, environ):
    # Called inside the startup transaction and advisory lock. The lock is
    # shared by deployments, so two starting instances cannot create two owners.
    if connection.execute('SELECT count(*) AS count FROM users').fetchone()['count']:
        return False
    email=environ.get('FIRST_USER_EMAIL','').strip().lower()
    name=environ.get('FIRST_USER_NAME','Администратор команды').strip()
    password=environ.get('FIRST_USER_PASSWORD','')
    if not email or not password:
        raise ValueError('An empty database requires FIRST_USER_EMAIL and FIRST_USER_PASSWORD')
    if not 12<=len(password)<=256:
        raise ValueError('First user password must contain 12–256 characters')
    email,name,salt,hashed=storage.new_user_fields(email,name,password)
    import time
    connection.execute('INSERT INTO users(email,name,salt,password_hash,created) VALUES(%s,%s,%s,%s,%s)',
                       (email,name,salt,hashed,time.time()))
    return True


def prepare():
    import wsgi
    # Validate origin and PostgreSQL before touching the database.
    wsgi.validate_config()
    url=os.environ['DATABASE_URL']
    storage.initialize_postgres(url)
    with storage.postgres_connect(url) as db:
        db.execute('SELECT pg_advisory_xact_lock(73402109)')
        created=bootstrap_owner(db,os.environ)
        db.execute('SELECT 1')
    return created


def main():
    try:
        port=int(os.environ.get('PORT','10000'))
        if not 1<=port<=65535:raise ValueError('Invalid PORT')
        created=prepare()
    except Exception as exc:
        # Provider exceptions may contain credentials. Never log their text.
        print('Cloud startup failed ('+type(exc).__name__+'). Check APP_ENV, HTTPS origin, '
              'DATABASE_URL and first-user secrets. See DEPLOYMENT.md.',file=sys.stderr)
        return 1
    print('Database ready; '+('first team account created.' if created else 'existing accounts preserved.'),flush=True)
    # Secrets are no longer needed by the HTTP workers. Remove them from the
    # child environment (also delete from the hosting dashboard after setup).
    for name in ('FIRST_USER_EMAIL','FIRST_USER_PASSWORD','FIRST_USER_NAME'):
        os.environ.pop(name,None)
    os.execv(sys.executable,[sys.executable,'-m','gunicorn','wsgi:create_app()',
        '--bind',f'0.0.0.0:{port}','--workers','1','--threads','4',
        '--timeout','180','--access-logfile','-'])


if __name__=='__main__':
    sys.exit(main())
