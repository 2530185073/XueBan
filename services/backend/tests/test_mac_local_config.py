"""Private local configuration stays outside browser/API and preserves existing data settings."""
import json
import os
from pathlib import Path

import pytest

from scripts.mac_local import api_environment, configure_models, load, write_private


@pytest.fixture
def local_config(tmp_path, monkeypatch):
    for key in ("ARK_API_KEY", "ARK_BASE_URL", "ZYAI_API_KEY", "ZYAI_BASE_URL", "CORRECTION_MODE"):
        monkeypatch.delenv(key, raising=False)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    ark = tmp_path / "ark.env"
    gateway = tmp_path / "gateway.env"
    write_private(ark, "ARK_BASE_URL=https://ark.example.invalid/api/v3\nARK_API_KEY=synthetic-ark-secret\n")
    write_private(gateway, "ZYAI_BASE_URL=https://gateway.example.invalid/v1\nZYAI_API_KEY=synthetic-gateway-secret\n")
    config = {"model_config": str(ark), "host": "127.0.0.1", "api_port": 8000,
              "environment": {"DATABASE_URL": "unchanged-private-database", "WORKER_LEASE_SECONDS": "300"}}
    write_private(runtime / "runtime.json", json.dumps(config))
    return runtime, ark, gateway


@pytest.mark.parametrize("mode", ["multimodel", "two_model"])
def test_enable_loads_worker_credentials_only_on_explicit_model_load(local_config, mode):
    runtime, _, gateway = local_config
    configure_models(runtime, mode=mode, gateway_config=gateway)
    config, no_models = load(runtime)
    assert config["environment"]["DATABASE_URL"] == "unchanged-private-database"
    assert config["environment"]["WORKER_LEASE_SECONDS"] == "600"
    assert "ZYAI_API_KEY" not in no_models and "ARK_API_KEY" not in no_models
    _, worker = load(runtime, require_model=True)
    assert worker["ZYAI_API_KEY"] == "synthetic-gateway-secret"
    assert worker["ARK_API_KEY"] == "synthetic-ark-secret"
    assert worker["READBACK_MODEL"] == "kimi-k3"
    assert worker["REVIEW_API"] == "responses"
    assert worker["REVIEW_THINKING"] == "enabled"
    assert worker["RECONCILIATION_PROVIDER"] == "primary"
    assert worker["VISUAL_REVIEW_MODEL"] == "gpt-6-astra"
    assert worker["VISUAL_REVIEW_ENABLED"] == "1"
    assert worker["CORRECTION_MODE"] == mode
    assert not any(k.startswith(("ARK_", "ZYAI_", "OPENAI_")) for k in api_environment(worker))
    assert (runtime / "runtime.json").stat().st_mode & 0o077 == 0
    assert "synthetic-gateway-secret" not in (runtime / "runtime.json").read_text()


def test_missing_gateway_blocks_enable_without_altering_runtime(local_config):
    runtime, _, _ = local_config
    before = (runtime / "runtime.json").read_bytes()
    with pytest.raises(ValueError):
        configure_models(runtime, mode="multimodel")
    assert (runtime / "runtime.json").read_bytes() == before


def test_explicit_chat_reviewer_configuration_preserves_identity_and_database(local_config):
    runtime, _, gateway = local_config
    configure_models(runtime, mode="two_model", gateway_config=gateway,
                     review_model="glm-5.3", review_api="chat", review_thinking="disabled",
                     reconciliation_provider="secondary")
    config, worker = load(runtime, require_model=True)
    assert worker["REVIEW_MODEL"] == "glm-5.3"
    assert worker["REVIEW_API"] == "chat"
    assert worker["REVIEW_THINKING"] == "disabled"
    assert worker["RECONCILIATION_PROVIDER"] == "secondary"
    assert config["environment"]["DATABASE_URL"] == "unchanged-private-database"


@pytest.mark.parametrize("changes", [
    {"review_api": "guessed"}, {"review_thinking": "false"},
    {"reconciliation_provider": "third_vote"},
    {"visual_review_enabled": "true"}, {"visual_review_model": "../invalid"},
    {"mode": "multimodel", "review_api": "chat"},
])
def test_invalid_review_settings_do_not_mutate_runtime(local_config, changes):
    runtime, _, gateway = local_config
    before = (runtime / "runtime.json").read_bytes()
    with pytest.raises(ValueError):
        configure_models(runtime, **{"mode": "two_model", "gateway_config": gateway, **changes})
    assert (runtime / "runtime.json").read_bytes() == before


