"""launchd supervision keeps secrets out of plists and out of the API, and backups safe."""
import json
import os
import plistlib
import time
from pathlib import Path

import pytest

from scripts import mac_service
from scripts.mac_local import configure_models, write_private

SECRETS = ("synthetic-ark-secret", "synthetic-gateway-secret")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for key in ("ARK_API_KEY", "ARK_BASE_URL", "ZYAI_API_KEY", "ZYAI_BASE_URL", "CORRECTION_MODE"):
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / "runtime"
    (root / "logs").mkdir(parents=True)
    ark, gateway = tmp_path / "ark.env", tmp_path / "gateway.env"
    write_private(ark, "ARK_BASE_URL=https://ark.example.invalid/api/v3\nARK_API_KEY=synthetic-ark-secret\n")
    write_private(gateway, "ZYAI_BASE_URL=https://gateway.example.invalid/v1\nZYAI_API_KEY=synthetic-gateway-secret\n")
    config = {"model_config": str(ark), "host": "127.0.0.1", "api_port": 8000, "pg_bin": "/opt/pg/bin", "pg_port": 55432,
              "environment": {"DATABASE_URL": "postgresql+psycopg://u:p@127.0.0.1:55432/d", "WORKER_LEASE_SECONDS": "300"}}
    write_private(root / "runtime.json", json.dumps(config))
    configure_models(root, mode="two_model", gateway_config=gateway)
    return root


def test_plists_hold_no_secret_and_no_configuration(runtime, tmp_path):
    written = mac_service.write_plists(runtime, tmp_path / "agents")
    assert len(written) == 4
    for path in written:
        raw = Path(path).read_bytes()
        assert not any(secret.encode() in raw for secret in SECRETS)
        assert b"postgresql+psycopg" not in raw and b"DATABASE_URL" not in raw
        assert oct(Path(path).stat().st_mode & 0o777) == "0o600"
        plist = plistlib.loads(raw)
        if plist["Label"].endswith(".backup"):
            assert "KeepAlive" not in plist and plist["StartCalendarInterval"] == {"Hour": 3, "Minute": 17}
            assert plist["ProgramArguments"][-1] == "backup"
            continue
        assert plist["KeepAlive"] is True and plist["RunAtLoad"] is True and plist["ThrottleInterval"] >= 10
        assert plist["ProgramArguments"][2:4] == ["--runtime", str(runtime)] and plist["ProgramArguments"][-2] == "exec"
        assert set(plist["EnvironmentVariables"]) == {"PATH", "LC_ALL", "LANG"}


def test_only_the_worker_gets_model_credentials(runtime):
    api, worker, postgres = (mac_service.process_environment(runtime, n) for n in ("api", "worker", "postgres"))
    assert worker["ARK_API_KEY"] == "synthetic-ark-secret" and worker["ZYAI_API_KEY"] == "synthetic-gateway-secret"
    assert not any(k.startswith(("ARK_", "ZYAI_", "OPENAI_")) for k in api)
    assert "DATABASE_URL" in api and "DATABASE_URL" in worker
    assert "DATABASE_URL" not in postgres and set(postgres) == {"PATH", "HOME", "LC_ALL", "LANG"}
    assert api["LC_ALL"] == worker["LC_ALL"] == postgres["LC_ALL"]
    assert api["PATH"] == worker["PATH"] == mac_service.BASE_PATH


def test_programs_are_exec_form_and_loopback_only(runtime):
    config = json.loads((runtime / "runtime.json").read_text())
    programs = mac_service.build_programs(runtime, config)
    assert programs["postgres"][0] == "/opt/pg/bin/postgres" and "-h" in programs["postgres"]
    assert programs["postgres"][programs["postgres"].index("-h") + 1] == "127.0.0.1"
    assert programs["api"][programs["api"].index("--host") + 1] == "127.0.0.1"
    assert "xueban_backend.worker" in programs["worker"]
    assert all(isinstance(part, str) for program in programs.values() for part in program)


