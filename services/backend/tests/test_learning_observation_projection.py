"""An R1 change to a correction cannot rewrite the unchanged original answer."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_learning import reviewed
from xueban_backend.correction_models import CorrectionEvent, Entity
from xueban_backend.learning_link_scope import observation_binding
from xueban_backend.learning_models import LearningEvidence, QuestionKnowledgeLink, StudentKnowledgeState
from xueban_backend.learning_projection import project_student
from xueban_backend.learning_rules import RULE_VERSION, derive_evidence


def test_rule_version_fits_persisted_state_contract():
    # SQLite does not enforce VARCHAR length; check the PostgreSQL contract too.
    assert len(RULE_VERSION) <= StudentKnowledgeState.__table__.c.rule_version.type.length


def inputs():
    attempts = [{"id": f"a{n}", "question_id": "q1", "parent_attempt_id": None if n == 1 else "a1",
                 "recognition_revision": 1, "captured_question_revision": 1,
                 "recognition_status": "ready", "answer_status": "answered", "recognition_uncertainties": [],
                 "help_condition": "none_reported", "occurred_at": f"2026-10-21T0{n}:00:00Z"} for n in (1, 2)]
    decisions = [{"id": f"d{n}", "revision": 1, "origin": "model", "validity": "candidate", "verdict": "incorrect",
                  "created_at": f"2026-10-21T0{n}:00:10Z", "input_versions": {
                      "attempt_id": f"a{n}", "question_id": "q1", "question_revision": 1,
                      "attempt_recognition_revision": 1}} for n in (1, 2)]
    decisions[0].update(revision=2, validity="stale", invalidated_by_event_id="new-attempt")
    links = [{"id": f"link-{n}", "question_id": "q1", "question_revision": 1, "graph_version": "g1",
              "knowledge_id": "kp-add", "role": "primary", "source": "model", "status": "confirmed",
              "observation_bindings": [observation_binding(a, d)], "created_at": f"2026-10-21T0{n}:01:00Z"}
             for n, (a, d) in enumerate(zip(attempts, decisions), 1)]
    return {"cases": [{"id": "c1", "question_id": "q1", "homework_id": "h1", "attempt_ids": ["a1", "a2"]}],
            "attempts": attempts, "decisions": decisions, "hints": [], "links": links,
            "questions": {"q1": {"question_id": "q1", "revision": 1}}, "conditions": {},
            "stale_reasons": {"new-attempt": "new_attempt"}, "graph_version": "g1"}


def test_second_attempt_r1_and_new_attribution_leave_first_source_key_unchanged():
    data = inputs()
    before = {e["attempt_id"]: e for e in derive_evidence(**data)}
    assert set(before) == {"a1", "a2"}
    data["attempts"][1]["recognition_revision"] = 2
    data["decisions"][1].update(validity="stale", invalidated_by_event_id="re-recognize")
    data["stale_reasons"]["re-recognize"] = "recognition_recheck"
    d3 = {**deepcopy(data["decisions"][1]), "id": "d3", "validity": "candidate", "created_at": "2026-10-21T03:00:10Z"}
    d3["input_versions"]["attempt_recognition_revision"] = 2
    data["decisions"].append(d3)
    data["links"].extend([
        {**data["links"][1], "id": "retired-2", "status": "candidate", "created_at": "2026-10-21T03:00:00Z"},
        {**data["links"][1], "id": "link-3", "knowledge_id": "kp-divide", "created_at": "2026-10-21T03:01:00Z",
         "observation_bindings": [observation_binding(data["attempts"][1], d3)]},
    ])
    after = {e["attempt_id"]: e for e in derive_evidence(**data)}
    assert after["a1"] == before["a1"]
    assert after["a1"]["source_key"] == before["a1"]["source_key"]
    assert after["a2"]["knowledge_id"] == "kp-divide"
    assert after["a2"]["decision_id"] == "d3"
    assert after["a2"]["source_key"] != before["a2"]["source_key"]
    assert derive_evidence(**data) == derive_evidence(**data)


@pytest.mark.parametrize("basis", ["decision", "legacy_evidence"])
def test_binding_provenance_does_not_create_independent_mastery(basis):
    data = inputs()
    for link in data["links"]:
        link["observation_bindings"][0]["basis"] = basis
    derived = derive_evidence(**data)
    assert len(derived) == 2
    assert all(not e["independent"] and e["source_role"] == "model" for e in derived)
    data["conditions"] = {"a1": "independent", "a2": "independent"}
    first, correction = derive_evidence(**data)
    assert first["independent"] is True
    assert correction["independent"] is False


def test_unbound_legacy_and_other_authoritative_decision_are_not_reused():
    data = inputs()
    data["links"][0]["observation_bindings"] = None
    data["decisions"].append({**data["decisions"][1], "id": "d3", "created_at": "2026-10-21T03:00:00Z"})
    assert derive_evidence(**data) == []


def test_sqlite_reprojection_keeps_first_evidence_id_and_raw_document_after_second_r1(flow):
    case = reviewed(flow)
    graph = "test-bnup-math-g7-latest"
    timestamp = datetime(2026, 10, 21, tzinfo=timezone.utc)

    def mapping(identifier, attempt, decision, *, skill="kp-rational-add-sub", status="confirmed", stamp=timestamp):
        return QuestionKnowledgeLink(
            id=identifier, tenant_id="tenant_a", student_id="student_a", homework_id=flow.homework_id,
            question_id=case["question_id"], question_revision=1, graph_version=graph, knowledge_id=skill,
            source="model", role="primary", status=status, created_at=stamp,
            observation_bindings=[observation_binding(attempt, decision)])

    with Session(flow.database.engine) as session:
        a1 = session.get(Entity, case["current_attempt_id"])
        d1 = session.get(Entity, case["current_decision"]["id"])
        session.add(mapping("scope-link-a1", a1, d1))
        session.flush()
        project_student(session, "tenant_a", "student_a")
        session.commit()
        [first] = session.scalars(select(LearningEvidence).where(LearningEvidence.validity == "valid")).all()
        first_id, first_key, first_raw = first.id, first.source_key, deepcopy(first.document)
        # Preserve historical raw-version labels during a later rule reproject.
        first_raw["rule_version"] = "r1.4-versioned-knowledge"
        first.document = deepcopy(first_raw)
        session.commit()

        a2_doc = {**deepcopy(a1.document), "id": "scope-a2", "parent_attempt_id": a1.id,
                  "occurred_at": "2026-10-21T01:00:00Z"}
        d2_doc = {**deepcopy(d1.document), "id": "scope-d2", "created_at": "2026-10-21T01:00:10Z"}
        d2_doc["input_versions"]["attempt_id"] = a2_doc["id"]
        a2 = Entity(id=a2_doc["id"], homework_id=flow.homework_id, kind="attempt", revision=1, document=a2_doc)
        d2 = Entity(id=d2_doc["id"], homework_id=flow.homework_id, kind="decision", revision=1, document=d2_doc)
        session.add_all([a2, d2, mapping("scope-link-a2", a2, d2)])
        d1.revision += 1
        d1.document = {**d1.document, "revision": d1.revision, "validity": "stale", "invalidated_by_event_id": "scope-new-attempt"}
        session.add(CorrectionEvent(id="scope-new-attempt", homework_id=flow.homework_id, aggregate_id=d1.id,
                                    aggregate_revision=d1.revision, kind="evidence.invalidated",
                                    payload={"reason": "new_attempt"}, created_at=timestamp))
        case_row = session.get(Entity, case["id"])
        case_row.document = {**case_row.document, "attempt_ids": [a1.id, a2.id], "current_attempt_id": a2.id,
                             "current_decision": deepcopy(d2.document)}
        session.flush()
        project_student(session, "tenant_a", "student_a")
        session.commit()
        valid = session.scalars(select(LearningEvidence).where(LearningEvidence.validity == "valid")).all()
        assert len(valid) == 2
        assert session.get(LearningEvidence, first_id).source_key == first_key
        assert session.get(LearningEvidence, first_id).document == first_raw

        # Only the newer attempt's recognition and attribution change.
        a2.revision += 1
        a2.document = {**a2.document, "revision": a2.revision, "recognition_revision": 2}
        d2.revision += 1
        d2.document = {**d2.document, "revision": d2.revision, "validity": "stale", "invalidated_by_event_id": "scope-r1"}
        d3_doc = {**deepcopy(d2.document), "id": "scope-d3", "validity": "candidate", "created_at": "2026-10-21T02:00:10Z"}
        d3_doc["revision"] = 1
        d3_doc["input_versions"]["attempt_recognition_revision"] = 2
        d3 = Entity(id=d3_doc["id"], homework_id=flow.homework_id, kind="decision", revision=1, document=d3_doc)
        session.add_all([d3, mapping("scope-link-a2-new", a2, d3, skill="kp-rational-mul-div")])
        case_row.document = {**case_row.document, "current_decision": deepcopy(d3.document)}
        session.flush()
        result = project_student(session, "tenant_a", "student_a")
        assert result["added"] == result["invalidated"] == 1
        session.commit()
        for _ in range(2):
            repeated = project_student(session, "tenant_a", "student_a")
            assert repeated["added"] == repeated["invalidated"] == 0
            session.commit()
            unchanged = session.get(LearningEvidence, first_id)
            assert unchanged.validity == "valid"
            assert unchanged.source_key == first_key
            assert unchanged.document == first_raw
        valid = session.scalars(select(LearningEvidence).where(LearningEvidence.validity == "valid")).all()
        assert {(e.document["attempt_id"], e.knowledge_id) for e in valid} == {
            (a1.id, "kp-rational-add-sub"), (a2.id, "kp-rational-mul-div")}
