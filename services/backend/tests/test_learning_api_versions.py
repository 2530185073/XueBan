"""Student-scoped graph views and current-version mapping over real HTTP/DB."""
from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401
from test_learning import knowledge, learning, link, prepare_latest_math_curriculum, reviewed
from xueban_backend.correction_models import Entity
from xueban_backend.learning_models import (
    KnowledgeEdge, KnowledgeNode, LearningEvidence, QuestionKnowledgeLink, StudentKnowledgeState,
)
from xueban_backend.learning_projection import published_graph


def test_unreviewed_graph_never_exposes_pilot_nodes_or_details(flow):
    page = knowledge(flow)
    assert page["graph_version"] is None
    assert page["nodes"] == page["edges"] == page["states"] == []
    assert flow.client.get("/v1/students/student_a/knowledge/kp-rational-add-sub").status_code == 503


def test_scoped_nodes_include_unknowns_and_only_edges_with_visible_endpoints(flow):
    prepare_latest_math_curriculum(flow)
    with Session(flow.database.engine) as session:
        graph = published_graph(session)
        base = session.get(KnowledgeNode, (graph.version, "kp-rational-add-sub"))
        refs = deepcopy(base.curriculum_refs)
        refs[0]["grade"] = 8
        session.add(KnowledgeNode(
            graph_version=graph.version, id="kp-other-grade", node_type="skill", parent_id=base.parent_id,
            name="其他年级", definition=None, observable_objective=None, curriculum_refs=refs,
            coverage_facets=[], misconception_codes=[], sort_order=999))
        session.add(KnowledgeEdge(
            graph_version=graph.version, id="edge-other-grade", from_id="kp-other-grade", to_id=base.id,
            relation_type="prerequisite_of", scope=None, rationale=None))
        session.commit()
    page = knowledge(flow)
    assert page["graph_version"] == "test-bnup-math-g7-latest"
    assert page["nodes"] and page["edges"]
    ids = {node["id"] for node in page["nodes"]}
    assert "kp-other-grade" not in ids
    assert all(edge["from_id"] in ids and edge["to_id"] in ids and edge["relation_type"] == "prerequisite_of"
               for edge in page["edges"])
    assert all(node["learning_status"] == "no_judgment" and node["assessment_source"] == "none"
               and node["valid_evidence_count"] == 0 for node in page["nodes"])
    assert all(node["parent_id"] is None or node["parent_id"] in ids for node in page["nodes"])
    target = next(node for node in page["nodes"] if node["id"] == "kp-rational-add-sub")
    assert target["subject"] == "数学" and target["chapter_name"]
    assert flow.client.get("/v1/students/student_a/knowledge/kp-other-grade").status_code == 404


def test_unavailable_catalog_excludes_nodes_even_when_graph_is_reviewed(flow, monkeypatch):
    prepare_latest_math_curriculum(flow)
    from xueban_backend import learning_api
    original = learning_api.scope_payload
    monkeypatch.setattr(learning_api, "scope_payload", lambda scope: {**original(scope), "catalog_status": "unavailable"})
    assert knowledge(flow)["nodes"] == []
    assert knowledge(flow)["edges"] == []
    assert flow.client.get("/v1/students/student_a/knowledge/kp-rational-add-sub").status_code == 503


