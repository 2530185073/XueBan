import json
import os
from types import SimpleNamespace

import pytest

from scripts import mac_local, mac_web
from scripts.mac_local import load, run


MODEL_NAMES = {
    "ARK_BASE_URL",
    "ARK_API_KEY",
    "ARK_MODEL",
    "ARK_FALLBACK_MODEL",
    "OPENAI_API_KEY",
    "ZYAI_API_KEY",
}

PRIMARY = {
    "ARK_BASE_URL": "https://model.example.invalid/v1",
    "ARK_API_KEY": "synthetic-primary-key",
    "ARK_MODEL": "deepseek-v4-1-flash-260910",
}
FALLBACK = {
    "ARK_FALLBACK_MODEL": "gpt-6-astra",
}
LEGACY_REVIEW = {
    "ARK_REVIEW_MODEL": "kimi-k3",
    "ARK_REVIEW_BASE_URL": "https://review.example.invalid/v1",
    "ARK_REVIEW_API_KEY": "synthetic-review-key",
    "ARK_REVIEW_PROTOCOL": "chat_completions",
    "REVIEW_PROTOCOL": "responses",
}


@pytest.fixture(autouse=True)
def clear_inherited_model_environment(monkeypatch):
    for name in list(os.environ):
        if name in MODEL_NAMES or name.startswith(("ARK_", "OPENAI_", "ZYAI_")):
            monkeypatch.delenv(name, raising=False)


def private_file(path, content):
    path.write_text(content)
    path.chmod(0o600)
    return path


def model_file(tmp_path, values=None, extra=()):
    values = PRIMARY | (FALLBACK if values is None else values)
    lines = [f"{name}={value}" for name, value in values.items()]
    lines.extend(extra)
    return private_file(tmp_path / "model.env", "\n".join(lines) + "\n")


def runtime_config(tmp_path, model_config, environment=None):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    private_file(runtime / "runtime.json", json.dumps({
        "model_config": str(model_config),
        "host": "127.0.0.1",
        "api_port": 8000,
        "environment": {
            "PUBLIC_BASE_URL": "http://127.0.0.1:8000",
            **(environment or {}),
        },
    }))
    return runtime


def assert_no_model_environment(environment):
    assert not any(name in MODEL_NAMES or name.startswith(("ARK_", "OPENAI_", "ZYAI_"))
                    for name in environment)


def test_worker_load_reads_primary_and_fallback_only_without_mutating_parent(monkeypatch, tmp_path):
    model_config = model_file(tmp_path, PRIMARY | FALLBACK | LEGACY_REVIEW)
    runtime = runtime_config(tmp_path, model_config)
    monkeypatch.setenv("ARK_REVIEW_MODEL", "inherited-value")
    monkeypatch.setenv("REVIEW_PROTOCOL", "inherited-protocol")
    parent_before = os.environ["ARK_REVIEW_MODEL"]

    _, env = load(runtime, require_model=True)

    assert {name: env[name] for name in PRIMARY} == PRIMARY
    assert {name: env[name] for name in FALLBACK} == FALLBACK
    assert not any(name in env for name in LEGACY_REVIEW)
    assert os.environ["ARK_REVIEW_MODEL"] == parent_before
    assert os.environ["REVIEW_PROTOCOL"] == "inherited-protocol"


@pytest.mark.parametrize("fallback_value", [None, ""])
def test_fallback_default_stays_empty_and_optional_inherited_values_are_removed(monkeypatch, tmp_path, fallback_value):
    values = dict(PRIMARY)
    if fallback_value is not None:
        values["ARK_FALLBACK_MODEL"] = fallback_value
    model_config = model_file(tmp_path, values)
    runtime = runtime_config(tmp_path, model_config)
    monkeypatch.setenv("ARK_FALLBACK_MODEL", "inherited-fallback")
    monkeypatch.setenv("ARK_REVIEW_MODEL", "inherited-review")
    monkeypatch.setenv("REVIEW_PROTOCOL", "inherited-protocol")

    _, env = load(runtime, require_model=True)

    assert env.get("ARK_FALLBACK_MODEL", "") == ""
    assert env.get("ARK_REVIEW_MODEL", "") == ""
    assert env.get("REVIEW_PROTOCOL", "") == ""


def test_model_file_allowlist_does_not_override_service_environment(tmp_path):
    model_config = model_file(tmp_path, extra=[
        "DATABASE_URL=should-not-win",
        "ARK_UNKNOWN_KEY=should-not-load",
    ])
    runtime = runtime_config(tmp_path, model_config, {"DATABASE_URL": "sqlite:///service.db"})

    _, env = load(runtime, require_model=True)

    assert env["DATABASE_URL"] == "sqlite:///service.db"
    assert "ARK_UNKNOWN_KEY" not in env


def test_non_worker_load_strips_model_environment_from_parent_and_runtime(monkeypatch, tmp_path):
    model_config = model_file(tmp_path)
    runtime = runtime_config(tmp_path, model_config, {
        "ARK_API_KEY": "runtime-secret",
        "OPENAI_API_KEY": "runtime-secret",
    })
    monkeypatch.setenv("ARK_API_KEY", "parent-secret")
    monkeypatch.setenv("ZYAI_API_KEY", "parent-secret")

    _, env = load(runtime)

    assert_no_model_environment(env)
    assert env["PUBLIC_BASE_URL"] == "http://127.0.0.1:8000"


