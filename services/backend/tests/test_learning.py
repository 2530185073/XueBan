"""Personal knowledge graph end to end: reviewed corrections -> evidence -> state."""
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401  (pytest fixture reuse)
from xueban_backend.governance import purge_student
from xueban_backend.asset_store import LocalAssetStore
from xueban_backend.learning_jobs import LearningWorker
from xueban_backend.learning_models import LearningEvidence, LearningOutbox, QuestionKnowledgeLink, StudentKnowledgeState
from xueban_backend.providers.ark import Metadata


def learning(flow, client=None):
    return LearningWorker(flow.database.engine, client=client)


def reviewed(flow, verdict="incorrect"):
    case = flow.ready()
    flow.pipeline.verdict = verdict
    flow.start_check(case)
    assert flow.worker.run_once()
    return flow.client.get(f"/v1/cases/{case['id']}").json()


def review(flow, case, action="confirm", verdict=None):
    decision = case["current_decision"]
    attempt = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    body = {"expected_revision": case["revision"], "target_decision_id": decision["id"] if decision else None,
            "target_decision_revision": decision["revision"] if decision else None, "action": action,
            "verdict": verdict or (decision["verdict"] if decision else "incorrect"), "reason": "老师核对原图",
            "basis": [{"kind": "staff_observation", "summary": "已核对原图及过程。", "source_ref": None, "regions": []}],
            "attempt_id": attempt["id"], "question_revision": case["current_question_revision"],
            "attempt_recognition_revision": attempt["recognition_revision"]}
    response = flow.post(f"/v1/cases/{case['id']}/reviews", body)
    assert response.status_code == 201, response.text
    return flow.client.get(f"/v1/cases/{case['id']}").json()


def link(flow, case, knowledge_id="kp-rational-add-sub", role="primary", status="confirmed", expected=None):
    response = flow.client.post(f"/v1/questions/{case['question_id']}/knowledge-links",
                                json={"knowledge_id": knowledge_id, "role": role, "status": status, "expected_link_id": expected})
    assert response.status_code == 201, response.text
    return response.json()


def knowledge(flow):
    response = flow.client.get("/v1/students/student_a/knowledge")
    assert response.status_code == 200, response.text
    return response.json()


def evidence_count(flow, validity="valid"):
    with Session(flow.database.engine) as session:
        return session.scalar(select(func.count()).select_from(LearningEvidence).where(LearningEvidence.validity == validity))


def test_published_graph_is_readable(flow):
    graph = flow.client.get("/v1/curriculum/graph").json()
    assert graph["review_status"] == "pilot_seed_unreviewed"
    ids = {n["id"] for n in graph["nodes"]}
    assert {"kp-remove-brackets", "kp-linear-eq-brackets", "ch-7a-05"} <= ids
    assert all(e["from_id"] in ids and e["to_id"] in ids for e in graph["edges"])


def test_model_candidate_never_becomes_evidence(flow):
    case = reviewed(flow)
    teacher(flow)
    link(flow, case)
    learning(flow).drain()
    assert evidence_count(flow) == 0
    assert knowledge(flow)["states"] == []


def test_staff_confirmed_error_with_confirmed_link_is_evidence_once(flow):
    case = reviewed(flow)
    teacher(flow)
    case = review(flow, case)
    # A staff-confirmed answer without a confirmed knowledge link proves nothing about a node.
    learning(flow).drain()
    assert evidence_count(flow) == 0
    queue = flow.client.get("/v1/knowledge-link-queue").json()["items"]
    assert [item["case_id"] for item in queue] == [case["id"]]
    link(flow, case)
    learning(flow).drain()
    learning(flow).drain()
    assert evidence_count(flow) == 1
    body = knowledge(flow)
    assert body["projection"]["pending"] is False
    [state] = body["states"]
    assert state["knowledge_id"] == "kp-rational-add-sub"
    assert state["learning_status"] == "needs_help_now"
    assert state["reason_codes"] == ["single_error_needs_diagnosis"]
    assert body["chapters"][0]["chapter_id"] == "ch-7a-02"
    detail = flow.client.get("/v1/students/student_a/knowledge/kp-rational-add-sub").json()
    assert detail["evidence"][0]["question_text"] == "1 + 1 = ?"
    assert detail["evidence"][0]["assistance_level"] == "unknown"
    assert {p["id"] for p in detail["prerequisites"]} == {"kp-opposite-abs"}
    assert flow.client.get("/v1/knowledge-link-queue").json()["items"] == []


def test_staff_replace_supersedes_evidence_and_recomputes(flow):
    case = reviewed(flow)
    teacher(flow)
    case = review(flow, case)
    link(flow, case)
    learning(flow).drain()
    review(flow, case, action="replace", verdict="correct")
    learning(flow).drain()
    assert evidence_count(flow) == 1
    assert evidence_count(flow, "invalidated") == 1
    [state] = knowledge(flow)["states"]
    assert state["learning_status"] == "no_judgment"
    assert state["reason_codes"] == ["assisted_or_unverified_only"]
    staff_view = flow.client.get("/v1/students/student_a/knowledge/kp-rational-add-sub?include_invalidated=true").json()
    assert [e["invalidation_reason"] for e in staff_view["evidence"]].count("superseded") == 1


