"""Whole-page candidates use real HTTP/worker transactions and no external API."""
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401 -- shared real-DB fixture
from xueban_backend.correction_core import now
from xueban_backend.correction_models import Entity, Task
from xueban_backend.worker import CorrectionWorker


def assessment(verdict="incorrect", status="matched"):
    return {
        "verdict": verdict, "reason_code": None,
        "basis_summary": "本题的学生作答已独立核对，模型判断仍为候选。",
        "uncertainties": [],
        "verification": {
            "mode": "multimodel", "status": status,
            "primary_model": "synthetic-primary", "readback_model": "synthetic-reader",
            "review_model": None, "independent_readback": True, "reviewed": False,
            "reason_codes": [],
        },
    }


def enable_page_assessments(flow, transform=None):
    recognize = flow.pipeline.recognize

    def page(**context):
        result = recognize(**context)
        for item in result.data["items"]:
            item["assessment"] = assessment()
        if transform:
            transform(result.data["items"])
        result.data["audit"] = {"primary": {"raw": "synthetic private read"}, "reader": {"raw": "independent synthetic read"}}
        return result

    flow.pipeline.recognize = page


def current_case(flow, case_id):
    return flow.client.get(f"/v1/cases/{case_id}").json()


def submit_correction(flow, case):
    asset = flow.uploaded()
    response = flow.post(f"/v1/cases/{case['id']}/attempts", {
        "expected_revision": case["revision"], "parent_attempt_id": case["current_attempt_id"],
        "question_revision": case["current_question_revision"], "asset_ids": [asset["id"]],
        "occurred_at": "2026-10-18T01:01:00Z", "reported_help": "unknown",
    })
    assert response.status_code == 202, response.text
    return asset, response.json()["job"]


def test_page_job_persists_all_candidates_checks_and_private_audit_without_extra_calls(flow):
    flow.pipeline.extra_item_label = "2"
    enable_page_assessments(flow)
    flow.ready()
    homework = flow.homework()
    assert len(homework["job_ids"]) == 1
    assert len(homework["case_ids"]) == 2
    assert flow.pipeline.calls == ["recognize"]
    with Session(flow.database.engine) as session:
        task = session.get(Task, homework["job_ids"][0])
        assert task.audit["executions"][0]["candidate"]["audit"]["reader"]["raw"] == "independent synthetic read"
        for case_id in homework["case_ids"]:
            case = current_case(flow, case_id)
            decision = case["current_decision"]
            assert decision["origin"] == "model"
            assert decision["validity"] == "candidate"
            assert decision["confirmed_by"] is None
            assert decision["confirmation_source"] is None
            assert case["progress"] == "needs_correction"
            check = session.get(Entity, decision["check_id"])
            assert check.document["job_id"] == task.id
            assert check.document["decision_ids"] == [decision["id"]]
            assert check.document["input_versions"]["attempt_id"] == case["current_attempt_id"]
            assert check.annotations["assessment"]["verification"]["independent_readback"] is True
            assert "assessment" not in check.document
            assert session.get(Entity, decision["id"]).annotations["recognition_job_id"] == task.id


def test_one_conflicting_item_does_not_discard_clear_page_results(flow):
    flow.pipeline.extra_item_label = "2"

    def mixed(items):
        items[0]["assessment"] = assessment("correct")
        items[1]["assessment"] = assessment("correct", "conflict")

    enable_page_assessments(flow, mixed)
    flow.ready()
    cases = [current_case(flow, case_id) for case_id in flow.homework()["case_ids"]]
    assert sorted(case["current_decision"]["verdict"] for case in cases) == ["correct", "unable_to_judge"]
    assert {case["current_decision"]["validity"] for case in cases} == {"candidate", "needs_review"}
    assert all(case["progress"] != "verified_resolved" for case in cases)


@pytest.mark.parametrize("flag", ["unclear", "uncertainty", "degraded", "not_independent", "not_ready"])
def test_worker_does_not_promote_questionable_assessment_to_clear_correct(flow, flag):
    def uncertain(items):
        item = items[0]
        item["assessment"] = assessment("correct")
        if flag == "unclear":
            item["answer_status"] = "unclear"
        elif flag == "not_ready":
            item["recognition_status"] = "needs_revision"
        elif flag == "uncertainty":
            item["assessment"]["uncertainties"] = ["末位字迹不清"]
        elif flag == "degraded":
            item["assessment"]["verification"]["status"] = "degraded"
        else:
            item["assessment"]["verification"]["independent_readback"] = False

    enable_page_assessments(flow, uncertain)
    case = flow.ready()
    assert case["current_decision"]["verdict"] == "unable_to_judge"
    assert case["current_decision"]["validity"] == "needs_review"
    assert case["progress"] == "pending"


