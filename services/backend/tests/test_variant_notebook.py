"""Notebook HTTP regressions: migrated temporary DB, synthetic records, no models."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from xueban_backend.account_models import PasswordAccount
from xueban_backend.app import create_app
from xueban_backend.correction_models import Entity
from xueban_backend.device_models import DeviceSession
from xueban_backend.learning_models import LearningOutbox, PracticeAssessment, PracticeResponse, PracticeSession, VariantGeneration
from xueban_backend.models import Homework, Principal, Student, StudentGrant
from xueban_backend.variant_notebook import install_variant_notebook


STAMP = datetime(2026, 10, 1, 8, tzinfo=timezone.utc)
URL = "/v1/students/student_a/variant-notebook"


@pytest.fixture
def notebook(settings, signing):
    app = create_app(settings)
    # Test the installer independently of when the parallel app integration lands.
    if not any(getattr(route, "path", None) == "/v1/students/{student_id}/variant-notebook" for route in app.routes):
        identity = next(route for route in app.routes if getattr(route, "path", None) == "/v1/me")
        dependencies = {dependency.name: dependency.call for dependency in identity.dependant.dependencies}
        install_variant_notebook(app, dependencies["session"], dependencies["actor"])
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + signing.token()
        yield client


def source(session, key, *, student_id="student_a", verdict="incorrect", answer_status="present",
           occurred_at=STAMP, attempt_time=None, validity="candidate", lifecycle="active", blocked=False):
    student = session.get(Student, student_id)
    homework = Homework(id=f"hw_{key}", tenant_id=student.tenant_id, student_id=student.id,
        campus_id=student.campus_id, revision=1, curriculum={"publisher_code": "historical-book"},
        title=f"作业 {key}", collection_scope="partial", created_at=STAMP, occurred_at=occurred_at,
        processing_blocked=blocked)
    session.add(homework)
    session.flush()
    question = Entity(id=f"question_{key}", homework_id=homework.id, kind="question", revision=1,
                      document={"stem_text": f"原题 {key}", "question_type": "calculation"})
    attempt = Entity(id=f"attempt_{key}", homework_id=homework.id, kind="attempt", revision=1,
        document={"answer_status": answer_status, "occurred_at": attempt_time,
                  "answer_text": None, "case_id": f"case_{key}", "question_id": question.id})
    case = Entity(id=f"case_{key}", homework_id=homework.id, kind="case", revision=1, document={
        "question_id": question.id, "current_attempt_id": attempt.id, "lifecycle": lifecycle,
        "current_decision": {"origin": "model", "validity": validity, "verdict": verdict}})
    session.add_all([question, attempt, case])
    session.flush()
    return case


def generation(session, case, key="one", *, student_id="student_a", status="ready", at=STAMP):
    student = session.get(Student, student_id)
    row = VariantGeneration(id=f"generation_{key}", tenant_id=student.tenant_id, student_id=student.id,
        source_case_id=case.id, source_fingerprint=key, question_type="calculation", question_kind="solution",
        snapshot={}, status=status, audit={}, item_id=f"item_{key}", created_at=at, updated_at=at)
    session.add(row)
    session.flush()
    return row


def activity(session, case, key="one", *, generated=None, student_id="student_a", at=STAMP,
             status="active", purpose="variant"):
    student = session.get(Student, student_id)
    row = PracticeSession(id=f"practice_{key}", tenant_id=student.tenant_id, student_id=student.id,
        purpose=purpose, source_case_id=case.id, knowledge_ids=[],
        item_ids=[generated.item_id if generated else f"item_{key}"], hints_used=[],
        gate={"generation_job_id": generated.id, "no_expiry": True} if generated else {},
        status=status, started_at=at, budget_ends_at=at + timedelta(minutes=10))
    session.add(row)
    if generated:
        generated.session_id = row.id
    session.flush()
    return row


def response(session, row, result="incorrect", *, key="one", at=STAMP, **overrides):
    data = dict(id=f"response_{key}", tenant_id=row.tenant_id, student_id=row.student_id,
        session_id=row.id, item_id=row.item_ids[0], event_type="optional_variant", answer_text="synthetic",
        result=result, hint_used=False, occurred_at=at)
    data.update(overrides)
    answer = PracticeResponse(**data)
    session.add(answer)
    session.flush()
    return answer


def assessment(session, row, status="queued", *, result=None, key="one", at=STAMP, job_status=None, **overrides):
    data = dict(id=f"assessment_{key}", tenant_id=row.tenant_id, student_id=row.student_id,
        session_id=row.id, item_id=row.item_ids[0], item_revision=1, item_digest="synthetic",
        submission={"answer_text": "synthetic"}, status=status, result=result, audit={}, created_at=at, updated_at=at)
    data.update(overrides)
    check = PracticeAssessment(**data)
    if job_status:
        job = LearningOutbox(id=f"job_{key}", tenant_id=row.tenant_id, student_id=row.student_id,
            kind="assess_student_practice", dedupe_key=key, payload={"assessment_id": check.id},
            status=job_status, tries=1, available_at=at, created_at=at)
        session.add(job)
        check.outbox_id = job.id
    session.add(check)
    session.flush()
    return check


def items(client):
    result = client.get(URL)
    assert result.status_code == 200, result.text
    assert result.json()["student_id"] == "student_a"
    return result.json()["items"]


def test_empty_notebook_and_authentication(notebook):
    assert items(notebook) == []
    notebook.headers.pop("Authorization")
    assert notebook.get(URL).status_code == 401


def test_all_history_individual_cases_without_graph_or_top_n_limit(notebook, database):
    with Session(database.engine) as session, session.begin():
        for index in range(35):
            case = source(session, f"history_{index:02}", verdict="partially_correct" if index % 2 else "incorrect",
                          occurred_at=STAMP - timedelta(days=365 + index))
        # Distinct cases sharing a question must not be collapsed into one card.
        session.add(Entity(id="case_same_question", homework_id=case.homework_id, kind="case", revision=1,
                           document=deepcopy(case.document)))
    result = items(notebook)
    assert len(result) == 36 and len({entry["case_id"] for entry in result}) == 36
    assert {entry["status"] for entry in result} == {"uncorrected"}
    assert all(not entry["practiced"] and entry["generation_id"] is None and entry["session_id"] is None for entry in result)
    assert result[0] == {"case_id": "case_history_00", "question_id": "question_history_00",
        "question_text": "原题 history_00", "homework_title": "作业 history_00", "occurred_at": "2025-10-01T08:00:00Z",
        "status": "uncorrected", "practiced": False, "generation_id": None, "generation_status": None, "session_id": None}
    assert items(notebook) == result


def test_only_actual_wrong_cases_exclude_explicit_blank_and_unresolved_decisions(notebook, database):
    with Session(database.engine) as session, session.begin():
        source(session, "wrong")
        source(session, "partial", verdict="partially_correct")
        source(session, "unknown_answer", answer_status="unclear")
        source(session, "unanswered", verdict="unanswered")
        source(session, "blank_wrong", answer_status="blank")
        source(session, "blank_partial", verdict="partially_correct", answer_status="blank")
        source(session, "correct", verdict="correct")
        source(session, "uncertain", verdict="unable_to_judge")
        source(session, "review", validity="needs_review")
        source(session, "stale", validity="stale")
        source(session, "superseded", lifecycle="superseded")
        source(session, "blocked", blocked=True)
        conflict = source(session, "conflict")
        conflict.document = {**conflict.document, "current_decision": {
            **conflict.document["current_decision"], "verification": {"status": "disagreed"}}}
    assert {entry["case_id"] for entry in items(notebook)} == {"case_wrong", "case_partial", "case_unknown_answer", "case_conflict"}


@pytest.mark.parametrize("status,job_status,expected", [
    ("queued", None, "queued"), ("queued", "queued", "queued"),
    ("queued", "running", "generating"), ("generating", "running", "generating"),
    ("verifying", "running", "verifying"), ("queued", "dead", "failed"),
    ("verifying", "dead", "failed"), ("ready", "running", "ready"),
    ("failed", "running", "failed"), ("superseded", "dead", "superseded"),
])
def test_generation_progress_survives_reload_without_changing_practice(notebook, database, status, job_status, expected):
    with Session(database.engine) as session, session.begin():
        case = source(session, "one")
        gen = generation(session, case, status=status)
        if job_status:
            job = LearningOutbox(id="generation_job", tenant_id=gen.tenant_id, student_id=gen.student_id,
                kind="generate_student_variant", dedupe_key="generation", payload={}, status=job_status,
                tries=1, available_at=STAMP, created_at=STAMP)
            session.add(job)
            gen.outbox_id = job.id
    entry = items(notebook)[0]
    assert entry["generation_status"] == expected
    assert entry["status"] == "uncorrected" and not entry["practiced"]
    assert items(notebook)[0] == entry
    with Session(database.engine) as session:
        assert session.get(VariantGeneration, "generation_one").status == status


def test_foreign_queue_cannot_override_owned_generation_progress(notebook, database):
    with Session(database.engine) as session, session.begin():
        gen = generation(session, source(session, "one"), status="queued")
        session.add(LearningOutbox(id="foreign_job", tenant_id=gen.tenant_id, student_id="student_b",
            kind="generate_student_variant", dedupe_key="foreign", payload={}, status="dead",
            tries=1, available_at=STAMP, created_at=STAMP))
        gen.outbox_id = "foreign_job"
    assert items(notebook)[0]["generation_status"] == "queued"


def test_normalized_dates_fallbacks_nullable_text_and_stable_ties(notebook, database):
    with Session(database.engine) as session, session.begin():
        source(session, "offset", attempt_time="2026-10-01T16:00:01+08:00")
        source(session, "late", attempt_time="2026-10-01T09:00:00Z")
        for key, value in (("fallback_a", "bad-date"), ("fallback_b", "2026-10-01T23:00:00"), ("fallback_c", None)):
            source(session, key, attempt_time=value)
        session.get(Homework, "hw_fallback_a").title = None
        session.get(Entity, "question_fallback_a").document = {"stem_text": None}
        session.delete(session.get(Entity, "question_fallback_c"))
    result = items(notebook)
    assert [entry["case_id"] for entry in result] == ["case_late", "case_offset", "case_fallback_c", "case_fallback_b", "case_fallback_a"]
    assert result[1]["occurred_at"] == "2026-10-01T08:00:01Z"
    assert {entry["occurred_at"] for entry in result[2:]} == {"2026-10-01T08:00:00Z"}
    assert result[-1]["question_text"] is None and result[-1]["homework_title"] is None
    assert result[2]["question_text"] is None


@pytest.mark.parametrize("result,expected", [("correct", "correct"), ("incorrect", "needs_practice"),
                                            ("unscorable", "check_failed")])
def test_response_progress_retains_original_wrong_and_read_is_non_mutating(notebook, database, result, expected):
    with Session(database.engine) as session, session.begin():
        case = source(session, "one")
        original = deepcopy(case.document)
        gen = generation(session, case)
        row = activity(session, case, generated=gen, status="completed")
        response(session, row, result)
    statements = []
    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.strip().split()[0].upper())
    event.listen(notebook.app.state.engine, "before_cursor_execute", record)
    try:
        result = items(notebook)
        assert items(notebook) == result
    finally:
        event.remove(notebook.app.state.engine, "before_cursor_execute", record)
    assert len(result) == 1 and result[0]["status"] == expected and result[0]["practiced"] is True
    assert result[0]["generation_id"] == "generation_one" and result[0]["session_id"] == "practice_one"
    assert not set(statements) & {"INSERT", "UPDATE", "DELETE"}
    with Session(database.engine) as session:
        assert session.get(Entity, "case_one").document == original
        assert session.get(PracticeSession, "practice_one").status == "completed"


@pytest.mark.parametrize("status,result,job_status,expected", [
    ("queued", None, None, "submitted"), ("grading", None, "running", "submitted"),
    ("queued", None, "succeeded", "submitted"), ("queued", None, "dead", "check_failed"),
    ("failed", "unscorable", None, "check_failed"), ("superseded", "unscorable", None, "check_failed"),
    ("completed", "correct", None, "correct"), ("completed", "incorrect", None, "needs_practice"),
])
def test_assessment_status_uses_persisted_result_even_when_session_active(notebook, database, status, result, job_status, expected):
    with Session(database.engine) as session, session.begin():
        case = source(session, "one")
        row = activity(session, case, generated=generation(session, case))
        assessment(session, row, status, result=result, job_status=job_status)
    entry = items(notebook)[0]
    assert entry["status"] == expected and entry["practiced"] is True
    with Session(database.engine) as session:
        assert session.get(PracticeSession, "practice_one").status == "active"  # GET never expires history.


def test_latest_submission_wins_over_old_failed_assessment_and_response(notebook, database):
    with Session(database.engine) as session, session.begin():
        case = source(session, "one")
        row = activity(session, case, generated=generation(session, case))
        old = assessment(session, row, "failed", key="old")
        old.updated_at = STAMP + timedelta(days=2)
        response(session, row, "correct", at=STAMP + timedelta(minutes=1))
    assert items(notebook)[0]["status"] == "correct"
    with Session(database.engine) as session, session.begin():
        row = session.get(PracticeSession, "practice_one")
        assessment(session, row, key="new", at=STAMP + timedelta(minutes=2))
    assert items(notebook)[0]["status"] == "submitted"


def test_response_beats_pending_assessment_at_same_timestamp(notebook, database):
    with Session(database.engine) as session, session.begin():
        case = source(session, "one")
        row = activity(session, case, generated=generation(session, case))
        assessment(session, row)
        response(session, row, "correct")
    assert items(notebook)[0]["status"] == "correct"


def test_retry_session_keeps_wrong_result_until_new_submission_without_rewriting_original(notebook, database):
    with Session(database.engine) as session, session.begin():
        case = source(session, "one")
        gen = generation(session, case)
        first = activity(session, case, generated=gen, status="completed")
        response(session, first)
        activity(session, case, "retry", generated=gen, at=STAMP + timedelta(minutes=1))
    entry = items(notebook)[0]
    assert entry["session_id"] == "practice_retry" and entry["status"] == "needs_practice" and entry["practiced"]
    with Session(database.engine) as session, session.begin():
        retry = session.get(PracticeSession, "practice_retry")
        assessment(session, retry, at=STAMP + timedelta(minutes=2))
    assert items(notebook)[0]["status"] == "submitted"
    with Session(database.engine) as session, session.begin():
        check = session.get(PracticeAssessment, "assessment_one")
        check.status, check.result = "completed", "correct"
        response(session, session.get(PracticeSession, "practice_retry"), "correct", key="retry", at=STAMP + timedelta(minutes=3))
    assert items(notebook)[0]["status"] == "correct"
    with Session(database.engine) as session:
        assert session.get(PracticeResponse, "response_one").result == "incorrect"
        assert session.get(Entity, "case_one").document["current_decision"]["verdict"] == "incorrect"


@pytest.mark.parametrize("status", ["queued", "generating", "verifying", "ready", "failed"])
def test_new_generation_has_own_progress_and_keeps_practice_history(notebook, database, status):
    with Session(database.engine) as session, session.begin():
        case = source(session, "one")
        old = generation(session, case, "old")
        response(session, activity(session, case, "old", generated=old, status="completed"), "correct")
        generation(session, case, "new", status=status, at=STAMP + timedelta(minutes=1))
        # Updating old audit data must not make it the newest generated question.
        old.updated_at = STAMP + timedelta(days=1)
    entry = items(notebook)[0]
    assert entry["generation_id"] == "generation_new" and entry["session_id"] is None
    assert entry["status"] == "uncorrected" and entry["practiced"] is True
    if status == "failed":
        detail = notebook.get("/v1/variant-generations/generation_new")
        assert detail.status_code == 200 and detail.json()["status"] == "failed"


def test_generation_ties_are_deterministic_and_saved_session_pointer_wins(notebook, database):
    with Session(database.engine) as session, session.begin():
        case = source(session, "one")
        generation(session, case, "aaa")
        newest = generation(session, case, "zzz")
        activity(session, case, "zzz", generated=newest)
        activity(session, case, "aaa", generated=newest)
    entry = items(notebook)[0]
    assert entry["generation_id"] == "generation_zzz" and entry["session_id"] == "practice_aaa"
    assert entry["status"] == "uncorrected" and entry["practiced"] is False


def test_retry_with_same_timestamp_uses_saved_pointer_and_preserves_wrong_status(notebook, database):
    with Session(database.engine) as session, session.begin():
        case = source(session, "one")
        gen = generation(session, case)
        response(session, activity(session, case, "zzz", generated=gen, status="completed"))
        activity(session, case, "aaa", generated=gen)
    entry = items(notebook)[0]
    assert entry["session_id"] == "practice_aaa" and entry["status"] == "needs_practice"


def test_legacy_variant_progress_included_preview_progress_ignored(notebook, database):
    with Session(database.engine) as session, session.begin():
        case = source(session, "one")
        response(session, activity(session, case), "incorrect")
        response(session, activity(session, case, "preview", purpose="preview", at=STAMP + timedelta(days=1)),
                 "correct", key="preview")
    entry = items(notebook)[0]
    assert entry["generation_id"] is None and entry["session_id"] == "practice_one"
    assert entry["status"] == "needs_practice" and entry["practiced"] is True


@pytest.mark.parametrize("student_id", ["student_ungranted", "student_b", "student_missing"])
def test_notebook_denies_other_students_without_disclosing_existence(notebook, student_id):
    result = notebook.get(f"/v1/students/{student_id}/variant-notebook")
    assert result.status_code == 404 and result.json()["code"] == "not_found"


@pytest.mark.parametrize("change", ["grant", "student", "principal"])
def test_revoked_access_applies_to_every_read(notebook, database, change):
    with Session(database.engine) as session, session.begin():
        source(session, "one")
    assert len(items(notebook)) == 1
    with Session(database.engine) as session, session.begin():
        model, key = {"grant": (StudentGrant, ("actor_a", "student_a")),
                      "student": (Student, "student_a"), "principal": (Principal, "actor_a")}[change]
        session.get(model, key).active = False
    assert notebook.get(URL).status_code == (403 if change == "principal" else 404)


@pytest.mark.parametrize("foreign_student", ["student_ungranted", "student_b"])
def test_foreign_generations_sessions_responses_and_assessments_cannot_pollute_progress(notebook, database, foreign_student):
    with Session(database.engine) as session, session.begin():
        own = source(session, "own")
        foreign = source(session, "foreign", student_id=foreign_student)
        owner = session.get(Student, foreign_student)
        gen = generation(session, own)
        row = activity(session, own, generated=gen)
        # These unconstrained string references deliberately point across owners.
        foreign_gen = generation(session, own, "foreign", student_id=foreign_student, at=STAMP + timedelta(days=1))
        foreign_row = activity(session, own, "foreign", generated=foreign_gen, student_id=foreign_student)
        gen.session_id = foreign_row.id
        response(session, row, "correct", student_id=foreign_student, tenant_id=owner.tenant_id)
        assessment(session, row, "failed", student_id=foreign_student, tenant_id=owner.tenant_id)
        response(session, row, "correct", key="wrong_item", item_id="not_in_session")
        assessment(session, row, "failed", key="wrong_item", item_id="not_in_session")
        own.document = {**own.document, "question_id": foreign.document["question_id"],
                        "current_attempt_id": foreign.document["current_attempt_id"]}
    entry, = items(notebook)
    assert entry["case_id"] == "case_own" and entry["question_text"] is None
    assert entry["generation_id"] == "generation_one" and entry["session_id"] == "practice_one"
    assert entry["status"] == "uncorrected" and entry["practiced"] is False


def test_same_owner_wrong_case_session_pointer_is_ignored(notebook, database):
    with Session(database.engine) as session, session.begin():
        first, second = source(session, "first"), source(session, "second")
        gen = generation(session, first)
        other = activity(session, second)
        gen.session_id = other.id
        response(session, other, "correct")
    entries = {entry["case_id"]: entry for entry in items(notebook)}
    assert entries["case_first"]["session_id"] is None and entries["case_first"]["status"] == "uncorrected"
    assert entries["case_second"]["status"] == "correct"


def test_password_bound_account_cannot_expand_scope_via_an_extra_grant(notebook, database, signing):
    with Session(database.engine) as session, session.begin():
        source(session, "own")
        source(session, "other", student_id="student_ungranted")
        session.add(StudentGrant(principal_id="actor_a", student_id="student_ungranted", tenant_id="tenant_a", active=True))
        session.add(PasswordAccount(username="synthetic.alpha", principal_id="actor_a", student_id="student_a",
            tenant_id="tenant_a", password_hash="unused-synthetic-hash", must_change_password=False, active=True,
            created_at=STAMP, updated_at=STAMP))
        session.add(DeviceSession(id="device_notebook", principal_id="actor_a", issued_at=STAMP,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1), revoked=False))
    notebook.headers["Authorization"] = "Bearer " + signing.token(overrides={"device_session_id": "device_notebook"})
    assert [entry["case_id"] for entry in items(notebook)] == ["case_own"]
    assert notebook.get("/v1/students/student_ungranted/variant-notebook").status_code == 404
    with Session(database.engine) as session, session.begin():
        session.get(PasswordAccount, "synthetic.alpha").active = False
    assert notebook.get(URL).status_code == 401


def test_switching_authenticated_accounts_never_reuses_notebook_data(notebook, database, signing):
    with Session(database.engine) as session, session.begin():
        source(session, "own")
        source(session, "foreign", student_id="student_b")
    assert [entry["case_id"] for entry in items(notebook)] == ["case_own"]
    notebook.headers["Authorization"] = "Bearer " + signing.token(subject="subject-b")
    assert notebook.get(URL).status_code == 404
    other = notebook.get("/v1/students/student_b/variant-notebook")
    assert other.status_code == 200
    assert [entry["case_id"] for entry in other.json()["items"]] == ["case_foreign"]
    notebook.headers["Authorization"] = "Bearer " + signing.token()
    assert [entry["case_id"] for entry in items(notebook)] == ["case_own"]
