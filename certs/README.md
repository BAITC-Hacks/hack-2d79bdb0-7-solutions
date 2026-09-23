`supabase-ca.crt` is the public Supabase root CA linked from Database Settings → SSL configuration.

Source: https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt

It contains no private key or account credentials. Vercel sets `PGSSLROOTCERT=certs/supabase-ca.crt`; keep `sslmode=verify-full`. Other PostgreSQL providers can use their trusted CA through PGSSLROOTCERT or the default certifi bundle.
