#!/bin/sh
# Restore a backup directory, then re-apply every deletion from the live ledger
# so restored data of deleted learners never comes back.
set -eu
cd "$(dirname "$0")/.."
source_dir=${1:?usage: restore.sh <backup-directory>}
( cd "$source_dir" && shasum -a 256 -c SHA256SUMS )
docker compose stop api worker caddy
docker compose exec -T postgres dropdb -U xueban --if-exists xueban
docker compose exec -T postgres createdb -U xueban xueban
docker compose exec -T postgres pg_restore -U xueban -d xueban --no-owner < "$source_dir/database.dump"
docker compose run --rm --no-deps -T --entrypoint sh api -c 'find /data/assets -mindepth 1 -delete && tar -C /data -xf -' < "$source_dir/assets.tar"
docker compose run --rm migrate
docker compose run --rm --no-deps --entrypoint python api -m xueban_backend.governance --reapply-ledger
docker compose up -d api worker caddy
echo "restore_complete=$source_dir"
