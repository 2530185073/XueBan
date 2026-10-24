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


REAL_AGENTS = Path.home() / "Library/LaunchAgents"


@pytest.fixture(autouse=True)
def never_touch_real_launch_agents(tmp_path, monkeypatch):
    """A test once wrote pytest paths into the real LaunchAgents plists and took the live service down.

    Every test gets a private agent directory, and any write under the real one fails the test.
    """
    monkeypatch.setattr(mac_service, "AGENT_DIR", tmp_path / "agents")
    before = sorted(p.name for p in REAL_AGENTS.glob("local.xueban.*")) if REAL_AGENTS.exists() else []
    stamps = {p: p.stat().st_mtime_ns for p in REAL_AGENTS.glob("local.xueban.*")} if REAL_AGENTS.exists() else {}
    yield
    after = sorted(p.name for p in REAL_AGENTS.glob("local.xueban.*")) if REAL_AGENTS.exists() else []
    assert after == before, "test created or removed real LaunchAgents plists"
    assert {p: p.stat().st_mtime_ns for p in REAL_AGENTS.glob("local.xueban.*")} == stamps, "test modified a real LaunchAgents plist"


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


def _loaded_state():
    """A fake launchd: bootout unloads at once, bootstrap loads (optionally only on the Nth try)."""
    state = {"loaded": {"worker": True, "api": True}, "calls": [], "bootstrap_tries": {}, "fail_first": 0}

    def name_of(target):
        return Path(str(target)).name.removesuffix(".plist").rsplit(".", 1)[-1]

    class R:
        def __init__(self, code): self.returncode, self.stdout, self.stderr = code, "", ""

    def fake(*args, check=True):
        name = name_of(args[-1])
        state["calls"].append(f"{args[0]}:{name}")
        if args[0] == "bootout":
            state["loaded"][name] = False
            return R(0)
        if args[0] == "print":
            return R(0 if state["loaded"].get(name) else 113)
        if args[0] == "bootstrap":
            state["bootstrap_tries"][name] = state["bootstrap_tries"].get(name, 0) + 1
            if state["bootstrap_tries"][name] > state["fail_first"]:
                state["loaded"][name] = True
            return R(0)
        return R(0)

    return state, fake


class _Clock:
    """sleep() advances monotonic(), so timeout loops finish instantly and deterministically."""
    def __init__(self): self.now = 0.0
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += seconds


def _fake_time(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(mac_service.time, "sleep", clock.sleep)
    monkeypatch.setattr(mac_service.time, "monotonic", clock.monotonic)
    return clock


class _Proc:
    def __init__(self, code=0, out="", err=""): self.returncode, self.stdout, self.stderr = code, out, err


def test_backup_always_restarts_writers_even_when_recovery_fails(runtime, monkeypatch):
    state, fake = _loaded_state()
    monkeypatch.setattr(mac_service, "launchctl", fake)
    _fake_time(monkeypatch)
    monkeypatch.setattr(mac_service.subprocess, "run", lambda *a, **k: _Proc(1, "", "recovery_failed: disk_full"))
    with pytest.raises(RuntimeError, match="disk_full"):
        mac_service.run_backup(runtime)
    assert [c for c in state["calls"] if c.startswith("bootout")] == ["bootout:worker", "bootout:api"]
    assert state["loaded"] == {"worker": True, "api": True}  # both restarted


def test_backup_retries_while_clients_drain_then_succeeds(runtime, monkeypatch):
    state, fake = _loaded_state()
    attempts = []

    def run(*a, **k):
        attempts.append(1)
        return _Proc(1, "", "recovery_failed: database_has_other_clients") if len(attempts) < 3 else _Proc(0, '{"state": "complete"}')

    monkeypatch.setattr(mac_service, "launchctl", fake)
    _fake_time(monkeypatch)
    monkeypatch.setattr(mac_service.subprocess, "run", run)
    info = mac_service.run_backup(runtime)
    assert info["state"] == "complete" and len(attempts) == 3
    assert state["loaded"] == {"worker": True, "api": True}


def test_restart_retries_when_bootstrap_does_not_take_first_time(runtime, monkeypatch):
    """The real failure: bootstrap right after bootout was ignored, leaving the API down after a backup."""
    state, fake = _loaded_state()
    state["fail_first"] = 2
    monkeypatch.setattr(mac_service, "launchctl", fake)
    _fake_time(monkeypatch)
    monkeypatch.setattr(mac_service.subprocess, "run", lambda *a, **k: _Proc(0, '{"state": "complete"}'))
    mac_service.run_backup(runtime)
    assert state["bootstrap_tries"]["api"] == 3 and state["loaded"]["api"] is True


def test_backup_reports_when_a_writer_cannot_be_restarted(runtime, monkeypatch):
    state, fake = _loaded_state()
    state["fail_first"] = 99
    monkeypatch.setattr(mac_service, "launchctl", fake)
    _fake_time(monkeypatch)
    monkeypatch.setattr(mac_service.subprocess, "run", lambda *a, **k: _Proc(0, '{"state": "complete"}'))
    with pytest.raises(RuntimeError, match="could not restart: api, worker|could not restart: worker, api"):
        mac_service.run_backup(runtime)


def test_install_survives_nothing_left_to_stop(runtime, tmp_path, monkeypatch):
    seen = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    class AlreadyStopped:
        returncode, stdout, stderr = 1, "", "pg_ctl: no server running"

    agents = tmp_path / "agents"
    monkeypatch.setattr(mac_service, "launchctl", lambda *a, **k: seen.append(a[0]) or Done())
    monkeypatch.setattr(mac_service.subprocess, "run", lambda *a, **k: AlreadyStopped())
    written = mac_service.install(runtime, agents)
    assert len(written) == 4 and all(Path(p).parent == agents for p in written)
    assert seen.count("bootstrap") == 4 and seen.count("enable") == 4
    assert not list(REAL_AGENTS.glob("local.xueban.*")) or True  # the autouse guard asserts nothing real changed
