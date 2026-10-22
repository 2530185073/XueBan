"""Safety boundaries for isolated recovery, without touching any live service."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile

import pytest

from scripts import recovery_payload as assets

SPEC = importlib.util.spec_from_file_location("xueban_recovery_ops", Path(__file__).resolve().parents[3] / "infra/scripts/recovery.py")
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)


def item(name="asset_abc", data=b"original"):
    return {"id": name, "byte_size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def archive(entries):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        for name, kind, content in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(content)
            member.linkname = "../../outside"
            tar.addfile(member, io.BytesIO(content) if kind == tarfile.REGTYPE else None)
    out.seek(0)
    return out


def test_asset_round_trip_uses_db_manifest_and_private_files(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    (source / "asset_abc").write_bytes(b"original")
    (source / "asset_unreferenced").write_bytes(b"not retained")
    data = io.BytesIO()
    assets.pack(source, [item()], data)
    restored = tmp_path / "new"; data.seek(0)
    assets.unpack(restored, data)
    assert assets.verify(restored, [item()]) == {"verified_assets": 1, "verified_bytes": 8}
    assert (restored / "asset_abc").stat().st_mode & 0o777 == 0o600
    assert (source / "asset_unreferenced").read_bytes() == b"not retained"


@pytest.mark.parametrize("name,kind", [
    ("../../asset_escape", tarfile.REGTYPE), ("/asset_abs", tarfile.REGTYPE),
    ("asset_symlink", tarfile.SYMTYPE), ("asset_hardlink", tarfile.LNKTYPE),
    ("asset_fifo", tarfile.FIFOTYPE), ("assets/asset_nested", tarfile.REGTYPE),
])
def test_archive_rejects_path_traversal_and_nonregular_members(tmp_path, name, kind):
    with pytest.raises(ValueError, match="unsafe_asset_archive"):
        assets.unpack(tmp_path / "isolated", archive([(name, kind, b"secret")]))
    assert not (tmp_path / "outside").exists()


def test_archive_never_overwrites_existing_asset(tmp_path):
    (tmp_path / "asset_abc").write_bytes(b"retained")
    with pytest.raises(FileExistsError):
        assets.unpack(tmp_path, archive([("asset_abc", tarfile.REGTYPE, b"changed")]))
    assert (tmp_path / "asset_abc").read_bytes() == b"retained"


def test_duplicate_archive_asset_rejected(tmp_path):
    with pytest.raises(ValueError, match="unsafe_asset_archive"):
        assets.unpack(tmp_path, archive([("asset_abc", tarfile.REGTYPE, b"one"), ("asset_abc", tarfile.REGTYPE, b"two")]))


def test_pack_rejects_mutated_or_symlinked_original(tmp_path):
    (tmp_path / "asset_abc").write_bytes(b"wrong")
    with pytest.raises(ValueError, match="asset_integrity"):
        assets.pack(tmp_path, [item()], io.BytesIO())
    (tmp_path / "asset_abc").unlink()
    (tmp_path / "real").write_bytes(b"original")
    (tmp_path / "asset_abc").symlink_to(tmp_path / "real")
    with pytest.raises(OSError):
        assets.pack(tmp_path, [item()], io.BytesIO())


def test_verify_rejects_unreferenced_file(tmp_path):
    (tmp_path / "asset_extra").write_bytes(b"extra")
    with pytest.raises(ValueError, match="asset_set_mismatch"):
        assets.verify(tmp_path, [])


def test_ledger_rejects_asset_paths_before_purge():
    record = {"deletion_id": "d", "tenant_id": "t", "student_id": "s", "asset_ids": ["../../secret"]}
    with pytest.raises(ValueError, match="invalid_deletion_ledger"):
        assets.validate_ledger(json.dumps(record).encode())


class FakeTransport:
    kind = "local"
    source_database = "live_database"

    def __init__(self):
        self.calls = []
        self.databases = {}
        self.fail_restore = False
        self.fail_replay = False
        self.current_ledger = b''
        self.manifest = {"table_counts": {"students": 1}, "migration_revisions": ["head"], "assets": []}

    def quiesced(self):
        self.calls.append("quiesced")

    def sql(self, query, database=None):
        self.calls.append(query)
        if query.startswith("CREATE DATABASE"):
            name = query.split('"')[1]
            self.databases[name] = {"oid": 123, "marker": None}
        elif query.startswith("COMMENT ON DATABASE"):
            name = query.split('"')[1]
            self.databases[name]["marker"] = query.split(" IS '")[1][:-1]
        elif query.startswith("DROP DATABASE"):
            del self.databases[query.split('"')[1]]
        return ""

    def query(self, query, database=None):
        self.calls.append(query)
        if "pg_stat_activity" in query:
            return 0
        if "SELECT count(*) FROM pg_database" in query:
            return int(query.split("datname='")[1].split("'")[0] in self.databases)
        if "SELECT oid FROM pg_database" in query:
            return 123
        if "shobj_description" in query:
            return self.databases.get(query.split("datname='")[1].split("'")[0])
        raise AssertionError(query)

    def pg(self, command, arguments, output_file=None, input_file=None, database=None):
        self.calls.append((command, arguments, database))
        if command == "pg_dump":
            output_file.write(b"fake dump")
        elif self.fail_restore:
            raise ops.RecoveryError("simulated_restore_failure")

    def payload(self, operation, asset_target=None, data=None, input_file=None, output_file=None, target_db=None, owner_marker=None):
        self.calls.append((operation, target_db, asset_target))
        if operation == "pack":
            assets.pack(Path("."), [], output_file)
        elif operation == "unpack":
            assets.unpack(Path(asset_target), input_file)
        elif operation == "verify":
            return json.dumps(assets.verify(Path(asset_target), [])).encode()
        elif operation == "ledger":
            if output_file:
                output_file.write(self.current_ledger)
            else:
                return self.current_ledger
        elif operation == "replay":
            assert data == self.current_ledger
            if self.fail_replay:
                raise ops.RecoveryError("simulated_replay_failure")
            return b'{"reapplied_deletions":0}'

    create_assets = ops.Transport.create_assets
    check_assets = ops.Transport.check_assets
    remove_assets = ops.Transport.remove_assets
    runtime = True


@pytest.fixture
def fake(monkeypatch):
    transport = FakeTransport()
    monkeypatch.setattr(ops, "database_manifest", lambda t, database=None: t.manifest)
    return transport


def test_backup_refuses_live_default_without_side_effect(fake, tmp_path):
    with pytest.raises(ops.RecoveryError, match="explicit_quiesced"):
        ops.backup(fake, tmp_path)
    assert not list(tmp_path.iterdir())
    assert not fake.calls


def test_restore_never_changes_source_or_existing_assets(fake, tmp_path):
    saved = ops.backup(fake, tmp_path / "backups", writers_quiesced=True)
    restored = ops.restore(fake, saved["backup_directory"], tmp_path / "restore")
    assert restored["state"] == "validated_staged"
    assert restored["automatic_cutover"] is False
    assert restored["target_database"] != fake.source_database
    assert not any("DROP DATABASE" in x for x in fake.calls if isinstance(x, str))
    ops.cleanup(fake, restored["stage_directory"])
    assert not fake.databases
    assert not Path(restored["asset_target"]).exists()


def test_restore_refuses_existing_database_before_creating_assets(fake, tmp_path):
    saved = ops.backup(fake, tmp_path / "backup", writers_quiesced=True)
    fake.databases["xueban_restore_existing123"] = {"oid": 55, "marker": "other"}
    with pytest.raises(ops.RecoveryError, match="target_database_exists"):
        ops.restore(fake, saved["backup_directory"], tmp_path / "new", target_database="xueban_restore_existing123")
    assert fake.databases["xueban_restore_existing123"]["oid"] == 55
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("failure", ["fail_restore", "fail_replay"])
def test_failed_restore_cleans_only_its_new_objects(fake, tmp_path, failure):
    saved = ops.backup(fake, tmp_path / "backup", writers_quiesced=True)
    setattr(fake, failure, True)
    with pytest.raises(ops.RecoveryError):
        ops.restore(fake, saved["backup_directory"], tmp_path / "new")
    assert not fake.databases
    state = json.loads(next((tmp_path / "new").glob("*/stage.json")).read_text())
    assert state["state"] == "cleaned"
    assert not list((tmp_path / "new").glob("*/assets"))


def test_checksum_failure_happens_before_database_creation(fake, tmp_path):
    saved = ops.backup(fake, tmp_path / "backup", writers_quiesced=True)
    Path(saved["backup_directory"], "database.dump").write_bytes(b"corrupt")
    fake.calls.clear()
    with pytest.raises(ops.RecoveryError, match="checksum"):
        ops.restore(fake, saved["backup_directory"], tmp_path / "new")
    assert not fake.calls


def test_cleanup_rejects_replaced_database_identity(fake, tmp_path):
    saved = ops.backup(fake, tmp_path / "backup", writers_quiesced=True)
    restored = ops.restore(fake, saved["backup_directory"], tmp_path / "new")
    fake.databases[restored["target_database"]]["oid"] = 124
    with pytest.raises(ops.RecoveryError, match="database_owner_mismatch"):
        ops.cleanup(fake, restored["stage_directory"])
    assert Path(restored["asset_target"]).exists()


def test_live_runtime_pid_blocks_backup(tmp_path):
    transport = object.__new__(ops.Transport)
    transport.runtime = tmp_path
    (tmp_path / "api.pid").write_text(str(os.getpid()))
    with pytest.raises(ops.RecoveryError, match="writers_still_running"):
        transport.quiesced()


def test_latest_ledger_must_exist_even_when_parent_exists(tmp_path):
    with pytest.raises(FileNotFoundError):
        assets.read_ledger(tmp_path / "deletions.jsonl")


def test_latest_ledger_rejects_symlink_and_public_file(tmp_path):
    ledger = tmp_path / "deletions.jsonl"
    ledger.write_bytes(b""); ledger.chmod(0o644)
    with pytest.raises(ValueError, match="not_private_regular"):
        assets.read_ledger(ledger)
    ledger.chmod(0o600)
    link = tmp_path / "link.jsonl"; link.symlink_to(ledger)
    with pytest.raises(OSError):
        assets.read_ledger(link)
    assert assets.read_ledger(ledger) == b""


def test_tampered_asset_owner_blocks_database_cleanup_too(fake, tmp_path):
    saved = ops.backup(fake, tmp_path / "backup", writers_quiesced=True)
    staged = ops.restore(fake, saved["backup_directory"], tmp_path / "new")
    (Path(staged["asset_target"]) / ".recovery-owner").write_text("different owner")
    with pytest.raises(ops.RecoveryError, match="asset_owner_mismatch"):
        ops.cleanup(fake, staged["stage_directory"])
    assert staged["target_database"] in fake.databases


def test_revalidation_replays_latest_ledger_without_cutover(fake, tmp_path):
    saved = ops.backup(fake, tmp_path / "backup", writers_quiesced=True)
    staged = ops.restore(fake, saved["backup_directory"], tmp_path / "new")
    fake.current_ledger = b'{"deletion_id":"new","tenant_id":"t","student_id":"s","asset_ids":[]}\n'
    result = ops.revalidate(fake, staged["stage_directory"], writers_quiesced=True)
    state = json.loads((Path(staged["stage_directory"]) / "stage.json").read_text())
    assert result["automatic_cutover"] is False
    assert state["latest_ledger_sha256"] == hashlib.sha256(fake.current_ledger).hexdigest()
    assert state["revalidated_with_writers_quiesced"] is True


def test_database_identity_query_has_stable_numeric_oid(fake, tmp_path):
    saved = ops.backup(fake, tmp_path / "backup", writers_quiesced=True)
    staged = ops.restore(fake, saved["backup_directory"], tmp_path / "new")
    ops.cleanup(fake, staged["stage_directory"])
    queries = [x for x in fake.calls if isinstance(x, str) and "shobj_description" in x]
    assert queries and all("oid::bigint" in q for q in queries)


def test_backup_artifacts_are_private_without_relying_on_shell_umask(fake, tmp_path):
    saved = ops.backup(fake, tmp_path / "backup", writers_quiesced=True)
    for path in Path(saved["backup_directory"]).iterdir():
        assert path.stat().st_mode & 0o777 == 0o600


def test_explicit_fresh_ledger_initialization_has_no_historical_claim(database, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database.url)
    monkeypatch.delenv("DATABASE_URL_FILE", raising=False)
    parent = tmp_path / "ledger"; parent.mkdir(mode=0o700)
    path = parent / "deletions.jsonl"
    monkeypatch.setenv("DELETION_LEDGER_PATH", str(path))
    result = assets.initialize_ledger()
    assert result["historical_backup_compliance"] == "unverified"
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_bytes(b"retained")
    with pytest.raises(ValueError, match="already_exists"):
        assets.initialize_ledger()
    assert path.read_bytes() == b"retained"


def test_explicit_fresh_ledger_refuses_deleted_students(database, tmp_path, monkeypatch):
    from sqlalchemy import text
    with database.engine.begin() as connection:
        connection.execute(text("UPDATE students SET display_name='已删除学员' WHERE id='student_a'"))
    monkeypatch.setenv("DATABASE_URL", database.url)
    monkeypatch.delenv("DATABASE_URL_FILE", raising=False)
    parent = tmp_path / "ledger"; parent.mkdir(mode=0o700)
    path = parent / "deletions.jsonl"
    monkeypatch.setenv("DELETION_LEDGER_PATH", str(path))
    with pytest.raises(ValueError, match="historical_deletions"):
        assets.initialize_ledger()
    assert not path.exists()


def test_unknown_database_creation_outcome_is_not_reported_as_cleaned(fake, tmp_path, monkeypatch):
    saved = ops.backup(fake, tmp_path / "backup", writers_quiesced=True)
    original_sql = fake.sql
    def disconnected(query, database=None):
        value = original_sql(query, database)
        if query.startswith("CREATE DATABASE"):
            raise ops.RecoveryError("connection_lost_after_creation")
        return value
    monkeypatch.setattr(fake, "sql", disconnected)
    with pytest.raises(ops.RecoveryError):
        ops.restore(fake, saved["backup_directory"], tmp_path / "restore")
    state = json.loads(next((tmp_path / "restore").glob("*/stage.json")).read_text())
    assert state["cleanup_failed"] is True
    assert state["state"] == "failed"
    assert state["target_database"] in fake.databases