def test_pending_projection_hides_old_conclusions_and_evidence_without_deleting_them(flow):
    case = reviewed(flow)
    teacher(flow)
    mapping = link(flow, case)
    learning(flow).drain()
    before = knowledge(flow)
    assert not before["projection"]["pending"]
    target = next(node for node in before["nodes"] if node["id"] == "kp-rational-add-sub")
    assert target["learning_status"] == "needs_help_now" and target["model_evidence_count"] == 1
    assert target["assessment_source"] == "model"
    detail_url = "/v1/students/student_a/knowledge/kp-rational-add-sub"
    [evidence] = flow.client.get(detail_url).json()["evidence"]
    assert evidence["question_revision"] == mapping["question_revision"]
    assert evidence["graph_version"] == mapping["graph_version"]
    assert evidence["case_id"] == case["id"] and evidence["source_role"] == "model"

    link(flow, case, status="rejected", expected=mapping["id"])
    pending = knowledge(flow)
    assert pending["projection"]["pending"] and pending["states"] == [] and pending["chapters"] == []
    assert all(node["learning_status"] == "no_judgment" and node["assessment_source"] == "none"
               and node["valid_evidence_count"] == 0 for node in pending["nodes"])
    detail = flow.client.get(detail_url).json()
    assert detail["projection"]["pending"] and detail["state"] is None and detail["evidence"] == []
    assert all(node["learning_status"] == "no_judgment" for node in detail["prerequisites"] + detail["leads_to"])
    with Session(flow.database.engine) as session:
        assert session.get(LearningEvidence, evidence["id"]).validity == "valid"
    learning(flow).drain()
    assert not knowledge(flow)["projection"]["pending"]
    assert flow.client.get(detail_url).json()["state"]["learning_status"] == "no_judgment"


@pytest.mark.parametrize("field,value", [("graph_version", "old-version"), ("rule_version", "old-rule")])
def test_state_row_must_match_current_graph_and_rule_even_if_projection_fingerprint_matches(flow, field, value):
    case = reviewed(flow)
    teacher(flow)
    link(flow, case)
    learning(flow).drain()
    with Session(flow.database.engine) as session:
        state = session.get(StudentKnowledgeState, ("student_a", "kp-rational-add-sub"))
        setattr(state, field, value)
        session.commit()
    assert not knowledge(flow)["projection"]["pending"]
    assert knowledge(flow)["states"] == []
    node = next(node for node in knowledge(flow)["nodes"] if node["id"] == "kp-rational-add-sub")
    assert node["learning_status"] == "no_judgment"
    assert flow.client.get("/v1/students/student_a/knowledge/kp-rational-add-sub").json()["state"] is None


def test_link_revisions_do_not_reuse_old_primary_and_stale_write_is_rejected(flow):
    case = reviewed(flow)
    teacher(flow)
    old = link(flow, case)
    path = f"/v1/questions/{case['question_id']}/knowledge-links"
    with Session(flow.database.engine) as session:
        question = session.get(Entity, case["question_id"])
        question.revision += 1
        question.document = {**question.document, "revision": question.revision}
        session.commit()
    current = flow.client.get(path).json()
    assert current["question_revision"] == 2 and current["graph_version"] == old["graph_version"]
    assert current["items"] == []
    payload = {"knowledge_id": "kp-rational-mul-div", "role": "primary", "status": "confirmed",
               "expected_question_revision": 1, "expected_graph_version": current["graph_version"]}
    assert flow.client.post(path, json=payload).status_code == 409
    payload["expected_question_revision"] = 2
    assert flow.client.post(path, json={**payload, "expected_graph_version": "old-graph"}).status_code == 409
    fresh = flow.client.post(path, json=payload)
    assert fresh.status_code == 201, fresh.text
    assert fresh.json()["question_revision"] == 2
    assert [row["id"] for row in flow.client.get(path).json()["items"]] == [fresh.json()["id"]]
    with Session(flow.database.engine) as session:
        assert session.get(QuestionKnowledgeLink, old["id"]).status == "confirmed"


def test_graph_detail_and_versioned_links_keep_student_authorization(flow):
    case = reviewed(flow)
    teacher(flow)
    link(flow, case)
    learning(flow).drain()
    other = {"Authorization": "Bearer " + flow.signing.token("subject-b")}
    for path in ("/v1/students/student_a/knowledge",
                 "/v1/students/student_a/knowledge/kp-rational-add-sub",
                 f"/v1/questions/{case['question_id']}/knowledge-links"):
        assert flow.client.get(path, headers=other).status_code == 404
    assert flow.client.post(f"/v1/questions/{case['question_id']}/knowledge-links",
                            headers=other, json={"knowledge_id": "kp-rational-add-sub", "role": "primary",
                                                 "status": "confirmed"}).status_code in (403, 404)