def test_page_lease_recovery_and_duplicate_finish_cannot_duplicate_decisions(flow):
    enable_page_assessments(flow)
    _, job = flow.recognize()
    old_claim = flow.worker.claim()
    old_context = flow.worker._load(*old_claim)[1]
    old_result = flow.pipeline.recognize(**old_context)
    with Session(flow.database.engine) as session:
        session.get(Task, job["id"]).lease_expires_at = now() - timedelta(seconds=1)
        session.commit()
    replacement = CorrectionWorker(flow.database.engine, flow.cfg, flow.pipeline)
    assert replacement.run_once()
    assert not flow.worker.finish(*old_claim, result=old_result)
    assert not replacement.run_once()
    with Session(flow.database.engine) as session:
        for kind in ("question", "attempt", "case", "check", "decision"):
            assert session.scalar(select(func.count()).select_from(Entity).where(Entity.kind == kind)) == 1


def test_corrected_whole_page_binds_only_unique_target_and_new_photo_decision(flow):
    enable_page_assessments(flow)
    case = flow.ready()
    old_decision = case["current_decision"]
    parent_id = case["current_attempt_id"]
    asset, job = submit_correction(flow, case)
    old_recognize = flow.pipeline.recognize

    def corrected_page(**context):
        result = old_recognize(**context)
        target = result.data["items"][0]
        target["answer_text"] = "2"
        target["assessment"] = assessment("correct")
        unrelated = {**deepcopy(target), "label": "2", "stem_text": "9 + 9 = ?", "answer_text": "18"}
        result.data["items"] = [unrelated, target]
        return result

    flow.pipeline.recognize = corrected_page
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded"
    case = current_case(flow, case["id"])
    attempt = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    assert attempt["answer_text"] == "2"
    assert attempt["parent_attempt_id"] == parent_id
    assert attempt["asset_ids"] == [asset["id"]]
    assert attempt["recognition_status"] == "ready"
    assert case["current_decision"]["verdict"] == "correct"
    assert case["current_decision"]["validity"] == "candidate"
    assert case["current_decision"]["input_versions"]["attempt_id"] == attempt["id"]
    assert case["current_decision"]["input_versions"]["attempt_recognition_revision"] == attempt["recognition_revision"]
    assert case["progress"] != "verified_resolved"
    assert flow.client.get(f"/v1/decisions/{old_decision['id']}").json()["record"]["validity"] == "stale"
    assert flow.pipeline.calls == ["recognize", "recognize"]
    with Session(flow.database.engine) as session:
        row = session.get(Entity, case["current_decision"]["id"])
        assert row.annotations["recognition_asset_ids"] == [asset["id"]]


@pytest.mark.parametrize("duplicate", [False, True])
def test_wrong_or_ambiguous_correction_never_attaches_page_assessment(flow, duplicate):
    enable_page_assessments(flow)
    case = flow.ready()
    _, job = submit_correction(flow, case)
    if duplicate:
        flow.pipeline.extra_item_label = flow.pipeline.label
    else:
        flow.pipeline.stem = "9 + 9 = ?"
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded"
    case = current_case(flow, case["id"])
    attempt = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    assert attempt["recognition_status"] == "needs_revision"
    assert attempt["answer_text"] is None
    assert case["current_decision"] is None
    assert len(case["check_ids"]) == 1


def test_newer_attempt_fences_late_whole_page_assessment(flow):
    enable_page_assessments(flow)
    case = flow.ready()
    _, first_job = submit_correction(flow, case)
    claim = flow.worker.claim()
    result = flow.pipeline.recognize(**flow.worker._load(*claim)[1])
    latest = current_case(flow, case["id"])
    _, second_job = submit_correction(flow, latest)
    assert flow.worker.finish(*claim, result=result)
    assert flow.client.get(f"/v1/jobs/{first_job['id']}").json()["status"] == "superseded"
    latest = current_case(flow, case["id"])
    assert latest["current_decision"] is None
    assert len(latest["check_ids"]) == 1
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{second_job['id']}").json()["status"] == "succeeded"
    latest = current_case(flow, case["id"])
    assert latest["current_decision"]["input_versions"]["attempt_id"] == latest["current_attempt_id"]
    assert len(latest["check_ids"]) == 2