def test_worker_requires_primary_url_and_key(tmp_path):
    model_config = model_file(tmp_path, {"ARK_BASE_URL": "", "ARK_API_KEY": ""})
    runtime = runtime_config(tmp_path, model_config)

    with pytest.raises(ValueError, match="Model configuration is incomplete"):
        load(runtime, require_model=True)


def test_model_file_must_remain_private(tmp_path):
    model_config = model_file(tmp_path)
    model_config.chmod(0o644)
    runtime = runtime_config(tmp_path, model_config)

    with pytest.raises(ValueError, match="private regular file"):
        load(runtime, require_model=True)


def test_local_start_routes_primary_and_fallback_only_to_worker(monkeypatch, tmp_path):
    model_config = model_file(tmp_path, PRIMARY | FALLBACK | LEGACY_REVIEW)
    runtime = runtime_config(tmp_path, model_config, {"DATABASE_URL": "sqlite:///service.db"})
    subprocess_calls = []
    spawn_calls = []

    monkeypatch.setattr(mac_local.subprocess, "run", lambda args, **kwargs: (
        subprocess_calls.append((args, kwargs)) or SimpleNamespace(returncode=0)
    ))
    monkeypatch.setattr(mac_local, "start_postgres", lambda runtime, config: None)
    monkeypatch.setattr(mac_local, "spawn", lambda runtime, name, args, env: spawn_calls.append((name, env)))
    monkeypatch.setattr(mac_local, "wait_ready", lambda runtime, config: None)
    monkeypatch.setattr(mac_local.sys, "argv", ["mac_local.py", "--runtime", str(runtime), "start"])

    mac_local.main()

    assert len(subprocess_calls) == 1
    assert_no_model_environment(subprocess_calls[0][1]["env"])
    assert [name for name, _ in spawn_calls] == ["api", "worker"]
    assert_no_model_environment(spawn_calls[0][1])
    assert spawn_calls[0][1]["DATABASE_URL"] == "sqlite:///service.db"
    assert spawn_calls[1][1]["ARK_MODEL"] == PRIMARY["ARK_MODEL"]
    assert spawn_calls[1][1]["ARK_FALLBACK_MODEL"] == FALLBACK["ARK_FALLBACK_MODEL"]
    assert not any(name in spawn_calls[1][1] for name in LEGACY_REVIEW)


def test_run_strips_model_environment_from_default_and_explicit_env(monkeypatch):
    calls = []
    monkeypatch.setenv("ARK_API_KEY", "parent-secret")
    monkeypatch.setattr(mac_local.subprocess, "run", lambda args, **kwargs: (
        calls.append(kwargs) or SimpleNamespace(returncode=0)
    ))
    explicit = {"ARK_API_KEY": "explicit-secret", "PUBLIC_BASE_URL": "http://127.0.0.1:8000"}

    run(["synthetic-command"], env=explicit)
    run(["synthetic-command"])

    assert_no_model_environment(calls[0]["env"])
    assert calls[0]["env"]["PUBLIC_BASE_URL"] == "http://127.0.0.1:8000"
    assert_no_model_environment(calls[1]["env"])


def test_web_restart_api_strips_models(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    captured = []
    config = {"host": "127.0.0.1", "api_port": 8000}
    env = {"DATABASE_URL": "sqlite:///service.db", **PRIMARY, **FALLBACK, **LEGACY_REVIEW}
    monkeypatch.setattr(mac_web, "spawn", lambda runtime, name, args, env: captured.append((name, env)))
    monkeypatch.setattr(mac_web, "wait_ready", lambda runtime, config: None)

    mac_web.restart_api(runtime, config, env)

    assert captured[0][0] == "api"
    assert_no_model_environment(captured[0][1])
    assert captured[0][1]["DATABASE_URL"] == "sqlite:///service.db"


def test_web_launcher_strips_models_from_build_and_backend_start(monkeypatch, tmp_path):
    web = tmp_path / "apps/student-web"
    (web / "node_modules").mkdir(parents=True)
    model_config = model_file(tmp_path)
    runtime = runtime_config(tmp_path, model_config)
    calls = []
    monkeypatch.setattr(mac_web, "REPO", tmp_path)
    monkeypatch.setattr(mac_web, "ready", lambda origin: True)
    monkeypatch.setattr(mac_web.subprocess, "run", lambda args, **kwargs: (
        calls.append((args, kwargs)) or SimpleNamespace(returncode=0)
    ))
    monkeypatch.setenv("ARK_API_KEY", "parent-secret")
    monkeypatch.setenv("DATABASE_URL", "parent-database")
    monkeypatch.setattr(mac_web.sys, "argv", ["mac_web.py", "--runtime", str(runtime), "--no-open"])

    mac_web.main()

    assert [call[0][:2] for call in calls] == [["npm", "run"], [mac_web.sys.executable, str(mac_web.BACKEND / "scripts/mac_local.py")]]
    build_env = calls[0][1]["env"]
    backend_env = calls[1][1]["env"]
    assert_no_model_environment(build_env)
    assert_no_model_environment(backend_env)
    assert "DATABASE_URL" not in build_env
    assert backend_env["DATABASE_URL"] == "parent-database"
