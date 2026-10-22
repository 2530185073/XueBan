"""Practice content is pinned to its graph, not a globally reusable node ID."""
from types import SimpleNamespace

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from conftest import migrate
from test_correction_flow import flow, teacher  # noqa: F401
from test_curriculum_binding import choose, publish_book
from test_learning import learning, prepare_latest_math_curriculum, reviewed
from test_practice import ItemClient, clock, open_window, start  # noqa: F401
from xueban_backend.correction_models import Entity
from xueban_backend.learning_models import (KnowledgeNode, LearningOutbox, PracticeItem, PracticeResponse,
                                          PracticeSession, StudentLearningProfile)
from xueban_backend.models import Homework
from xueban_backend.practice import generate_practice, pick_items


def create(flow, **context):
    return flow.client.post("/v1/practice-items", json={
        "knowledge_id": "test-pep-add", "stem_text": "计算 2+3", "answer_kind": "number",
        "answer_values": ["5"], "solution_text": "2+3=5", **context,
    })


def selected(flow):
    prepare_latest_math_curriculum(flow)
    publish_book(flow)
    teacher(flow)
    choose(flow, "test-pep-book-v1")


def test_create_generate_accept_explicit_book_and_queue_pins_it_across_selection_change(flow):
    selected(flow)
    # Old clients retain the legacy default; new clients identify the book.
    assert create(flow).status_code == 422
    created = create(flow, student_id="student_a")
    assert created.status_code == 201, created.text
    assert created.json()["graph_version"] == "test-pep-book-v1"
    queued = flow.client.post("/v1/practice-items/generate", json={
        "knowledge_id": "test-pep-add", "student_id": "student_a", "count": 1})
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["job_id"]
    publish_book(flow, "test-pep-book-v2")
    choose(flow, "test-pep-book-v2")
    client = ItemClient([])
    learning(flow, client).drain()
    assert len(client.calls) == 1 and '"graph_version": "test-pep-book-v1"' in client.calls[0]["text"]
    status = flow.client.get(f"/v1/practice-generations/{job_id}").json()
    assert status["status"] == "succeeded" and status["graph_version"] == "test-pep-book-v1"
    with Session(flow.database.engine) as session:
        job = session.get(LearningOutbox, job_id)
        assert job.payload["graph_version"] == "test-pep-book-v1"
        generated = session.get(PracticeItem, status["candidate_item_ids"][0])
        assert generated.graph_version == "test-pep-book-v1"
    assert flow.client.get("/v1/practice-items?student_id=student_a").json()["items"] == []
    old = flow.client.get("/v1/practice-items?graph_version=test-pep-book-v1").json()["items"]
    assert [item["id"] for item in old] == status["candidate_item_ids"]
    assert create(flow, student_id="student_a", graph_version="test-pep-book-v1").status_code == 409


def test_same_knowledge_id_in_two_graphs_never_crosses_item_selection_or_counts(flow):
    selected(flow)
    old = create(flow, graph_version="test-pep-book-v1").json()
    publish_book(flow, "test-pep-book-v2")
    choose(flow, "test-pep-book-v2")
    with Session(flow.database.engine) as session:
        assert pick_items(session, "tenant_a", "student_a", ["test-pep-add"], 2) == []
    new = create(flow, graph_version="test-pep-book-v2").json()
    with Session(flow.database.engine) as session:
        assert [item.id for item in pick_items(session, "tenant_a", "student_a", ["test-pep-add"], 2)] == [new["id"]]
    current = flow.client.get("/v1/practice-items?student_id=student_a&status=approved").json()["items"]
    assert [item["id"] for item in current] == [new["id"]]
    assert old["id"] not in {item["id"] for item in current}


def test_old_bnup_error_keeps_history_and_cannot_supply_new_pep_practice(flow):
    case = reviewed(flow)
    with Session(flow.database.engine) as session:
        row = session.get(Entity, case["id"])
        row.document = {**row.document, "current_decision": {
            **row.document["current_decision"], "knowledge_points": ["有理数的加减法"]}}
        session.commit()
    publish_book(flow)
    teacher(flow)
    choose(flow, "test-pep-book-v1")
    assert create(flow, student_id="student_a").status_code == 201
    body = flow.client.get("/v1/students/student_a/variant-practice").json()
    assert not any(group["knowledge_id"] == "test-pep-add" for group in body["groups"])
    [historical] = body["groups"]
    assert historical["curriculum_status"] == "historical_unmatched"
    assert historical["graph_version"] is None and historical["available_items"] == 0
    assert historical["questions"][0]["source_curriculum"]["publisher_code"] == "BNUP"
    blocked = start(flow, purpose="variant", knowledge_id="test-pep-add", source_case_id=case["id"])
    assert blocked.status_code == 409 and blocked.json()["code"] == "curriculum_mismatch"
    generated = flow.client.post("/v1/practice-items/generate", json={
        "knowledge_id": "test-pep-add", "source_case_id": case["id"]})
    assert generated.status_code == 409 and generated.json()["code"] == "curriculum_mismatch"


