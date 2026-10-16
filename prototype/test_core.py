from datetime import datetime, timedelta, timezone
import pytest
from .core import Kernel, DomainError
from .api import ACTORS


@pytest.fixture
def k():
    return Kernel()


def recorded(k, question="dist-a", correct=False, days=10, student="lin", assistance="none", stage="assessment"):
    actor = ACTORS["teacher-east"]
    rev = k.students[student]["revision"]
    a = k.submit_attempt(actor, student, f"a-{rev}", rev, question, "synthetic", (datetime.now(timezone.utc)-timedelta(days=days)).isoformat(), stage, assistance)
    r = k.review(actor, a["id"], f"r-{rev}", rev+1, correct, True, "teacher verified")
    return a, r


def node(k, student="lin", name="distribution"):
    return next(n for n in k.graph(ACTORS["teacher-east"], student)["nodes"] if n["id"] == name)


def test_pending_evidence_does_not_change_mastery(k):
    k.submit_attempt(ACTORS["teacher-east"], "lin", "pending", 0, "dist-a", "bad", datetime.now(timezone.utc).isoformat())
    assert node(k)["state"] == "insufficient_evidence"


def test_verified_error_then_distinct_dates_and_templates(k):
    recorded(k)
    recorded(k, "dist-b", True, 8)
    assert node(k)["state"] == "consolidating"
    recorded(k, "dist-d", True, 1)
    assert node(k)["state"] == "stable_for_now"
    assert node(k)["delayed_verified"]


def test_hint_or_correction_does_not_prove_independent_mastery(k):
    recorded(k, correct=True, assistance="hint")
    recorded(k, "dist-b", True, 1, stage="correction")
    assert node(k)["state"] == "awaiting_independent_check"
    assert node(k)["independent_count"] == 0


def test_repeat_identical_question_does_not_inflate_evidence(k):
    recorded(k, correct=True, days=5)
    recorded(k, correct=True, days=1)
    assert node(k)["independent_count"] == 1


def test_same_template_cluster_cannot_establish_broad_mastery(k):
    recorded(k, correct=True, days=5)
    recorded(k, "dist-c", True, 1)
    assert node(k)["state"] == "consolidating"


def test_cross_date_does_not_imply_delayed_retention(k):
    recorded(k, correct=True, days=2)
    recorded(k, "dist-b", True, 1)
    assert node(k)["state"] == "stable_for_now"
    assert not node(k)["delayed_verified"]


def test_corrected_error_rebuilds_graph_and_keeps_audit(k):
    a, r = recorded(k, correct=True)
    recorded(k, "dist-b", True, 1)
    assert node(k)["state"] == "stable_for_now"
    k.review(ACTORS["teacher-east"], a["id"], "fix", 4, False, True, "earlier review wrong", r["id"])
    assert node(k)["state"] == "consolidating"
    assert len(k.reviews) == 3


def test_retracted_review_removed_from_projection(k):
    a, r = recorded(k)
    k.review(ACTORS["teacher-east"], a["id"], "withdraw", 2, False, False, "invalid evidence", r["id"], True)
    assert node(k)["state"] == "insufficient_evidence"


@pytest.mark.parametrize("actor,student", [("teacher-east", "west"), ("teacher-east", "other"), ("parent-lin", "yu"), ("student-lin", "yu")])
def test_tenant_campus_and_student_scope(k, actor, student):
    with pytest.raises(DomainError) as err:
        k.authorize(ACTORS[actor], student)
    assert err.value.status == 404


def test_coach_cannot_write_academic_review(k):
    a, _ = recorded(k)
    with pytest.raises(DomainError) as err:
        k.review(ACTORS["coach-east"], a["id"], "no", 2, True, True, "not allowed")
    assert err.value.status == 403


def test_idempotency_replay_and_payload_conflict(k):
    at = datetime.now(timezone.utc).isoformat()
    args = (ACTORS["teacher-east"], "lin", "same", 0, "dist-a", "1", at)
    first = k.submit_attempt(*args)
    assert k.submit_attempt(*args) == first
    assert len(k.attempts) == 1
    with pytest.raises(DomainError) as err:
        k.submit_attempt(ACTORS["teacher-east"], "lin", "same", 1, "dist-a", "2", at)
    assert err.value.code == "idempotency_payload_conflict"


def test_stale_revision_rejected(k):
    recorded(k)
    with pytest.raises(DomainError) as err:
        k.plan(ACTORS["teacher-east"], "lin", "plan", 0)
    assert err.value.code == "student_revision_conflict"


def test_plans_differ_for_individuals_and_respect_budget(k):
    recorded(k, student="lin")
    recorded(k, question="sign-a", student="yu")
    a = k.plan(ACTORS["teacher-east"], "lin", "p", 2, 10)
    b = k.plan(ACTORS["teacher-east"], "yu", "p", 2, 10)
    assert a["tasks"][0]["node_id"] != b["tasks"][0]["node_id"]
    assert a["allocated_minutes"] <= 10


def test_draft_hidden_from_parent_and_stale_report_blocked(k):
    now = datetime.now(timezone.utc)
    report = k.report(ACTORS["teacher-east"], "lin", "report", 0, (now-timedelta(days=15)).isoformat(), now.isoformat())
    with pytest.raises(DomainError):
        k.read_report(ACTORS["parent-lin"], report["id"])
    recorded(k)
    with pytest.raises(DomainError) as err:
        k.publish(ACTORS["teacher-east"], report["id"], "pub", 2)
    assert err.value.code == "stale_report_regenerate_required"


