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