def test_exec_agent_replaces_process_and_records_pid(runtime, monkeypatch):
    captured = {}

    def fake_execve(path, argv, env):
        captured.update(path=path, argv=argv, env=env)
        raise SystemExit(0)

    monkeypatch.setattr(os, "execve", fake_execve)
    monkeypatch.setattr(os, "chdir", lambda path: None)
    with pytest.raises(SystemExit):
        mac_service.exec_agent(runtime, "worker")
    assert captured["env"]["ARK_API_KEY"] == "synthetic-ark-secret"
    assert (runtime / "worker.pid").read_text() == str(os.getpid())
    assert oct((runtime / "worker.pid").stat().st_mode & 0o777) == "0o600"
    with pytest.raises(SystemExit):
        mac_service.exec_agent(runtime, "api")
    assert "ARK_API_KEY" not in captured["env"] and "ZYAI_API_KEY" not in captured["env"]


def _backup(root, name, age_hours, complete=True):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "database.dump").write_bytes(b"x")
    if complete:
        manifest = directory / "manifest.json"
        manifest.write_text("{}")
        stamp = time.time() - age_hours * 3600
        os.utime(manifest, (stamp, stamp))
    return directory


def test_prune_keeps_newest_and_never_removes_incomplete_or_foreign_files(tmp_path):
    root = tmp_path / "scheduled-backups"
    root.mkdir()
    for i in range(5):
        _backup(root, f"b{i}", age_hours=50 - i * 10)
    partial = _backup(root, "partial", 1, complete=False)
    (root / "notes.txt").write_text("keep")
    removed = mac_service.prune(root, keep=2)
    assert sorted(removed) == ["b0", "b1", "b2"]
    assert (root / "b3").exists() and (root / "b4").exists() and partial.exists() and (root / "notes.txt").exists()
    assert mac_service.prune(root, keep=0) == []


def test_status_reports_missing_backup_and_stale_backup(runtime, monkeypatch):
    class Printed:  # launchctl says the agent is not loaded
        returncode, stdout = 113, ""

    monkeypatch.setattr(mac_service, "launchctl", lambda *a, **k: Printed())
    monkeypatch.setattr(mac_service, "_http_status", lambda url: 200)
    report = mac_service.status(runtime)
    assert report["ok"] is False and report["ready"] is True
    assert any("no scheduled backup" in p for p in report["problems"])
    assert all(not v["running"] for v in report["agents"].values())
    _backup(runtime / "scheduled-backups", "old", age_hours=40)
    stale = mac_service.status(runtime)
    assert any("older than 30 hours" in p for p in stale["problems"]) and stale["last_backup"]["directory"] == "old"
    _backup(runtime / "scheduled-backups", "fresh", age_hours=1)
    fresh = mac_service.status(runtime)
    assert fresh["last_backup"]["directory"] == "fresh" and not any("backup" in p for p in fresh["problems"])


def test_backup_always_restarts_writers_even_when_recovery_fails(runtime, monkeypatch):
    calls = []

    class Done:
        returncode, stdout = 0, ""

    def fake_launchctl(*args, check=True):
        # bootout takes "gui/<uid>/<label>"; bootstrap takes the domain and then the plist path.
        target = args[-1]
        calls.append(args[0] + ":" + Path(target).name.removesuffix(".plist").rsplit(".", 1)[-1].rsplit("/", 1)[-1])
        return Done()

    class Failed:
        returncode, stdout, stderr = 1, "", "recovery_failed: database_has_other_clients"

    monkeypatch.setattr(mac_service, "launchctl", fake_launchctl)
    monkeypatch.setattr(mac_service.subprocess, "run", lambda *a, **k: Failed())
    with pytest.raises(RuntimeError, match="database_has_other_clients"):
        mac_service.run_backup(runtime)
    assert calls[:2] == ["bootout:worker", "bootout:api"]
    assert calls[2:] == ["bootstrap:api", "bootstrap:worker"]


def test_install_survives_nothing_left_to_stop(runtime, tmp_path, monkeypatch):
    seen = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    class AlreadyStopped:
        returncode, stdout, stderr = 1, "", "pg_ctl: no server running"

    monkeypatch.setattr(mac_service, "AGENT_DIR", tmp_path / "agents")
    monkeypatch.setattr(mac_service, "plist_path", lambda name, agent_dir=tmp_path / "agents": agent_dir / f"{mac_service.label(name)}.plist")
    monkeypatch.setattr(mac_service, "launchctl", lambda *a, **k: seen.append(a[0]) or Done())
    monkeypatch.setattr(mac_service.subprocess, "run", lambda *a, **k: AlreadyStopped())
    written = mac_service.install(runtime)
    assert len(written) == 4 and seen.count("bootstrap") == 4 and seen.count("enable") == 4