def test_publish_then_correction_marks_report_stale(k):
    a, r = recorded(k)
    now = datetime.now(timezone.utc)
    report = k.report(ACTORS["teacher-east"], "lin", "report", 2, (now-timedelta(days=15)).isoformat(), now.isoformat())
    k.publish(ACTORS["teacher-east"], report["id"], "pub", 2)
    assert k.read_report(ACTORS["parent-lin"], report["id"])["status"] == "published"
    k.review(ACTORS["teacher-east"], a["id"], "fix", 2, True, True, "corrected", r["id"])
    assert k.read_report(ACTORS["parent-lin"], report["id"])["stale"]


def test_no_observation_implies_no_habit_improvement(k):
    context = k.context(ACTORS["teacher-east"], "lin")
    assert context["verified_observations"] == []
    assert context["student_agent"]["id"] != k.context(ACTORS["teacher-east"], "yu")["student_agent"]["id"]


def test_future_attempt_rejected(k):
    with pytest.raises(DomainError) as err:
        k.submit_attempt(ACTORS["teacher-east"], "lin", "future", 0, "dist-a", "0", (datetime.now(timezone.utc)+timedelta(days=1)).isoformat())
    assert err.value.code == "future_attempt"


def test_cross_midnight_minute_cannot_prove_stability_or_delay(k):
    recorded(k, days=10)
    t = (datetime.now(timezone.utc)-timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    for q, at in [("dist-b", t-timedelta(seconds=30)), ("dist-d", t+timedelta(seconds=30))]:
        rev = k.students["lin"]["revision"]
        a = k.submit_attempt(ACTORS["teacher-east"], "lin", q, rev, q, "correct", at.isoformat(), "assessment")
        k.review(ACTORS["teacher-east"], a["id"], "r"+q, rev+1, True, True, "checked")
    assert node(k)["state"] == "consolidating"
    assert not node(k)["delayed_verified"]


def test_report_end_boundary_is_exclusive(k):
    end = datetime.now(timezone.utc)-timedelta(days=1)
    a = k.submit_attempt(ACTORS["teacher-east"], "lin", "at-end", 0, "dist-a", "0", end.isoformat(), "assessment")
    k.review(ACTORS["teacher-east"], a["id"], "r", 1, False, True, "checked")
    report = k.report(ACTORS["teacher-east"], "lin", "report", 2, (end-timedelta(days=15)).isoformat(), end.isoformat())
    assert report["snapshot"]["period_attempt_count"] == 0
    assert sum(n["independent_count"] for n in report["snapshot"]["knowledge_as_of_period_end"]) == 0


def test_publish_replay_reflects_current_stale_state(k):
    a, r = recorded(k)
    now = datetime.now(timezone.utc)
    report = k.report(ACTORS["teacher-east"], "lin", "report", 2, (now-timedelta(days=15)).isoformat(), now.isoformat())
    k.publish(ACTORS["teacher-east"], report["id"], "publish", 2)
    k.review(ACTORS["teacher-east"], a["id"], "correction", 2, True, True, "changed", r["id"])
    assert k.publish(ACTORS["teacher-east"], report["id"], "publish", 2)["stale"]


def test_projection_response_cannot_mutate_shared_graph(k):
    graph = k.graph(ACTORS["teacher-east"], "lin")
    next(n for n in graph["nodes"] if n["id"] == "distribution")["prerequisites"].clear()
    assert node(k, "yu")["prerequisites"] == ["sign"]


def test_nonindependent_conflicting_evidence_is_visible(k):
    recorded(k, correct=True, days=10)
    recorded(k, "dist-b", True, 8)
    recorded(k, "dist-d", False, 1, stage="homework")
    assert node(k)["conflicting_evidence"]
    plan = k.plan(ACTORS["teacher-east"], "lin", "plan", 6)
    assert plan["tasks"][0]["reason"] == "conflicting_evidence"


@pytest.mark.parametrize("timestamp", ["garbage", "2026-01-01T10:00:00"])
def test_invalid_timestamp_is_domain_validation(k, timestamp):
    with pytest.raises(DomainError) as err:
        k.submit_attempt(ACTORS["teacher-east"], "lin", "bad", 0, "dist-a", "0", timestamp)
    assert err.value.status == 422


def test_later_period_learning_does_not_invalidate_historical_report(k):
    now = datetime.now(timezone.utc)
    end = now-timedelta(days=5)
    report = k.report(ACTORS["teacher-east"], "lin", "history", 0, (end-timedelta(days=15)).isoformat(), end.isoformat())
    recorded(k, days=1)
    assert not k.reports[report["id"]]["stale"]
    published = k.publish(ACTORS["teacher-east"], report["id"], "pub-history", 2)
    assert published["status"] == "published"
    recorded(k, question="dist-b", correct=True, days=0)
    assert not k.read_report(ACTORS["parent-lin"], report["id"])["stale"]


def test_late_import_before_cutoff_invalidates_historical_report(k):
    now = datetime.now(timezone.utc)
    end = now-timedelta(days=5)
    report = k.report(ACTORS["teacher-east"], "lin", "history", 0, (end-timedelta(days=15)).isoformat(), end.isoformat())
    recorded(k, days=10)
    assert k.reports[report["id"]]["stale"]
