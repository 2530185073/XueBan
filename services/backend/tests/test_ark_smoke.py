from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.ark_smoke import DEFAULT_MODEL, PipelineFailure, RULE, main, provider_settings, run_pipeline, write_report
from xueban_backend.providers.ark import Attempt, CandidateResult, Metadata, ProviderError


class FakeClient:
    def __init__(self):
        self.calls = []
        self.outputs = [
            {"readable": True, "question_text": "Expand 3(x - 2)", "student_answer": "3x - 2", "uncertainties": []},
            {"verdict": "incorrect", "evidence_summary": "The factor was not applied to the second term.", "error_summary": "Distribution"},
            {"level": 1, "content": "括号外的数需要与哪些项相乘？", "exposes_answer": False},
            {"verdict": "correct", "evidence_summary": "Both terms are multiplied.", "error_summary": None},
            {"concept": "Distributive property", "source_ref": "synthetic-rule-001", "source_quote": RULE},
        ]

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        data = self.outputs[len(self.calls) - 1]
        if isinstance(data, Exception):
            raise data
        return CandidateResult(kwargs["role"], deepcopy(data), Metadata(DEFAULT_MODEL, DEFAULT_MODEL, "synthetic-request", None, None, 1, ()))


def test_five_roles_use_distinct_new_image_without_old_verdict_for_recheck():
    client = FakeClient()
    report = run_pipeline(client)
    assert [c["role"] for c in client.calls] == report["planned_roles"]
    assert client.calls[0]["images"] != client.calls[3]["images"]
    assert "Distribution" not in client.calls[3]["text"]
    assert "3x - 6" not in client.calls[3]["text"]
    assert all(c["max_tokens"] == 4096 for c in client.calls)
    assert report["smoke_status"] == "passed"
    assert report["synthetic_only"] and report["release_approval"] == "not_assessed"
    assert report["cost"]["amount"] is None
    assert all(s["status"] == "candidate" for s in report["steps"])


def test_unreadable_stops_before_inventing_a_check_or_hint():
    client = FakeClient()
    client.outputs[0] = {"readable": False, "question_text": None, "student_answer": None, "uncertainties": ["Unreadable"]}
    assert run_pipeline(client)["smoke_status"] == "recognition_incomplete"
    assert len(client.calls) == 1


def test_failure_preserves_earlier_candidates_and_failed_attempts():
    client = FakeClient()
    client.outputs[2] = ProviderError("rate_limited", attempts=(Attempt(1, 429, "rate_limited", 3),), latency_ms=3)
    with pytest.raises(PipelineFailure) as error:
        run_pipeline(client)
    report = error.value.report
    assert report["smoke_status"] == "provider_failure"
    assert [s["status"] for s in report["steps"]] == ["candidate", "candidate", "technical_failure"]
    assert report["steps"][-1]["attempts"][0]["charge_status"] == "unknown"
    assert len(client.calls) == 3


def test_generated_knowledge_must_quote_supplied_source():
    client = FakeClient()
    client.outputs[-1]["source_quote"] = "Unattributed new claim"
    report = run_pipeline(client)
    assert not report["source_quote_verified"]
    assert report["smoke_status"] == "unexpected_candidate"


def test_no_live_opt_in_means_no_request(tmp_path):
    with pytest.raises(SystemExit) as error:
        main(["--output", str(tmp_path / "report.json")])
    assert error.value.code == 2
    assert not (tmp_path / "report.json").exists()


def test_private_configuration_is_explicit_and_not_shell_code(tmp_path, monkeypatch):
    for name in ("ARK_BASE_URL", "ARK_API_KEY", "ARK_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert not provider_settings(None).enabled
    path = tmp_path / "private.env"
    path.write_text("ARK_BASE_URL=https://provider.example.invalid/api/v3\nARK_API_KEY=test-only-secret\n")
    path.chmod(0o644)
    with pytest.raises(ValueError):
        provider_settings(path)
    path.chmod(0o600)
    cfg = provider_settings(path)
    assert cfg.enabled and cfg.max_attempts == 1
    assert "test-only-secret" not in repr(cfg)


def test_reports_cannot_overwrite_existing_run(tmp_path):
    path = tmp_path / "run.json"
    write_report(path, {"synthetic_only": True})
    with pytest.raises(FileExistsError):
        write_report(path, {"synthetic_only": False})
    assert json.loads(path.read_text())["synthetic_only"]


def test_invalid_output_stops_before_client_or_billable_requests(tmp_path, monkeypatch):
    def forbidden(*args):
        pytest.fail("client must not be created before output preflight")
    monkeypatch.setattr("scripts.ark_smoke.ArkClient", forbidden)
    assert main(["--live", "--output", str(tmp_path / "missing" / "report.json")]) == 2


def test_live_report_is_reserved_and_checkpointed_before_next_request(tmp_path, monkeypatch):
    path = tmp_path / "run.json"
    client = FakeClient()
    original_generate = client.generate

    def generate(**kwargs):
        previous = json.loads(path.read_text())
        assert len(previous["steps"]) == len(client.calls)
        assert previous["smoke_status"] == "in_progress"
        return original_generate(**kwargs)

    client.generate = generate

    class Context:
        def __init__(self, settings):
            assert json.loads(path.read_text())["smoke_status"] == "initializing"

        def __enter__(self):
            return client

        def __exit__(self, *_):
            pass

    monkeypatch.setattr("scripts.ark_smoke.ArkClient", Context)
    assert main(["--live", "--output", str(path)]) == 0
    assert json.loads(path.read_text())["smoke_status"] == "passed"
    assert path.stat().st_mode & 0o777 == 0o600


def test_failure_checkpoint_keeps_previous_paid_steps(tmp_path):
    from scripts.ark_smoke import report_writer
    client = FakeClient()
    client.outputs[1] = ProviderError("deadline_exceeded")
    path = tmp_path / "run.json"
    with report_writer(path) as checkpoint, pytest.raises(PipelineFailure):
        run_pipeline(client, checkpoint=checkpoint)
    saved = json.loads(path.read_text())
    assert saved["smoke_status"] == "provider_failure"
    assert len(saved["steps"]) == 2
    assert saved["steps"][0]["metadata"]["model"] == DEFAULT_MODEL


def test_checkpoint_failure_prevents_next_billable_request():
    client = FakeClient()

    def broken_disk(report):
        if len(report["steps"]) == 1:
            raise OSError("synthetic disk failure")

    with pytest.raises(OSError):
        run_pipeline(client, checkpoint=broken_disk)
    assert len(client.calls) == 1
