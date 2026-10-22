"""Half-month reports and daily feedback: fixed windows, locked facts, publication, corrections and guardian links."""
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


def reports(flow, cadence="half_month"):
    return [r for r in flow.client.get("/v1/students/student_a/reports").json()["items"] if r["cadence"] == cadence]


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
    assert flow.client.get("/v1/reports?status=anything").status_code == 422
    assert flow.client.get("/v1/reports?limit=1").json()["next_offset"] is None


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
        draft_id = session.scalar(select(LearningReport.id).where(LearningReport.student_id == "student_a",
                                                                  LearningReport.cadence == "half_month"))
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


@pytest.fixture
def same_day(flow, monkeypatch):
    with Session(flow.database.engine) as session:
        occurred = utc(session.get(Homework, flow.homework_id).occurred_at)
    monkeypatch.setattr(learning_reports, "now", lambda: occurred + timedelta(minutes=1))
    return local_day(occurred).isoformat()


def test_daily_feedback_is_drafted_the_same_day_and_lists_what_to_correct(flow, same_day):
    case = reviewed(flow)
    teacher(flow)
    case = review(flow, case)
    link(flow, case)
    learning(flow).drain()
    [draft] = reports(flow, "daily")
    assert draft["period_start"] == same_day and draft["status"] == "draft" and draft["version"] == 1
    [queued] = flow.client.get(f"/v1/reports?cadence=daily&period_start={same_day}").json()["items"]
    assert queued["id"] == draft["id"] and queued["student_name"] and queued["mistake_count"] == 1
    assert draft["id"] not in {r["id"] for r in flow.client.get("/v1/reports").json()["items"]}
    snapshot = draft["snapshot"]
    assert snapshot["rule_version"] == "daily-r2-evidence-source" and snapshot["unknown_current"] == []
    [mistake] = snapshot["mistakes"]
    assert mistake["case_id"] == case["id"] and mistake["verdict"] == "答错" and mistake["correction"] == "还没订正"
    assert [f["knowledge_id"] for f in snapshot["focus"]] == ["kp-rational-add-sub"]
    edited = flow.client.patch(f"/v1/reports/{draft['id']}", json={"expected_revision": draft["revision"], "staff_note": "今天很专注。",
                                                                   "next_step": "明天先把这道题订正完。"}).json()
    v1 = publish(flow, edited)
    assert v1.status_code == 200, v1.text
    created = flow.client.post(f"/v1/reports/{draft['id']}/share-links", json={"expires_in_days": 7}).json()
    page = flow.client.get(created["path"], headers={"Authorization": ""}).text
    assert "今日学习反馈" in page and "今天错的题和订正情况" in page and "还没订正" in page and "今天很专注。" in page
    assert "半月" not in page

    review(flow, flow.client.get(f"/v1/cases/{case['id']}").json(), action="replace", verdict="correct")
    learning(flow).drain()
    items = {r["version"]: r for r in reports(flow, "daily")}
    assert items[2]["status"] == "draft" and items[2]["supersedes_id"] == v1.json()["id"] and items[2]["snapshot"]["mistakes"] == []
    assert items[2]["staff_note"] == "今天很专注。"
    assert publish(flow, items[2]).status_code == 200
    page = flow.client.get(created["path"], headers={"Authorization": ""}).text
    assert "老师更新了今天的情况" in page and "今天错的题和订正情况" not in page
    assert "发送后" not in page


def test_daily_feedback_keeps_publish_copy_confirmed_send_and_opened_apart(flow, same_day):
    case = reviewed(flow)
    teacher(flow)
    link(flow, review(flow, case))
    learning(flow).drain()
    [draft] = reports(flow, "daily")
    assert publish(flow, draft).status_code == 200
    queue = f"/v1/reports?cadence=daily&status=published&period_start={same_day}"
    [published] = flow.client.get(queue).json()["items"]
    assert published["delivery"] == {"links": 0, "copied": False, "sent_confirmed_at": None, "opened_at": None}
    created = flow.client.post(f"/v1/reports/{draft['id']}/share-links", json={"expires_in_days": 7}).json()
    assert created["message_copied_at"] is None and created["sent_confirmed_at"] is None
    [published] = flow.client.get(queue).json()["items"]
    assert published["delivery"]["links"] == 1 and not published["delivery"]["copied"] and published["delivery"]["sent_confirmed_at"] is None
    page = flow.client.get(created["path"], headers={"Authorization": ""}).text
    assert "发布于 " in page and "发送于" not in page and "确认发送记录" not in page
    data_url = f"/v1/report-links/{created['token']}"
    assert flow.client.get(data_url, headers={"Authorization": ""}).json()["delivery"]["sent_confirmed_at"] is None

    copied = flow.client.post(f"/v1/report-share-links/{created['id']}/copied")
    assert copied.status_code == 200 and copied.json()["message_copied_at"]
    assert flow.client.get(queue).json()["items"][0]["delivery"]["sent_confirmed_at"] is None
    assert "确认发送记录" not in flow.client.get(created["path"], headers={"Authorization": ""}).text
    sent = flow.client.post(f"/v1/report-share-links/{created['id']}/confirm-sent", json={"note": "已发妈妈微信"})
    assert sent.status_code == 200 and sent.json()["sent_confirmed_at"] and sent.json()["sent_note"] == "已发妈妈微信"
    again = flow.client.post(f"/v1/report-share-links/{created['id']}/confirm-sent", json={"note": "改一下"}).json()
    assert again["sent_confirmed_at"] == sent.json()["sent_confirmed_at"] and again["sent_note"] == "已发妈妈微信"
    delivery = flow.client.get(queue).json()["items"][0]["delivery"]
    assert delivery["copied"] and delivery["sent_confirmed_at"]
    assert flow.client.get(data_url, headers={"Authorization": ""}).json()["delivery"]["sent_confirmed_at"] == sent.json()["sent_confirmed_at"]
    assert "老师确认发送记录：" in flow.client.get(created["path"], headers={"Authorization": ""}).text

    flow.client.get(created["path"], headers={"Authorization": ""})
    assert flow.client.get(queue).json()["items"][0]["delivery"]["opened_at"]

    other = flow.client.post(f"/v1/reports/{draft['id']}/share-links", json={}).json()
    flow.client.post(f"/v1/report-share-links/{other['id']}/revoke")
    assert flow.client.post(f"/v1/report-share-links/{other['id']}/confirm-sent", json={}).status_code == 409
    assert flow.client.post("/v1/report-share-links/share_missing/copied").status_code == 404


