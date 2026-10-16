#!/bin/sh
# Consistent backup of the database and private originals.
# The deletion ledger is deliberately NOT in this archive: it must survive a
# restore of older backups. Copy ledger/ to separate off-host storage instead.
set -eu
cd "$(dirname "$0")/.."
stamp=$(date -u +%Y%m%dT%H%M%SZ)
target=${BACKUP_DIR:-./backups}/$stamp
umask 077
mkdir -p "$target"
docker compose exec -T postgres pg_dump -U xueban -d xueban --format=custom --no-owner > "$target/database.dump"
docker compose run --rm --no-deps -T --entrypoint sh api -c 'tar -C /data -cf - assets' > "$target/assets.tar"
docker compose run --rm --no-deps -T --entrypoint sh api -c 'cat /ledger/deletions.jsonl 2>/dev/null || true' > "$target/deletion-ledger.snapshot.jsonl"
( cd "$target" && shasum -a 256 database.dump assets.tar > SHA256SUMS )
echo "backup_written=$target"
echo "Copy deletion-ledger.snapshot.jsonl to the separate ledger store; it is not a substitute for the live ledger."
