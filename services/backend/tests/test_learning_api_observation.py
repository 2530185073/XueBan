"""The staff editor reads one current observation and never historical AI links."""
from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401
from test_learning import learning
from test_learning_mapping_versions import ai_case
from xueban_backend.correction_models import Entity
from xueban_backend.learning_models import QuestionKnowledgeLink


def prepared(flow):
    case = ai_case(flow)
    learning(flow).drain()
    path = f"/v1/questions/{case['question_id']}/knowledge-links"
    response = flow.client.get(path)
    assert response.status_code == 200, response.text
    [mapping] = response.json()["items"]
    assert mapping["source"] == "model" and mapping["status"] == "confirmed"
    return case, path, mapping


def test_current_observation_is_visible_and_staff_confirmation_keeps_question_scope(flow):
    case, path, mapping = prepared(flow)
    teacher(flow)
    response = flow.client.post(path, json={
        "knowledge_id": mapping["knowledge_id"], "role": "primary", "status": "confirmed",
        "expected_link_id": mapping["id"], "expected_question_revision": 1,
        "expected_graph_version": mapping["graph_version"], "rationale": "老师核对本题知识点",
    })
    assert response.status_code == 201, response.text
    staff = response.json()
    assert staff["source"] == "staff" and staff["supersedes_id"] == mapping["id"]
    with Session(flow.database.engine) as session:
        assert session.get(QuestionKnowledgeLink, staff["id"]).observation_bindings is None
        assert session.get(QuestionKnowledgeLink, mapping["id"]).observation_bindings
    assert [row["id"] for row in flow.client.get(path).json()["items"]] == [staff["id"]]
    # A stale machine card cannot overwrite the newly confirmed teacher link.
    assert flow.client.post(path, json={
        "knowledge_id": mapping["knowledge_id"], "role": "primary", "status": "rejected",
        "expected_link_id": mapping["id"],
    }).status_code == 409


@pytest.mark.parametrize("change", ["reading_revision", "stale_decision", "pointer_revision", "ambiguous_case"])
def test_unsafe_current_pointer_hides_machine_links_without_deleting_history(flow, change):
    case, path, mapping = prepared(flow)
    with Session(flow.database.engine) as session:
        attempt = session.get(Entity, case["current_attempt_id"])
        decision = session.get(Entity, case["current_decision"]["id"])
        saved_case = session.get(Entity, case["id"])
        if change == "reading_revision":
            attempt.document = {**attempt.document, "recognition_revision": 2}
        elif change == "stale_decision":
            decision.document = {**decision.document, "validity": "stale"}
        elif change == "pointer_revision":
            decision.revision += 1
            decision.document = {**decision.document, "revision": decision.revision}
        else:
            duplicate = deepcopy(saved_case.document)
            duplicate["id"] = "case_ambiguous_same_question"
            session.add(Entity(id=duplicate["id"], kind="case", revision=1,
                               homework_id=flow.homework_id, document=duplicate))
        session.commit()
    assert flow.client.get(path).json()["items"] == []
    with Session(flow.database.engine) as session:
        old = session.get(QuestionKnowledgeLink, mapping["id"])
        assert old.status == "confirmed" and old.observation_bindings


def test_uploading_a_new_answer_hides_old_machine_card_until_that_answer_is_mapped(flow):
    case, path, mapping = prepared(flow)
    asset = flow.uploaded()
    response = flow.post(f"/v1/cases/{case['id']}/attempts", {
        "expected_revision": case["revision"], "parent_attempt_id": case["current_attempt_id"],
        "question_revision": case["current_question_revision"], "asset_ids": [asset["id"]],
        "occurred_at": "2026-10-21T00:00:00Z", "reported_help": "none_reported",
    })
    assert response.status_code == 202, response.text
    assert flow.client.get(path).json()["items"] == []
    assert flow.worker.run_once()
    learning(flow).drain()
    [fresh] = flow.client.get(path).json()["items"]
    assert fresh["id"] != mapping["id"] and fresh["knowledge_id"] == mapping["knowledge_id"]
    with Session(flow.database.engine) as session:
        old = session.get(QuestionKnowledgeLink, mapping["id"])
        new = session.get(QuestionKnowledgeLink, fresh["id"])
        assert old.observation_bindings[0]["attempt_id"] == case["current_attempt_id"]
        assert new.observation_bindings[0]["attempt_id"] != case["current_attempt_id"]
        assert old.status == new.status == "confirmed"
