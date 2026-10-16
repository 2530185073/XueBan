"""Half-month reports: fixed windows, locked facts, publication, corrections and guardian links."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401  (pytest fixture reuse)
from test_learning import learning, link, review, reviewed
from xueban_backend import learning_reports
from xueban_backend.asset_store import LocalAssetStore
from xueban_backend.device_auth import utc
from xueban_backend.governance import purge_student
from xueban_backend.learning_models import LearningReport, ReportShareLink
from xueban_backend.learning_reports import last_completed_period, local_day, period_of, window
from xueban_backend.models import Homework


def test_periods_are_shanghai_half_months_left_closed():
    assert period_of(date(2026, 10, 15)) == (date(2026, 10, 1), date(2026, 10, 16))
    assert period_of(date(2026, 10, 16)) == (date(2026, 10, 16), date(2026, 11, 1))
    assert period_of(date(2026, 12, 31)) == (date(2026, 12, 16), date(2027, 1, 1))
    assert period_of(date(2028, 2, 29)) == (date(2028, 2, 16), date(2028, 3, 1))
    start, end = window(date(2026, 10, 1), date(2026, 10, 16))
    assert start == datetime(2026, 9, 30, 16, tzinfo=timezone.utc) and end == datetime(2026, 10, 15, 16, tzinfo=timezone.utc)
    assert local_day(datetime(2026, 10, 15, 15, 59, tzinfo=timezone.utc)) == date(2026, 10, 15)
    assert local_day(datetime(2026, 10, 15, 16, 0, tzinfo=timezone.utc)) == date(2026, 10, 16)
    assert last_completed_period(datetime(2026, 10, 15, 16, 30, tzinfo=timezone.utc)) == (date(2026, 10, 1), date(2026, 10, 16))
    assert last_completed_period(datetime(2026, 11, 1, 1, tzinfo=timezone.utc)) == (date(2026, 10, 16), date(2026, 11, 1))
    assert last_completed_period(datetime(2026, 10, 31, 15, 59, tzinfo=timezone.utc)) == (date(2026, 10, 1), date(2026, 10, 16))


@pytest.fixture
def after_period(flow, monkeypatch):
    with Session(flow.database.engine) as session:
        occurred = utc(session.get(Homework, flow.homework_id).occurred_at)
    start, end = period_of(local_day(occurred))
    monkeypatch.setattr(learning_reports, "now", lambda: window(start, end)[1] + timedelta(hours=1))
    return start.isoformat()


def reports(flow):
    return flow.client.get("/v1/students/student_a/reports").json()["items"]


def publish(flow, report, note=None):
    return flow.client.post(f"/v1/reports/{report['id']}/publish", json={"expected_revision": report["revision"], "correction_note": note})


def test_every_period_gets_a_report_even_without_subject_evidence(flow, after_period):
    reviewed(flow)
    assert reports(flow) == []
    learning(flow).drain()
    teacher(flow)
    [draft] = reports(flow)
    assert draft["period_start"] == after_period and draft["status"] == "draft" and draft["version"] == 1
    snapshot = draft["snapshot"]
    assert snapshot["school_tasks"]["questions"] == 1 and snapshot["school_tasks"]["awaiting_verification"] == 1
    assert snapshot["sufficient"] is False and snapshot["focus"] == []
    assert snapshot["coverage"]["observed_days"] == 1
    published = publish(flow, draft)
    assert published.status_code == 200, published.text
    created = flow.client.post(f"/v1/reports/{draft['id']}/share-links", json={"expires_in_days": 7})
    assert created.status_code == 201, created.text
    page = flow.client.get(created.json()["path"], headers={"Authorization": ""})
    assert page.status_code == 200 and "暂无足够的已核实作答" in page.text
    assert page.headers["cache-control"] == "no-store" and "default-src 'none'" in page.headers["content-security-policy"]
    data = flow.client.get(f"/v1/report-links/{created.json()['token']}", headers={"Authorization": ""}).json()
    assert data["report"]["version"] == 1 and "source_digest" not in data["report"]
    [listed] = flow.client.get(f"/v1/reports/{draft['id']}/share-links").json()["items"]
    assert listed["view_count"] == 2 and "token" not in listed
    flow.client.post(f"/v1/report-share-links/{listed['id']}/revoke")
    assert flow.client.get(created.json()["path"], headers={"Authorization": ""}).status_code == 404
    assert flow.client.get("/r/not-a-token", headers={"Authorization": ""}).status_code == 404


def test_current_period_cannot_be_generated_and_writes_need_staff(flow):
    reviewed(flow)
    start, _ = period_of(local_day(datetime.now(timezone.utc)))
    body = {"period_start": start.isoformat()}
    assert flow.client.post("/v1/students/student_a/reports", json=body).status_code == 403
    teacher(flow)
    assert flow.client.post("/v1/students/student_a/reports", json=body).status_code == 422
    assert flow.client.post("/v1/students/student_a/reports", json={"period_start": "2026-10-05"}).status_code == 422


def test_published_report_is_corrected_by_a_new_version_with_explanation(flow, after_period):
    case = reviewed(flow)
    teacher(flow)
    case = review(flow, case)
    link(flow, case)
    learning(flow).drain()
    [draft] = reports(flow)
    [focus] = draft["snapshot"]["focus"]
    assert focus["knowledge_id"] == "kp-rational-add-sub" and (focus["errors"], focus["denominator"]) == (1, 1)
    assert focus["cards"][0]["certainty"] == "老师已核实" and focus["cards"][0]["staff_explanation"] == "已核对原图及过程。"
    edited = flow.client.patch(f"/v1/reports/{draft['id']}", json={"expected_revision": draft["revision"], "staff_note": "作业态度认真。"})
    assert edited.status_code == 200, edited.text
    assert publish(flow, draft).status_code == 409
    v1 = publish(flow, edited.json()).json()
    token = flow.client.post(f"/v1/reports/{v1['id']}/share-links", json={}).json()["token"]
    review(flow, flow.client.get(f"/v1/cases/{case['id']}").json(), action="replace", verdict="correct")
    learning(flow).drain()
    items = {r["version"]: r for r in reports(flow)}
    assert items[1]["status"] == "published" and items[2]["status"] == "draft"
    assert items[2]["supersedes_id"] == v1["id"] and items[2]["regenerated_reason"] == "correction_required"
    assert items[2]["snapshot"]["focus"] == [] and items[2]["staff_note"] == "作业态度认真。"
    assert publish(flow, items[2]).status_code == 422
    v2 = publish(flow, items[2], "老师复核原图后，这道题改判为正确。")
    assert v2.status_code == 200, v2.text
    assert {r["version"]: r["status"] for r in reports(flow)} == {1: "superseded", 2: "published"}
    data = flow.client.get(f"/v1/report-links/{token}", headers={"Authorization": ""}).json()
    assert data["report"]["version"] == 2
    assert data["corrections"] == [{"version": 2, "published_at": v2.json()["published_at"],
                                    "correction_note": "老师复核原图后，这道题改判为正确。"}]
    page = flow.client.get(f"/r/{token}", headers={"Authorization": ""}).text
    assert "更正说明" in page and "改判为正确" in page


def test_publish_waits_for_projection_and_draft_follows_facts(flow, after_period):
    case = reviewed(flow)
    teacher(flow)
    learning(flow).drain()
    [draft] = reports(flow)
    case = review(flow, case)
    link(flow, case)
    blocked = publish(flow, draft)
    assert blocked.status_code == 409 and blocked.json()["code"] == "projection_pending"
    learning(flow).drain()
    [fresh] = reports(flow)
    assert fresh["id"] == draft["id"] and fresh["revision"] > draft["revision"]
    assert fresh["regenerated_reason"] == "facts_changed" and len(fresh["snapshot"]["focus"]) == 1
    assert publish(flow, draft).status_code == 409
    assert publish(flow, fresh).status_code == 200


def test_student_sees_only_published_reports_and_purge_removes_them(flow, after_period):
    reviewed(flow)
    learning(flow).drain()
    with Session(flow.database.engine) as session:
        draft_id = session.scalar(select(LearningReport.id).where(LearningReport.student_id == "student_a"))
        assert session.scalar(select(LearningReport.id).where(LearningReport.student_id == "student_b"))
    assert reports(flow) == []
    assert flow.client.get(f"/v1/reports/{draft_id}").status_code == 404
    teacher(flow)
    draft = flow.client.get(f"/v1/reports/{draft_id}").json()
    publish(flow, draft)
    flow.client.post(f"/v1/reports/{draft_id}/share-links", json={})
    with Session(flow.database.engine) as session:
        purge_student(session, LocalAssetStore(flow.cfg.asset_storage_dir), "tenant_a", "student_a")
        session.commit()
        for model in (LearningReport, ReportShareLink):
            assert session.scalar(select(func.count()).select_from(model).where(model.student_id == "student_a")) == 0