def test_corrected_multiblank_page_matches_fullwidth_labels_without_merging_blanks(flow):
    flow.pipeline.label = "３（１）#１"
    flow.pipeline.extra_item_label = "３（１）#２"
    enable_page_assessments(flow)
    flow.ready()
    # Case IDs are opaque and listing order is not the printed-question order.
    # Deliberately choose the first blank via its original recognition label.
    with Session(flow.database.engine) as session:
        question_id = session.scalar(select(Entity.id).where(
            Entity.homework_id == flow.homework_id, Entity.kind == "question",
            Entity.annotations["label"].as_string() == "３（１）#１"))
        case_id = session.scalar(select(Entity.id).where(
            Entity.homework_id == flow.homework_id, Entity.kind == "case",
            Entity.document["question_id"].as_string() == question_id))
    case = current_case(flow, case_id)
    _, job = submit_correction(flow, case)
    flow.pipeline.label = "3(1)#1"
    flow.pipeline.extra_item_label = "3(1)#2"
    recognize = flow.pipeline.recognize

    def distinguish_blanks(**context):
        result = recognize(**context)
        result.data["items"][0]["answer_text"] = "first blank new answer"
        result.data["items"][1]["answer_text"] = "second blank new answer"
        return result

    flow.pipeline.recognize = distinguish_blanks
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded"
    case = current_case(flow, case["id"])
    attempt = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    assert attempt["recognition_status"] == "ready"
    assert attempt["answer_text"] == "first blank new answer"
    assert case["current_decision"]["validity"] == "candidate"


@pytest.mark.parametrize("original,replacement", [
    ("3(10) 某商店上月收入a元，本月比上月的2倍多10元，求本月收入。", "某商店上月收入a元，本月比上月的2倍多10元，求本月收入。"),
    ("3(10)、计算3(x - 2)的值。", "计算3(x-2)的值。"),
])
def test_multimodel_correction_accepts_label_decoration_without_changing_full_stem(flow, original, replacement):
    flow.pipeline.label = "3(10)"
    flow.pipeline.stem = original
    enable_page_assessments(flow)
    case = flow.ready()
    _, job = submit_correction(flow, case)
    flow.pipeline.stem = replacement
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded"
    case = current_case(flow, case["id"])
    attempt = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    assert attempt["recognition_status"] == "ready"
    assert case["current_decision"]["validity"] == "candidate"


@pytest.mark.parametrize("original,replacement", [
    ("3(10) 计算3(x-2)的值。", "计算3x-2的值。"),
    ("3(10) 计算3(x-2)的值。", "计算3(x+2)的值。"),
    ("3(10) 计算3(x-2)的值。", "计算4(x-2)的值。"),
    ("3(10) 计算x²的值。", "计算x2的值。"),
    ("3(10)+2", "2"),
    ("3(10) 某商店上月收入a元，本月比上月的2倍多10元，求本月收入。", "某商店上月收入a元，本月比上月的2倍多10元。"),
])
def test_multimodel_correction_rejects_changed_math_or_partial_stem(flow, original, replacement):
    flow.pipeline.label = "3(10)"
    flow.pipeline.stem = original
    enable_page_assessments(flow)
    case = flow.ready()
    _, job = submit_correction(flow, case)
    flow.pipeline.stem = replacement
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded"
    case = current_case(flow, case["id"])
    attempt = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    assert attempt["recognition_status"] == "needs_revision"
    assert attempt["answer_text"] is None
    assert case["current_decision"] is None


def test_legacy_correction_keeps_original_stem_comparison_without_page_normalization(flow):
    flow.pipeline.label = "3(10)"
    flow.pipeline.stem = "3(10) 计算3(x-2)的值。"
    case = flow.ready()
    _, job = submit_correction(flow, case)
    flow.pipeline.stem = "计算3(x-2)的值。"
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded"
    case = current_case(flow, case["id"])
    attempt = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    assert attempt["recognition_status"] == "needs_revision"
    assert attempt["answer_text"] is None
    assert case["current_decision"] is None


def test_explicit_check_remains_available_and_invalidates_whole_page_candidate(flow):
    enable_page_assessments(flow)
    case = flow.ready()
    old_decision = case["current_decision"]
    flow.pipeline.verdict = "correct"
    job = flow.start_check(case)
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded"
    case = current_case(flow, case["id"])
    assert case["current_decision"]["verdict"] == "correct"
    assert case["current_decision"]["replaces_decision_id"] == old_decision["id"]
    assert flow.client.get(f"/v1/decisions/{old_decision['id']}").json()["record"]["validity"] == "stale"
    assert len(case["check_ids"]) == 2