def test_new_pep_error_can_practice_and_answer_history_keeps_original_version(flow, clock):
    case = reviewed(flow)
    publish_book(flow)
    teacher(flow)
    choose(flow, "test-pep-book-v1")
    with Session(flow.database.engine) as session:
        homework = session.get(Homework, flow.homework_id)
        homework.curriculum = {**homework.curriculum, "publisher_code": "PEP", "edition": "isbn:9787107382468"}
        row = session.get(Entity, case["id"])
        row.document = {**row.document, "current_decision": {
            **row.document["current_decision"], "knowledge_points": ["test-pep-add"]}}
        session.commit()
    item = create(flow, student_id="student_a").json()
    started = start(flow, purpose="variant", knowledge_id="test-pep-add", source_case_id=case["id"], count=1)
    assert started.status_code == 201, started.text
    body = started.json()
    assert body["graph_version"] == "test-pep-book-v1" and body["items"][0]["id"] == item["id"]
    publish_book(flow, "test-pep-book-v2")
    choose(flow, "test-pep-book-v2")
    answered = flow.client.post(f"/v1/practice-sessions/{body['id']}/responses",
                               json={"item_id": item["id"], "answer_text": "5"})
    assert answered.status_code == 201, answered.text
    with Session(flow.database.engine) as session:
        response = session.scalar(select(PracticeResponse).where(PracticeResponse.session_id == body["id"]))
        assert response.graph_version == "test-pep-book-v1"
    detail = flow.client.get(f"/v1/practice-sessions/{body['id']}").json()
    assert detail["graph_version"] == "test-pep-book-v1" and detail["items"][0]["graph_version"] == "test-pep-book-v1"


def test_preview_history_uses_original_book_even_after_switching_version(flow, clock):
    selected(flow)
    with Session(flow.database.engine) as session:
        row = session.get(StudentLearningProfile, "student_a")
        row.document = {**row.document, "current_knowledge_ids": ["test-pep-add"]}
        session.commit()
    open_window(flow, confirm=True)
    begun = start(flow, purpose="preview").json()
    assert begun["targets"][0]["name"] == "有理数的加减法"
    publish_book(flow, "test-pep-book-v2")
    with Session(flow.database.engine) as session:
        node = session.get(KnowledgeNode, ("test-pep-book-v2", "test-pep-add"))
        node.name = "合成第二版不同标题"
        session.commit()
    choose(flow, "test-pep-book-v2")
    detail = flow.client.get(f"/v1/practice-sessions/{begun['id']}").json()
    assert detail["graph_version"] == "test-pep-book-v1"
    assert detail["targets"][0]["name"] == "有理数的加减法"


def test_unknown_graph_old_outbox_is_not_silently_bound_to_current_default(flow):
    client = ItemClient([])
    with Session(flow.database.engine) as session:
        outcome = generate_practice(session, SimpleNamespace(payload={"knowledge_id": "kp-rational-add-sub"}),
                                    SimpleNamespace(client=client))
    assert outcome == {"status": "graph_version_missing"} and client.calls == []


def test_bank_student_context_obeys_access_and_explicit_graph_consistency(flow):
    selected(flow)
    assert create(flow, student_id="student_ungranted").status_code == 404
    assert flow.client.get("/v1/practice-items?student_id=student_ungranted").status_code == 404
    assert create(flow, student_id="student_a", graph_version="test-bnup-math-g7-latest").status_code == 409
    assert create(flow, graph_version="nonexistent-version").status_code == 422


def test_migration_backfills_only_unambiguous_legacy_items(database):
    migrate(database.url, "0012_curriculum_binding", downgrade=True)
    with database.engine.begin() as connection:
        # One node ID occurs only in the seed; another also occurs in a second
        # graph. Old items did not retain enough data to choose between them.
        connection.execute(text(
            "INSERT INTO curriculum_graphs (version,subject,title,status,review_status) "
            "VALUES ('test-ambiguous','数学','合成歧义版','draft','unreviewed')"))
        connection.execute(text(
            "INSERT INTO knowledge_nodes (graph_version,id,node_type,name,curriculum_refs,coverage_facets,misconception_codes,sort_order) "
            "VALUES ('test-ambiguous','kp-rational-add-sub','skill','合成重复ID','[]','[]','[]',1)"))
        for item_id, knowledge_id in (("old-ambiguous", "kp-rational-add-sub"), ("old-unique", "kp-rational-compare")):
            connection.execute(text(
                "INSERT INTO practice_items (id,tenant_id,knowledge_id,item_family_id,stem_text,answer_kind,answer_values,"
                "solution_text,difficulty,source,status,revision,created_at,updated_at) "
                "VALUES (:id,'tenant_a',:kid,:id,'合成历史题','number','[\"1\"]','合成',1,'staff','approved',1,"
                "'2026-10-21 00:00:00','2026-10-21 00:00:00')"), {"id": item_id, "kid": knowledge_id})
        connection.execute(text(
            "INSERT INTO practice_sessions (id,tenant_id,student_id,purpose,knowledge_ids,item_ids,hints_used,gate,status,"
            "started_at,budget_ends_at) VALUES "
            "('old-session','tenant_a','student_a','variant','[\"kp-rational-compare\"]','[\"old-unique\"]','[]','{}','completed',"
            "'2026-10-21 00:00:00','2026-10-21 00:10:00')"))
        connection.execute(text(
            "INSERT INTO practice_responses (id,tenant_id,student_id,session_id,item_id,knowledge_id,event_type,answer_text,"
            "result,hint_used,occurred_at) VALUES ('old-response','tenant_a','student_a','old-session','old-unique',"
            "'kp-rational-compare','optional_variant','1','correct',false,'2026-10-21 00:01:00')"))
    migrate(database.url)
    with Session(database.engine) as session:
        assert session.get(PracticeItem, "old-unique").graph_version == "pep-math-junior-pilot-1"
        assert session.get(PracticeItem, "old-ambiguous").graph_version is None
        assert session.get(PracticeSession, "old-session").graph_version == "pep-math-junior-pilot-1"
        assert session.get(PracticeResponse, "old-response").graph_version == "pep-math-junior-pilot-1"
