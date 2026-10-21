#!/usr/bin/env python3
"""Private backups and staged restores. No command changes the live deployment.

Host orchestration uses only the standard library. Docker helpers run existing
backend code; local mode uses the backend virtualenv and installed PG clients.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit

INFRA = Path(__file__).resolve().parents[1]
REPO = INFRA.parent
FORMAT = "xueban-recovery-v2"
DB_NAME = re.compile(r"xueban_restore_[a-z0-9_]{8,45}\Z")
FILES = ("database.dump", "assets.tar", "database.json", "deletion-ledger.snapshot.jsonl")


class RecoveryError(Exception):
    """Messages contain fixed operational codes only, never provider stderr."""


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ").lower() + "_" + secrets.token_hex(6)


def hash_file(path):
    h = hashlib.sha256()
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise RecoveryError("not_regular_file")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + "." + secrets.token_hex(4) + ".tmp")
    with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.flush(); os.fsync(out.fileno())
    os.replace(temp, path)


def open_new_private(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb")


def write_private_bytes(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + "." + secrets.token_hex(4) + ".tmp")
    with open_new_private(temporary) as output:
        output.write(data); output.flush(); os.fsync(output.fileno())
    os.replace(temporary, path)


def read_regular(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise RecoveryError("not_regular_file")
        return stream.read()


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def execute(args, *, cwd=None, env=None, data=None, input_file=None, output_file=None):
    result = subprocess.run(args, cwd=cwd, env=env, input=data if input_file is None else None,
                            stdin=input_file, stdout=output_file or subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=600)
    if result.returncode:
        # SQL/CLI errors can contain values or a URL; do not echo command/stderr.
        raise RecoveryError("subprocess_failed_" + str(result.returncode))
    return result.stdout


class Transport:
    def __init__(self, runtime=None):
        self.runtime = Path(runtime).expanduser().resolve() if runtime else None
        self.kind = "local" if self.runtime else "compose"
        if self.runtime:
            config_path = self.runtime / "runtime.json"
            if config_path.stat().st_mode & 0o077:
                raise RecoveryError("runtime_config_not_private")
            cfg = json.loads(read_regular(config_path))
            self.app_env = dict(cfg["environment"])
            raw_url = self.app_env["DATABASE_URL"]
            u = urlsplit(raw_url.replace("postgresql+psycopg://", "postgresql://", 1))
            if u.scheme != "postgresql" or u.hostname not in {"localhost", "127.0.0.1", "::1"} or u.query:
                raise RecoveryError("unsupported_local_database_url")
            self.source_database = unquote(u.path.lstrip("/"))
            self.pg_bin = Path(cfg["pg_bin"])
            self.pg_env = {"PATH": os.environ.get("PATH", ""), "PGHOST": u.hostname,
                           "PGPORT": str(u.port or 5432), "PGUSER": unquote(u.username or ""),
                           "PGPASSWORD": unquote(u.password or ""), "PGAPPNAME": "xueban_recovery"}
        else:
            self.pg_env = None
            connection = json.loads(self.payload("connection"))
            if connection["host"] != "postgres":
                raise RecoveryError("unsupported_compose_database_host")
            self.source_database = connection["database"]
            self.pg_user = connection["username"]

    def pg(self, binary, arguments, *, database=None, **kwargs):
        database = database or self.source_database
        if self.runtime:
            env = self.pg_env | {"PGDATABASE": database}
            return execute([str(self.pg_bin / binary), *arguments], env=env, **kwargs)
        return execute(["docker", "compose", "exec", "-T", "postgres", binary, "-U", self.pg_user, *arguments],
                       cwd=INFRA, env=None, **kwargs)

    def sql(self, query, *, database=None):
        db = database or self.source_database
        # SQL travels on stdin, not the process command line.
        out = self.pg("psql", ["-X", "--set", "ON_ERROR_STOP=1", "-At", "-d", db],
                      database=db, data=query.encode())
        return out.decode().strip()

    def query(self, query, *, database=None):
        return json.loads(self.sql(query, database=database))

    def payload(self, operation, *, asset_target=None, data=None, input_file=None, output_file=None, target_db=None, owner_marker=None):
        args = ["-m", "scripts.recovery_payload", operation]
        if target_db:
            args += ["--target-database", target_db]
        if owner_marker:
            args += ["--owner-marker", owner_marker]
        if self.runtime:
            env = {"PATH": os.environ.get("PATH", ""), **self.app_env, "PYTHONPATH": str(REPO / "services/backend")}
            if asset_target:
                env["ASSET_STORAGE_DIR"] = str(asset_target)
            return execute([sys.executable, *args], env=env, data=data, input_file=input_file, output_file=output_file)
        prefix = ["docker", "compose", "run", "--rm", "--no-deps", "-T"]
        if asset_target:
            prefix += ["--volume", asset_target + ":/data/assets"]
        return execute([*prefix, "--entrypoint", "python", "api", *args], cwd=INFRA,
                       data=data, input_file=input_file, output_file=output_file)

    def quiesced(self):
        if self.runtime:
            for service in ("api", "worker"):
                pidfile = self.runtime / (service + ".pid")
                if pidfile.exists():
                    try:
                        os.kill(int(pidfile.read_text().strip()), 0)
                    except ProcessLookupError:
                        continue
                    raise RecoveryError("local_writers_still_running")
        else:
            running = execute(["docker", "compose", "ps", "--status", "running", "--services"], cwd=INFRA).decode().split()
            if set(running) & {"api", "worker", "migrate"}:
                raise RecoveryError("compose_writers_still_running")
        count = self.query("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend'")
        if count:
            raise RecoveryError("database_has_other_clients")

    def create_assets(self, directory, marker):
        if self.runtime:
            root = Path(directory) / "assets"
            root.mkdir(mode=0o700)
            try:
                write_private_bytes(root / ".recovery-owner", marker.encode())
            except Exception:
                # We created this directory in this call. rmdir never removes
                # somebody else's newly added contents or follows a replacement.
                try:
                    root.rmdir()
                except OSError:
                    pass
                raise
            return str(root)
        name = "xueban_restore_assets_" + marker
        existing = execute(["docker", "volume", "ls", "--filter", "name=^" + name + "$", "--format", "{{.Name}}"])
        if existing.strip():
            raise RecoveryError("asset_volume_exists")
        execute(["docker", "volume", "create", "--label", "xueban.recovery=" + marker, name])
        return name

    def check_assets(self, target, marker, directory):
        if self.runtime:
            root = Path(target)
            if root != Path(directory) / "assets" or root.is_symlink() or read_regular(root / ".recovery-owner").decode() != marker:
                raise RecoveryError("asset_owner_mismatch")
        else:
            if target != "xueban_restore_assets_" + marker:
                raise RecoveryError("asset_owner_mismatch")
            value = json.loads(execute(["docker", "volume", "inspect", target]))
            if value[0].get("Labels", {}).get("xueban.recovery") != marker:
                raise RecoveryError("asset_owner_mismatch")

    def remove_assets(self, target, marker, directory):
        self.check_assets(target, marker, directory)
        if self.runtime:
            shutil.rmtree(target)
        else:
            execute(["docker", "volume", "rm", target])


def database_manifest(transport, database=None):
    tables = transport.query("SELECT coalesce(json_agg(tablename ORDER BY tablename),'[]') FROM pg_tables WHERE schemaname='public'", database=database)
    counts = {name: transport.query("SELECT count(*) FROM public." + identifier(name), database=database) for name in tables}
    revisions = transport.query("SELECT coalesce(json_agg(version_num ORDER BY version_num),'[]') FROM public.alembic_version", database=database)
    assets = transport.query("""SELECT coalesce(json_agg(json_build_object('id',id,'sha256',document->>'sha256',
      'byte_size',(document->>'byte_size')::bigint) ORDER BY id),'[]') FROM public.correction_assets
      WHERE document->>'status' IN ('uploaded','deletion_pending')""", database=database)
    invalid = transport.query("SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='public' AND NOT c.convalidated", database=database)
    if invalid:
        raise RecoveryError("unvalidated_database_constraints")
    return {"table_counts": counts, "migration_revisions": revisions, "assets": assets}


def backup(transport, base, *, writers_quiesced=False):
    if not writers_quiesced:
        raise RecoveryError("backup_requires_explicit_quiesced_writers")
    transport.quiesced()
    transport.payload("ledger")  # Missing current ledger fails before an archive is created.
    directory = Path(base).expanduser().resolve() / stamp()
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        before = database_manifest(transport)
        write_json(directory / "database.json", before)
        with open_new_private(directory / "database.dump") as out:
            transport.pg("pg_dump", ["--format=custom", "--no-owner", "--no-acl", "--schema=public", "-d", transport.source_database], output_file=out)
        with open_new_private(directory / "assets.tar") as out:
            transport.payload("pack", data=json.dumps(before["assets"]).encode(), output_file=out)
        with open_new_private(directory / "deletion-ledger.snapshot.jsonl") as out:
            transport.payload("ledger", output_file=out)
        # Refuse to label an archive consistent if a writer appeared or metadata
        # changed. This supplements, not replaces, the explicit maintenance window.
        transport.quiesced()
        if database_manifest(transport) != before:
            raise RecoveryError("database_changed_during_backup")
        manifest = {"format": FORMAT, "state": "complete", "created_at": datetime.now(timezone.utc).isoformat(),
                    "consistency": "writers_quiesced", "source_database": transport.source_database,
                    "files": {name: hash_file(directory / name) for name in FILES}}
        write_json(directory / "manifest.json", manifest)
        write_private_bytes(directory / "SHA256SUMS", "".join(f"{value}  {name}\n" for name, value in manifest["files"].items()).encode())
        return {"state": "complete", "backup_directory": str(directory), "tables": len(before["table_counts"]), "assets": len(before["assets"])}
    except BaseException:
        write_json(directory / "failure.json", {"state": "incomplete", "usable_for_restore": False})
        raise


def load_backup(directory):
    directory = Path(directory).expanduser().resolve()
    manifest = json.loads(read_regular(directory / "manifest.json"))
    if manifest.get("format") != FORMAT or manifest.get("state") != "complete" or manifest.get("consistency") != "writers_quiesced" or set(manifest.get("files", {})) != set(FILES):
        raise RecoveryError("unsupported_or_incomplete_backup")
    for name in FILES:
        # Refuse symlinks even if their target currently hashes correctly.
        if hash_file(directory / name) != manifest["files"][name]:
            raise RecoveryError("backup_checksum_mismatch")
    return directory, json.loads(read_regular(directory / "database.json"))


def owned_database(transport, state):
    db = state["target_database"]
    if not DB_NAME.fullmatch(db) or db == transport.source_database:
        raise RecoveryError("unsafe_target_database")
    row = transport.query("SELECT coalesce((SELECT json_build_object('oid',oid::bigint,'marker',shobj_description(oid,'pg_database')) FROM pg_database WHERE datname=" + literal(db) + "),'null'::json)")
    if row is None:
        return False
    if row != {"oid": state.get("database_oid"), "marker": "xueban-recovery:" + state["marker"]}:
        raise RecoveryError("database_owner_mismatch")
    return True


def revalidate(transport, directory, *, writers_quiesced=False):
    """Refresh current tombstones on the isolated stage before manual cutover."""
    directory = Path(directory).expanduser().resolve()
    state_path = directory / "stage.json"
    state = json.loads(read_regular(state_path))
    if state.get("format") != FORMAT or state.get("transport") != transport.kind or state.get("state") != "validated_staged":
        raise RecoveryError("stage_not_ready_for_revalidation")
    if not owned_database(transport, state):
        raise RecoveryError("stage_database_missing")
    transport.check_assets(state["asset_target"], state["marker"], directory)
    if transport.query("SELECT count(*) FROM pg_stat_activity WHERE datname=" + literal(state["target_database"]) + " AND backend_type='client backend'"):
        raise RecoveryError("staged_database_in_use")
    if writers_quiesced:
        transport.quiesced()
    latest = transport.payload("ledger")
    replayed = json.loads(transport.payload("replay", target_db=state["target_database"],
                            asset_target=state["asset_target"], data=latest, owner_marker=state["marker"]))
    final = database_manifest(transport, state["target_database"])
    verified = json.loads(transport.payload("verify", asset_target=state["asset_target"], data=json.dumps(final["assets"]).encode()))
    write_private_bytes(directory / "deletion-ledger.latest.jsonl", latest)
    write_json(directory / "verified-database.json", final)
    state.update(verified_after_deletion=verified, deletion_replay=replayed,
                 latest_ledger_sha256=hashlib.sha256(latest).hexdigest(),
                 revalidated_at=datetime.now(timezone.utc).isoformat(),
                 revalidated_with_writers_quiesced=writers_quiesced)
    write_json(state_path, state)
    return {"state": "validated_staged", "stage_directory": str(directory),
            "verified_assets": verified["verified_assets"], "reapplied_deletions": replayed["reapplied_deletions"],
            "automatic_cutover": False, "writers_quiesced": writers_quiesced}


def cleanup(transport, directory):
    directory = Path(directory).expanduser().resolve()
    state_path = directory / "stage.json"
    state = json.loads(read_regular(state_path))
    if state.get("format") != FORMAT or state.get("transport") != transport.kind:
        raise RecoveryError("stage_owner_mismatch")
    # Check every ownership boundary before removing anything.
    exists = owned_database(transport, state) if state.get("database_oid") is not None else False
    if state.get("database_creation_attempted") and state.get("database_oid") is None:
        if transport.query("SELECT count(*) FROM pg_database WHERE datname=" + literal(state["target_database"])):
            raise RecoveryError("database_creation_outcome_uncertain")
    if state.get("asset_target"):
        transport.check_assets(state["asset_target"], state["marker"], directory)
    if exists:
        # No FORCE: an active/restored application blocks deletion instead.
        transport.sql("DROP DATABASE " + identifier(state["target_database"]))
    if state.get("asset_target"):
        transport.remove_assets(state["asset_target"], state["marker"], directory)
        state["asset_target"] = None
    state.update(previous_state=state["state"], state="cleaned", database_oid=None)
    write_json(state_path, state)
    return {"state": "cleaned", "stage_directory": str(directory)}


def restore(transport, backup_directory, base, *, target_database=None):
    archive, expected = load_backup(backup_directory)
    marker = stamp()
    db = target_database or "xueban_restore_" + marker
    if not DB_NAME.fullmatch(db) or db == transport.source_database:
        raise RecoveryError("unsafe_target_database")
    if transport.query("SELECT count(*) FROM pg_database WHERE datname=" + literal(db)):
        raise RecoveryError("target_database_exists")
    transport.payload("ledger")  # Preflight only; fetch again just before replay.
    directory = Path(base).expanduser().resolve() / marker
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    state = {"format": FORMAT, "marker": marker, "transport": transport.kind, "state": "restoring",
             "target_database": db, "database_oid": None, "asset_target": None,
             "backup_directory": str(archive), "automatic_cutover": False}
    write_json(directory / "stage.json", state)
    started = time.monotonic()
    try:
        state["database_creation_attempted"] = True
        write_json(directory / "stage.json", state)
        transport.sql("CREATE DATABASE " + identifier(db) + " TEMPLATE template0")
        oid = transport.query("SELECT oid FROM pg_database WHERE datname=" + literal(db))
        transport.sql("COMMENT ON DATABASE " + identifier(db) + " IS " + literal("xueban-recovery:" + marker))
        state["database_oid"] = oid
        write_json(directory / "stage.json", state)
        state["asset_target"] = transport.create_assets(directory, marker)
        write_json(directory / "stage.json", state)
        with (archive / "database.dump").open("rb") as inp:
            transport.pg("pg_restore", ["--clean", "--if-exists", "--exit-on-error", "--single-transaction", "--no-owner", "--no-acl", "-d", db], database=db, input_file=inp)
        with (archive / "assets.tar").open("rb") as inp:
            transport.payload("unpack", asset_target=state["asset_target"], input_file=inp)
        restored = database_manifest(transport, db)
        if restored != expected:
            raise RecoveryError("restored_database_mismatch")
        verified = json.loads(transport.payload("verify", asset_target=state["asset_target"], data=json.dumps(restored["assets"]).encode()))
        # The backup's old ledger is evidence only. Fetch and replay the live,
        # separately retained ledger into the new database and new asset store.
        latest = transport.payload("ledger")
        write_private_bytes(directory / "deletion-ledger.latest.jsonl", latest)
        replayed = json.loads(transport.payload("replay", target_db=db, asset_target=state["asset_target"], data=latest, owner_marker=state["marker"]))
        final = database_manifest(transport, db)
        final_assets = json.loads(transport.payload("verify", asset_target=state["asset_target"], data=json.dumps(final["assets"]).encode()))
        write_json(directory / "verified-database.json", final)
        state.update(state="validated_staged", restore_completed=True, verified_before_deletion=verified,
                     verified_after_deletion=final_assets, deletion_replay=replayed,
                     latest_ledger_sha256=hashlib.sha256(latest).hexdigest(),
                     migration_revisions=final["migration_revisions"], elapsed_seconds=round(time.monotonic()-started,3),
                     cutover_required=["quiesce all writers", "refresh and replay latest ledger again", "verify matching application image and migration", "explicitly change connection secret and asset mount", "verify account, history, photo and knowledge graph"])
        write_json(directory / "stage.json", state)
        return {"state": state["state"], "stage_directory": str(directory), "target_database": db,
                "asset_target": state["asset_target"], "verified_assets": final_assets["verified_assets"],
                "reapplied_deletions": replayed["reapplied_deletions"], "automatic_cutover": False}
    except BaseException:
        state["state"] = "failed"
        state["restore_completed"] = False
        write_json(directory / "stage.json", state)
        try:
            cleanup(transport, directory)
        except Exception:
            state["cleanup_failed"] = True
            write_json(directory / "stage.json", state)
        raise


def main():
    parser = argparse.ArgumentParser(description="Create private backups or stage a restore; never switches the live deployment")
    parser.add_argument("--runtime", type=Path, help="Private local runtime; omit for Docker Compose")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--writers-quiesced", action="store_true", help="All API, worker, migration and external writers are stopped for the entire backup")
    b.add_argument("--backup-dir", default=os.environ.get("BACKUP_DIR", str(INFRA / "backups")))
    r = sub.add_parser("restore")
    r.add_argument("backup_directory")
    r.add_argument("--restore-dir", default=os.environ.get("RESTORE_DIR", str(INFRA / "restores")))
    r.add_argument("--target-database")
    v = sub.add_parser("revalidate")
    v.add_argument("stage_directory")
    v.add_argument("--writers-quiesced", action="store_true")
    c = sub.add_parser("cleanup")
    c.add_argument("stage_directory")
    args = parser.parse_args()
    os.umask(0o077)
    transport = Transport(args.runtime)
    if args.command == "backup":
        result = backup(transport, args.backup_dir, writers_quiesced=args.writers_quiesced)
    elif args.command == "restore":
        result = restore(transport, args.backup_directory, args.restore_dir, target_database=args.target_database)
    elif args.command == "revalidate":
        result = revalidate(transport, args.stage_directory, writers_quiesced=args.writers_quiesced)
    else:
        result = cleanup(transport, args.stage_directory)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        code = str(exc) if isinstance(exc, RecoveryError) else type(exc).__name__
        print("recovery_failed: " + code, file=sys.stderr)
        raise SystemExit(1) from None
