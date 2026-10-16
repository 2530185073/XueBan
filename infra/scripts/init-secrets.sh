#!/bin/sh
# Create deployment secrets once. Existing files are never overwritten.
set -eu
cd "$(dirname "$0")/.."
umask 077
mkdir -p secrets
if [ ! -f secrets/postgres_password ]; then
  python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > secrets/postgres_password
fi
if [ ! -f secrets/database_url ]; then
  printf 'postgresql+psycopg://xueban:%s@postgres:5432/xueban\n' "$(cat secrets/postgres_password)" > secrets/database_url
fi
if [ ! -f secrets/issuer-private.pem ]; then
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out secrets/issuer-private.pem 2>/dev/null
  openssl pkey -in secrets/issuer-private.pem -pubout -out secrets/issuer-public.pem
fi
chmod 600 secrets/*
# Compose bind-mounts secret files with host ownership. The app runs as uid
# 10001 and PostgreSQL as uid 999; keep 0600 and hand each file to its reader.
if [ "$(id -u)" = 0 ]; then
  chown 10001:10001 secrets/database_url secrets/issuer-private.pem secrets/issuer-public.pem
  chown 999:999 secrets/postgres_password
else
  echo "Run as root (sudo) once to set secret file ownership for the containers." >&2
fi
echo "secrets_ready=infra/secrets (contents not displayed)"