def test_new_report_version_does_not_inherit_old_send_confirmation(flow, same_day):
    case = reviewed(flow)
    teacher(flow)
    link(flow, review(flow, case))
    learning(flow).drain()
    [first] = reports(flow, "daily")
    assert publish(flow, first).status_code == 200
    old_link = flow.client.post(f"/v1/reports/{first['id']}/share-links", json={}).json()
    assert flow.client.post(f"/v1/report-share-links/{old_link['id']}/confirm-sent", json={}).status_code == 200
    review(flow, flow.client.get(f"/v1/cases/{case['id']}").json(), action="replace", verdict="correct")
    learning(flow).drain()
    second = next(row for row in reports(flow, "daily") if row["version"] == 2)
    assert publish(flow, second).status_code == 200
    data_url = f"/v1/report-links/{old_link['token']}"
    current = flow.client.get(data_url, headers={"Authorization": ""}).json()
    assert current["report"]["version"] == 2
    assert current["delivery"]["sent_confirmed_at"] is None
    page = flow.client.get(old_link["path"], headers={"Authorization": ""}).text
    assert "确认发送记录" not in page and "发布于 " in page
    new_link = flow.client.post(f"/v1/reports/{second['id']}/share-links", json={}).json()
    assert flow.client.post(f"/v1/report-share-links/{new_link['id']}/confirm-sent", json={}).status_code == 200
    assert flow.client.get(data_url, headers={"Authorization": ""}).json()["delivery"]["sent_confirmed_at"]


def test_daily_feedback_can_be_requested_for_today_but_not_tomorrow(flow, same_day):
    reviewed(flow)
    body = {"period_start": same_day, "cadence": "daily"}
    assert flow.client.post("/v1/students/student_a/reports", json=body).status_code == 403
    teacher(flow)
    created = flow.client.post("/v1/students/student_a/reports", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["cadence"] == "daily" and created.json()["snapshot"]["coverage"]["homeworks"] == 1
    tomorrow = (date.fromisoformat(same_day) + timedelta(days=1)).isoformat()
    assert flow.client.post("/v1/students/student_a/reports", json={**body, "period_start": tomorrow}).status_code == 422
    assert flow.client.post("/v1/students/student_a/reports", json={**body, "period_start": "2026-13-01"}).status_code == 422
    learning(flow).drain()
    assert len(reports(flow, "daily")) == 1


def test_daily_feedback_tells_a_real_correction_apart_from_a_rejudgment(flow, same_day):
    case = reviewed(flow)
    teacher(flow)
    case = review(flow, case)
    asset = flow.uploaded()
    response = flow.post(f"/v1/cases/{case['id']}/attempts", {
        "expected_revision": case["revision"], "parent_attempt_id": case["current_attempt_id"],
        "question_revision": case["current_question_revision"], "asset_ids": [asset["id"]],
        "occurred_at": "2026-10-17T01:01:00Z", "reported_help": "none_reported"})
    assert response.status_code == 202, response.text
    flow.worker.run_once()
    flow.pipeline.verdict = "correct"
    flow.start_check(flow.client.get(f"/v1/cases/{case['id']}").json())
    flow.worker.run_once()
    case = review(flow, flow.client.get(f"/v1/cases/{case['id']}").json(), verdict="correct")
    assert case["progress"] == "verified_resolved"
    learning(flow).drain()
    [draft] = reports(flow, "daily")
    [mistake] = draft["snapshot"]["mistakes"]
    assert mistake["verdict"] is None and mistake["correction"] == "已订正，老师确认通过"
    assert draft["snapshot"]["school_tasks"]["corrected_resolved"] == 1