def test_secondary_link_gets_no_negative_evidence_and_independence_needs_staff(flow):
    case = reviewed(flow)
    teacher(flow)
    case = review(flow, case)
    link(flow, case, "kp-rational-add-sub", "primary")
    link(flow, case, "kp-rational-compare", "secondary")
    response = flow.client.post(f"/v1/attempts/{case['current_attempt_id']}/condition",
                                json={"assistance_level": "independent", "expected_condition_id": None})
    assert response.status_code == 201, response.text
    stale = flow.client.post(f"/v1/attempts/{case['current_attempt_id']}/condition",
                             json={"assistance_level": "unknown", "expected_condition_id": None})
    assert stale.status_code == 409
    learning(flow).drain()
    states = {s["knowledge_id"]: s for s in knowledge(flow)["states"]}
    assert states["kp-rational-add-sub"]["learning_status"] == "needs_help_now"
    assert states["kp-rational-add-sub"]["independent_evidence_count"] == 1
    assert states["kp-rational-compare"]["learning_status"] == "no_judgment"
    assert states["kp-rational-compare"]["scorable_evidence_count"] == 0


def test_diagnosis_requires_valid_evidence_and_rejected_link_withdraws(flow):
    case = reviewed(flow)
    teacher(flow)
    case = review(flow, case)
    current = link(flow, case)
    learning(flow).drain()
    evidence_id = flow.client.get("/v1/students/student_a/knowledge/kp-rational-add-sub").json()["evidence"][0]["id"]
    bad = flow.client.post("/v1/students/student_a/knowledge/kp-rational-add-sub/diagnoses",
                           json={"kind": "verified_weak_scope", "scope_text": "负数加法符号", "evidence_ids": [evidence_id]})
    assert bad.status_code == 422
    made = flow.client.post("/v1/students/student_a/knowledge/kp-rational-add-sub/diagnoses",
                            json={"kind": "suspected_cause", "scope_text": "可能是异号相加取符号", "evidence_ids": [evidence_id]})
    assert made.status_code == 201, made.text
    learning(flow).drain()
    [state] = knowledge(flow)["states"]
    assert state["diagnoses"][0]["kind"] == "suspected_cause"
    assert all(q["code"] != "cause_not_diagnosed" for q in state["open_questions"])
    link(flow, case, status="rejected", expected=current["id"])
    learning(flow).drain()
    assert evidence_count(flow) == 0
    assert evidence_count(flow, "invalidated") == 1


def test_learning_endpoints_isolate_students_and_writes_need_staff(flow):
    case = reviewed(flow)
    assert flow.client.get("/v1/students/student_b/knowledge").status_code == 404
    assert flow.client.post(f"/v1/questions/{case['question_id']}/knowledge-links",
                            json={"knowledge_id": "kp-rational-add-sub", "role": "primary", "status": "confirmed",
                                  "expected_link_id": None}).status_code == 403
    teacher(flow)
    chapter = flow.client.post(f"/v1/questions/{case['question_id']}/knowledge-links",
                               json={"knowledge_id": "ch-7a-02", "role": "primary", "status": "confirmed", "expected_link_id": None})
    assert chapter.status_code == 422
    profile = flow.client.put("/v1/students/student_a/learning-profile",
                              json={"expected_revision": 0, "grade": 7, "term": "upper", "current_knowledge_ids": ["kp-linear-eq-brackets"]})
    assert profile.status_code == 200, profile.text
    assert flow.client.put("/v1/students/student_a/learning-profile", json={"expected_revision": 0}).status_code == 409


class FakeClient:
    def __init__(self, links):
        self.settings = SimpleNamespace(enabled=True)
        self.links = links
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        assert "1 + 1" not in kwargs["instructions"]
        return SimpleNamespace(data={"links": self.links}, metadata=Metadata("m", "m", None, None, None, 1, ()))


def test_model_proposes_candidate_links_only_after_staff_decision(flow):
    case = reviewed(flow)
    client = FakeClient([{"knowledge_id": "kp-rational-add-sub", "role": "primary", "rationale": "整数加法"},
                         {"knowledge_id": "kp-rational-compare", "role": "primary", "rationale": "比较"}])
    learning(flow, client).drain()
    assert client.calls == []
    teacher(flow)
    review(flow, case)
    learning(flow, client).drain()
    assert len(client.calls) == 1
    allowed = client.calls[0]["output_schema"]["properties"]["links"]["items"]["properties"]["knowledge_id"]["enum"]
    assert "ch-7a-02" not in allowed and "kp-rational-add-sub" in allowed
    links = flow.client.get(f"/v1/questions/{case['question_id']}/knowledge-links").json()["items"]
    assert {(l["knowledge_id"], l["role"], l["status"]) for l in links} == {
        ("kp-rational-add-sub", "primary", "candidate"), ("kp-rational-compare", "secondary", "candidate")}
    assert evidence_count(flow) == 0
    learning(flow, client).drain()
    assert len(client.calls) == 1


def test_student_deletion_purges_learning_records(flow):
    case = reviewed(flow)
    teacher(flow)
    case = review(flow, case)
    link(flow, case)
    learning(flow).drain()
    with Session(flow.database.engine) as session:
        purge_student(session, LocalAssetStore(flow.cfg.asset_storage_dir), "tenant_a", "student_a")
        session.commit()
        for model in (LearningEvidence, StudentKnowledgeState, QuestionKnowledgeLink, LearningOutbox):
            assert session.scalar(select(func.count()).select_from(model)) == 0