def test_public_config_rejected_and_stop_status_do_not_require_model_files(local_config):
    runtime, ark, gateway = local_config
    os.chmod(gateway, 0o644)
    with pytest.raises(ValueError):
        configure_models(runtime, mode="multimodel", gateway_config=gateway)
    os.chmod(gateway, 0o600)
    configure_models(runtime, mode="multimodel", gateway_config=gateway)
    ark.unlink(); gateway.unlink()
    assert load(runtime)[0]["host"] == "127.0.0.1"
    with pytest.raises(FileNotFoundError):
        load(runtime, require_model=True)
    configure_models(runtime, mode="single")
    assert load(runtime)[1]["CORRECTION_MODE"] == "single"


def test_new_local_runtime_has_private_separate_deletion_ledger(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from scripts import mac_local
    pg = tmp_path / 'pg'; pg.mkdir()
    for name in ('initdb', 'pg_ctl', 'createdb'):
        (pg / name).touch()
    model = tmp_path / 'model.env'
    write_private(model, 'ARK_BASE_URL=https://example.invalid\nARK_API_KEY=synthetic\n')
    monkeypatch.setattr(mac_local, 'run', lambda *a, **k: None)
    monkeypatch.setattr(mac_local, 'start_postgres', lambda *a: None)
    runtime = tmp_path / 'fresh'
    mac_local.initialize(SimpleNamespace(runtime=runtime, pg_bin=pg, model_config=model,
                        base_url='http://127.0.0.1:8000', pg_port=55432, host='127.0.0.1'))
    config, _ = load(runtime)
    ledger = Path(config['environment']['DELETION_LEDGER_PATH'])
    assert ledger == runtime / 'ledger/deletions.jsonl'
    assert ledger.read_bytes() == b''
    assert ledger.stat().st_mode & 0o777 == 0o600
    assert ledger.parent.stat().st_mode & 0o777 == 0o700
    assert ledger.parent != runtime / 'assets'


def test_existing_runtime_is_not_silently_given_empty_ledger(local_config):
    runtime, _, _ = local_config
    before = (runtime / 'runtime.json').read_bytes()
    load(runtime)
    assert (runtime / 'runtime.json').read_bytes() == before
    assert not (runtime / 'ledger').exists()


def test_ledger_history_missing_or_wrong_student_is_not_compliant():
    from scripts.mac_local import ledger_history_check
    row = {'id': 'd1', 'tenant_id': 't', 'student_id': 's', 'status': 'completed',
           'server_completed_at': None, 'targets': {}}
    assert ledger_history_check([row], [])['missing_historical_entries'] == 1
    wrong = {'deletion_id': 'd1', 'tenant_id': 't', 'student_id': 'other'}
    assert ledger_history_check([row], [wrong])['missing_historical_entries'] == 1
    right = {**wrong, 'student_id': 's'}
    result = ledger_history_check([row], [right])
    assert result['missing_historical_entries'] == 0
    assert result['historical_backup_compliance'] == 'unverified'
    with pytest.raises(ValueError, match='Conflicting'):
        ledger_history_check([row], [wrong, right])


def test_ledger_bind_requires_existing_file_and_complete_history(local_config, monkeypatch):
    from scripts import mac_local
    runtime, _, _ = local_config
    parent = runtime / 'ledger'; parent.mkdir(mode=0o700)
    missing = parent / 'missing.jsonl'
    before = (runtime / 'runtime.json').read_bytes()
    with pytest.raises(FileNotFoundError):
        mac_local.configure_deletion_ledger(runtime, missing)
    assert (runtime / 'runtime.json').read_bytes() == before
    ledger = parent / 'deletions.jsonl'; write_private(ledger, '')
    monkeypatch.setattr(mac_local, 'audit_deletion_ledger', lambda *a: {'missing_historical_entries': 1, 'deleted_students_without_request': 0})
    with pytest.raises(ValueError, match='Historical'):
        mac_local.configure_deletion_ledger(runtime, ledger)
    assert (runtime / 'runtime.json').read_bytes() == before
    monkeypatch.setattr(mac_local, 'audit_deletion_ledger', lambda *a: {'missing_historical_entries': 0, 'deleted_students_without_request': 0, 'historical_backup_compliance': 'unverified'})
    result = mac_local.configure_deletion_ledger(runtime, ledger)
    assert result['restart_required']
    assert load(runtime)[0]['environment']['DELETION_LEDGER_PATH'] == str(ledger)


def test_ledger_binding_rejects_symlink(local_config):
    from scripts.mac_local import configure_deletion_ledger
    runtime, _, _ = local_config
    parent = runtime / 'ledger'; parent.mkdir(mode=0o700)
    source = parent / 'source.jsonl'; write_private(source, '')
    link = parent / 'link.jsonl'; link.symlink_to(source)
    with pytest.raises(ValueError, match='private regular file'):
        configure_deletion_ledger(runtime, link)


def test_moving_configured_ledger_requires_stopped_writers(local_config, monkeypatch):
    from scripts import mac_local
    runtime, _, _ = local_config
    parent = runtime / 'ledger'; parent.mkdir(mode=0o700)
    old = parent / 'old.jsonl'; write_private(old, '')
    new = parent / 'new.jsonl'; write_private(new, '')
    config = json.loads((runtime / 'runtime.json').read_text())
    config['environment']['DELETION_LEDGER_PATH'] = str(old)
    (runtime / 'runtime.json').write_text(json.dumps(config))
    (runtime / 'worker.pid').write_text(str(os.getpid()))
    with pytest.raises(ValueError, match='Stop API and worker'):
        mac_local.configure_deletion_ledger(runtime, new)
    assert load(runtime)[0]['environment']['DELETION_LEDGER_PATH'] == str(old)


@pytest.mark.parametrize("explicit_models", [False, True])
def test_user_original_pins_models_and_removes_previous_readback_layers(local_config, monkeypatch, explicit_models):
    runtime, ark, gateway = local_config
    configure_models(runtime, mode="two_model", gateway_config=gateway,
                     review_model="glm-5.3", review_api="chat", review_thinking="disabled")
    if explicit_models:
        ark.write_text(ark.read_text() + "ARK_MODEL=deepseek-v4-1-flash-260910\nARK_FALLBACK_MODEL=gpt-6-astra\n")
    before_ark, before_gateway = ark.read_bytes(), gateway.read_bytes()
    monkeypatch.setenv("READBACK_MODEL", "inherited-kimi")
    monkeypatch.setenv("VISUAL_REVIEW_MODEL", "inherited-reviewer")
    monkeypatch.setenv("ZYAI_API_KEY", "inherited-key-must-not-win")
    monkeypatch.setenv("ARK_MODEL", "inherited-model-must-not-win")
    monkeypatch.setenv("ARK_FALLBACK_MODEL", "inherited-reviewer-must-not-win")
    configure_models(runtime, mode="user_original")  # Reuse the saved private gateway path.

    config, no_models = load(runtime)
    _, worker = load(runtime, require_model=True)
    assert worker["ARK_MODEL"] == config["environment"]["ARK_MODEL"] == "deepseek-v4-1-flash-260910"
    assert worker["ARK_FALLBACK_MODEL"] == config["environment"]["ARK_FALLBACK_MODEL"] == "gpt-6-astra"
    assert worker["CORRECTION_MODE"] == "user_original"
    assert worker["ARK_API_KEY"] == "synthetic-ark-secret"
    assert worker["ZYAI_API_KEY"] == "synthetic-gateway-secret"
    assert worker["ZYAI_BASE_URL"] == "https://gateway.example.invalid/v1"
    assert worker["DATABASE_URL"] == "unchanged-private-database"
    assert worker["WORKER_LEASE_SECONDS"] == "600"
    for environment in (config["environment"], no_models, worker):
        assert not any(name in environment for name in (
            "READBACK_MODEL", "REVIEW_MODEL", "REVIEW_API", "REVIEW_THINKING",
            "RECONCILIATION_PROVIDER", "VISUAL_REVIEW_MODEL", "VISUAL_REVIEW_ENABLED"))
    for environment in (no_models, api_environment(worker)):
        assert not any(k.startswith(("ARK_", "ZYAI_", "OPENAI_")) for k in environment)
    persisted = (runtime / "runtime.json").read_text()
    assert "synthetic-ark-secret" not in persisted and "synthetic-gateway-secret" not in persisted
    assert ark.read_bytes() == before_ark and gateway.read_bytes() == before_gateway
    assert (runtime / "runtime.json").stat().st_mode & 0o077 == 0
    assert os.environ["READBACK_MODEL"] == "inherited-kimi"


def test_user_original_requires_explicit_gateway_path(local_config, monkeypatch):
    runtime, _, _ = local_config
    monkeypatch.setenv("ZYAI_BASE_URL", "https://inherited.example.invalid/v1")
    monkeypatch.setenv("ZYAI_API_KEY", "inherited-secret")
    before = (runtime / "runtime.json").read_bytes()
    with pytest.raises(ValueError, match="explicit private gateway"):
        configure_models(runtime, mode="user_original")
    assert (runtime / "runtime.json").read_bytes() == before


@pytest.mark.parametrize("missing", ["ZYAI_BASE_URL", "ZYAI_API_KEY"])
@pytest.mark.parametrize("empty", [False, True])
def test_user_original_missing_gateway_fields_never_use_ark_or_inherited_values(
        local_config, monkeypatch, missing, empty):
    runtime, _, gateway = local_config
    configure_models(runtime, mode="user_original", gateway_config=gateway)
    values = {"ZYAI_BASE_URL": "https://gateway.example.invalid/v1", "ZYAI_API_KEY": "synthetic-gateway-secret"}
    monkeypatch.setenv(missing, values[missing])
    if empty:
        values[missing] = ""
    else:
        del values[missing]
    gateway.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    before = (runtime / "runtime.json").read_bytes()
    with pytest.raises(ValueError, match="gateway configuration is incomplete"):
        load(runtime, require_model=True)
    with pytest.raises(ValueError, match="invalid_private_configuration"):
        configure_models(runtime, mode="user_original", gateway_config=gateway)
    assert (runtime / "runtime.json").read_bytes() == before
    assert "ZYAI_API_KEY" not in load(runtime)[1]


def test_user_original_private_gateway_and_unavailable_credentials(local_config):
    runtime, ark, gateway = local_config
    before = (runtime / "runtime.json").read_bytes()
    gateway.chmod(0o644)
    with pytest.raises(ValueError, match="private regular file"):
        configure_models(runtime, mode="user_original", gateway_config=gateway)
    assert (runtime / "runtime.json").read_bytes() == before
    gateway.chmod(0o600)
    configure_models(runtime, mode="user_original", gateway_config=gateway)
    gateway.chmod(0o644)
    with pytest.raises(ValueError, match="private regular file"):
        load(runtime, require_model=True)
    ark.unlink()
    gateway.unlink()
    assert load(runtime)[1]["CORRECTION_MODE"] == "user_original"
    configure_models(runtime, mode="single")
    assert load(runtime)[1]["CORRECTION_MODE"] == "single"


@pytest.mark.parametrize("changes", [
    {"review_model": "glm-5.3"}, {"review_api": "chat"}, {"review_thinking": "disabled"},
])
def test_user_original_rejects_alternate_review_configuration(local_config, changes):
    runtime, _, gateway = local_config
    before = (runtime / "runtime.json").read_bytes()
    with pytest.raises(ValueError):
        configure_models(runtime, mode="user_original", gateway_config=gateway, **changes)
    assert (runtime / "runtime.json").read_bytes() == before


@pytest.mark.parametrize("mode", ["single", "multimodel", "two_model"])
def test_switching_back_from_user_original_restores_existing_mode_settings(local_config, mode):
    runtime, ark, gateway = local_config
    configure_models(runtime, mode="user_original", gateway_config=gateway)
    ark.write_text(ark.read_text() + "ARK_MODEL=existing-primary\nARK_FALLBACK_MODEL=existing-fallback\n")
    configure_models(runtime, mode=mode)
    _, worker = load(runtime, require_model=True)
    assert worker["CORRECTION_MODE"] == mode
    assert worker["ARK_MODEL"] == "existing-primary"
    assert worker["ARK_FALLBACK_MODEL"] == "existing-fallback"
    assert worker["READBACK_MODEL"] == "kimi-k3"
    assert worker["REVIEW_MODEL"] == "gpt-6-astra"
    if mode == "single":
        assert "ZYAI_API_KEY" not in worker
    else:
        assert worker["ZYAI_API_KEY"] == "synthetic-gateway-secret"


def test_user_original_cli_configures_without_starting_services(local_config, monkeypatch, capsys):
    from scripts import mac_local
    runtime, _, gateway = local_config

    def unexpected_run(*args, **kwargs):
        pytest.fail("configuration must not start services or call models")

    monkeypatch.setattr(mac_local.subprocess, "run", unexpected_run)
    monkeypatch.setattr(mac_local.sys, "argv", [
        "mac_local.py", "--runtime", str(runtime), "configure-models",
        "--mode", "user_original", "--gateway-config", str(gateway),
    ])
    mac_local.main()
    config, worker = load(runtime, require_model=True)
    assert config["gateway_config"] == str(gateway.resolve())
    assert worker["CORRECTION_MODE"] == "user_original"
    assert "READBACK_MODEL" not in worker
    output = capsys.readouterr().out
    assert "user_original" in output
    assert "synthetic-ark-secret" not in output and "synthetic-gateway-secret" not in output


@pytest.mark.parametrize("source", ["model_file", "runtime_environment"])
@pytest.mark.parametrize("name", ["ARK_MODEL", "ARK_FALLBACK_MODEL"])
@pytest.mark.parametrize("value", ["unsupported-explicit-model", ""])
def test_user_original_rejects_explicit_incompatible_models_without_overwriting_files(
        local_config, source, name, value):
    runtime, ark, gateway = local_config
    configure_models(runtime, mode="user_original", gateway_config=gateway)
    if source == "model_file":
        ark.write_text(ark.read_text() + f"{name}={value}\n")
    else:
        config = json.loads((runtime / "runtime.json").read_text())
        config["environment"][name] = value
        (runtime / "runtime.json").write_text(json.dumps(config))
    paths = (runtime / "runtime.json", ark, gateway)
    before = [path.read_bytes() for path in paths]
    with pytest.raises(ValueError, match=f"user_original requires {name}=") as error:
        load(runtime, require_model=True)
    if value:
        assert value not in str(error.value)
    with pytest.raises(ValueError, match=f"user_original requires {name}="):
        configure_models(runtime, mode="user_original", gateway_config=gateway)
    assert [path.read_bytes() for path in paths] == before
    # Non-worker inspection and stop/status must remain usable without models.
    _, no_models = load(runtime, require_model=False)
    assert "ARK_MODEL" not in no_models and "ARK_FALLBACK_MODEL" not in no_models
    assert "REVIEW_MODEL" not in no_models and "VISUAL_REVIEW_MODEL" not in no_models


def test_user_original_start_exposes_both_keys_only_to_worker(local_config, monkeypatch):
    from types import SimpleNamespace
    from scripts import mac_local
    runtime, _, gateway = local_config
    configure_models(runtime, mode="user_original", gateway_config=gateway)
    config = json.loads((runtime / "runtime.json").read_text())
    config["environment"]["PUBLIC_BASE_URL"] = "http://127.0.0.1:8000"
    (runtime / "runtime.json").write_text(json.dumps(config))
    calls, spawned = [], []
    monkeypatch.setattr(mac_local.subprocess, "run", lambda args, **kwargs: (
        calls.append(kwargs["env"]) or SimpleNamespace(returncode=0)))
    monkeypatch.setattr(mac_local, "start_postgres", lambda *args: None)
    monkeypatch.setattr(mac_local, "spawn", lambda runtime, name, args, env: spawned.append((name, env)))
    monkeypatch.setattr(mac_local, "wait_ready", lambda *args: None)
    monkeypatch.setattr(mac_local.sys, "argv", ["mac_local.py", "--runtime", str(runtime), "start"])
    mac_local.main()
    assert [name for name, _ in spawned] == ["api", "worker"]
    assert len(calls) == 1
    for environment in (calls[0], spawned[0][1]):
        assert not any(key.startswith(("ARK_", "ZYAI_", "OPENAI_")) for key in environment)
    worker = spawned[1][1]
    assert worker["ARK_API_KEY"] == "synthetic-ark-secret"
    assert worker["ZYAI_API_KEY"] == "synthetic-gateway-secret"
    assert worker["ARK_MODEL"] == "deepseek-v4-1-flash-260910"
    assert worker["ARK_FALLBACK_MODEL"] == "gpt-6-astra"
    assert "READBACK_MODEL" not in worker
