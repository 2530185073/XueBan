"""Narrow private-asset operations for isolated recovery; never runs a worker."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile
import tempfile

ASSET_ID = re.compile(r"asset_[A-Za-z0-9]+\Z")
MAX_BYTES = 4 * 1024 * 1024


def validated_manifest(items):
    seen = set()
    for item in items:
        identifier = item.get("id", "")
        if (not ASSET_ID.fullmatch(identifier) or identifier in seen
                or not re.fullmatch(r"[a-f0-9]{64}", item.get("sha256", ""))
                or type(item.get("byte_size")) is not int or not 0 < item["byte_size"] <= MAX_BYTES):
            raise ValueError("invalid_asset_manifest")
        seen.add(identifier)
    return items


def read_verified(root, item):
    descriptor = os.open(Path(root) / item["id"], os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("asset_not_regular")
        data = stream.read(MAX_BYTES + 1)
    if len(data) != item["byte_size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError("asset_integrity_mismatch")
    return data


def pack(root, items, output):
    with tarfile.open(fileobj=output, mode="w|") as archive:
        for item in validated_manifest(items):
            data = read_verified(root, item)
            entry = tarfile.TarInfo(item["id"])
            entry.size, entry.mode = len(data), 0o600
            archive.addfile(entry, io.BytesIO(data))


def unpack(root, source):
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    seen = set()
    with tarfile.open(fileobj=source, mode="r|*") as archive:
        for entry in archive:
            if (not entry.isfile() or not ASSET_ID.fullmatch(entry.name) or entry.name in seen
                    or not 0 < entry.size <= MAX_BYTES):
                raise ValueError("unsafe_asset_archive")
            seen.add(entry.name)
            # No extractall: archive paths, links, ownership and modes are never used.
            with os.fdopen(os.open(root / entry.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as out:
                content = archive.extractfile(entry).read(MAX_BYTES + 1)
                if len(content) != entry.size:
                    raise ValueError("truncated_asset_archive")
                out.write(content)


def verify(root, items):
    items = validated_manifest(items)
    for item in items:
        read_verified(root, item)
    actual = {p.name for p in Path(root).iterdir() if p.name != ".recovery-owner"}
    if actual != {item["id"] for item in items}:
        raise ValueError("asset_set_mismatch")
    return {"verified_assets": len(items), "verified_bytes": sum(i["byte_size"] for i in items)}


def validate_ledger(data):
    entries = []
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if (not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k]
                for k in ("deletion_id", "tenant_id", "student_id"))
                or not isinstance(item.get("asset_ids", []), list)
                or any(not isinstance(a, str) or not ASSET_ID.fullmatch(a) for a in item.get("asset_ids", []))):
            raise ValueError("invalid_deletion_ledger")
        entries.append(item)
    return entries


def read_ledger(path):
    """Missing ledger storage is a recovery failure, never an empty history."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        mode = os.fstat(source.fileno()).st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise ValueError("deletion_ledger_not_private_regular")
        data = source.read()
    validate_ledger(data)
    return data


def replay(target_database, data, owner_marker):
    from sqlalchemy.engine import make_url
    from sqlalchemy import create_engine, text
    from types import SimpleNamespace
    from xueban_backend.config import database_url_from_environment
    from xueban_backend.governance import DeletionExecutor
    validate_ledger(data)
    source = make_url(database_url_from_environment())
    if (not re.fullmatch(r"xueban_restore_[a-z0-9_]{8,45}", target_database)
            or source.database == target_database):
        raise ValueError("unsafe_recovery_database")
    # Only the explicit isolated target receives the latest ledger; source URL is
    # read in-process and never placed in a subprocess argument or output.
    target = source.set(database=target_database)
    with tempfile.TemporaryDirectory(prefix="xueban-ledger-") as temporary:
        ledger = Path(temporary) / "latest.jsonl"
        ledger.write_bytes(data); ledger.chmod(0o600)
        engine = create_engine(target)
        try:
            with engine.connect() as connection:
                marker = connection.scalar(text("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"))
            if not owner_marker or marker != "xueban-recovery:" + owner_marker:
                raise ValueError("recovery_database_owner_mismatch")
            settings = SimpleNamespace(asset_storage_dir=Path(os.environ["ASSET_STORAGE_DIR"]), deletion_ledger_path=ledger)
            count = DeletionExecutor(engine, settings).reapply_ledger()
        finally:
            engine.dispose()
    return {"reapplied_deletions": count}


def initialize_ledger():
    """Explicit first-deployment baseline, never an automatic recovery fallback."""
    from sqlalchemy import create_engine, text
    from xueban_backend.config import database_url_from_environment
    path = Path(os.environ["DELETION_LEDGER_PATH"])
    if not path.parent.is_dir() or path.parent.stat().st_mode & 0o077:
        raise ValueError("ledger_parent_not_private")
    if path.exists() or path.is_symlink():
        raise ValueError("ledger_already_exists")
    engine = create_engine(database_url_from_environment())
    try:
        with engine.connect() as connection:
            requests = connection.scalar(text("SELECT count(*) FROM deletion_requests"))
            deleted = connection.scalar(text("SELECT count(*) FROM students WHERE display_name='已删除学员'"))
            if requests or deleted:
                raise ValueError("historical_deletions_require_recovered_ledger")
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as output:
                output.flush(); os.fsync(output.fileno())
    finally:
        engine.dispose()
    return {"ledger_initialized": True, "history_baseline": "current_database_only", "historical_backup_compliance": "unverified"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("pack", "unpack", "verify", "ledger", "replay", "connection", "initialize-ledger"))
    parser.add_argument("--target-database")
    parser.add_argument("--owner-marker")
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(os.environ.get("ASSET_STORAGE_DIR", "/data/assets"))
    if args.operation == "initialize-ledger":
        print(json.dumps(initialize_ledger()))
    elif args.operation == "connection":
        from sqlalchemy.engine import make_url
        from xueban_backend.config import database_url_from_environment
        url = make_url(database_url_from_environment())
        print(json.dumps({"database": url.database, "username": url.username, "host": url.host}))
    elif args.operation == "pack":
        pack(root, json.load(sys.stdin), sys.stdout.buffer)
    elif args.operation == "unpack":
        unpack(root, sys.stdin.buffer)
    elif args.operation == "verify":
        print(json.dumps(verify(root, json.load(sys.stdin))))
    elif args.operation == "ledger":
        path = Path(os.environ["DELETION_LEDGER_PATH"])
        sys.stdout.buffer.write(read_ledger(path))
    else:
        print(json.dumps(replay(args.target_database, sys.stdin.buffer.read(), args.owner_marker)))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("recovery_payload_failed: private data or configuration could not be verified", file=sys.stderr)
        raise SystemExit(1) from None
