"""Synthetic manifests test publication gates, explicit selection and history."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401
from test_learning import knowledge, learning, link, prepare_latest_math_curriculum, reviewed
from xueban_backend.curriculum_catalog import graph_is_usable, scope_for_profile, scope_for_subject
from xueban_backend.curriculum_publication import import_publication, prepare_publication
from xueban_backend.learning_models import CurriculumGraph, LearningEvidence, StudentLearningProfile
from xueban_backend.learning_projection import published_graph
from xueban_backend.models import Homework
from xueban_backend.worker import CorrectionWorker
from xueban_backend.correction_models import Entity


def prepared_book(version="test-pep-book-v1"):
    # Self-authored test fixture, not a production source/audit approval.
    candidate = {
        "schema_version": "curriculum-research-candidate-1", "candidate_id": "test-pep-candidate", "candidate_revision": 2,
        "book_identity": {"title": "测试人教七上", "grade": 7, "subject": "数学", "publisher_code": "PEP",
                          "publisher": "人民教育出版社", "isbn": "9787107382468", "term": "up", "stage": "junior_middle"},
        "sources": [{"id": "source-a", "url": "https://example.invalid/a", "sha256": "a" * 64},
                    {"id": "source-b", "url": "https://example.invalid/b", "sha256": "b" * 64}],
        "official_table_of_contents": {"chapters": [
            {"id": "test-pep-chapter", "number": 1, "title": "有理数", "source_id": "source-a",
             "sections": [{"id": "section-1", "source_id": "source-b"}]}]},
        "proposed_pilot_scope": {
            "knowledge_nodes": [{"id": node, "name": name, "node_type": "skill", "official_section_ids": ["section-1"],
                                 "observable_objective": "测试可观察目标", "content_origin": "project_ai_authored"}
                                for node, name in [("test-pep-add", "有理数的加减法"), ("test-pep-mul", "有理数的乘除法")]],
            "prerequisite_edges": [{"id": "test-pep-support", "from_id": "test-pep-add", "to_id": "test-pep-mul",
                                    "relation_type": "supports", "applies_when": "测试范围", "rationale": "仅有条件支持",
                                    "official_relation": False, "propagate_mastery_or_weakness": False,
                                    "gates_learning": False, "requires_whole_source_node_mastery": False}],
        },
    }
    raw = json.dumps(candidate, ensure_ascii=False).encode()
    review = {"status": "source_verified_ai_reviewed", "review_kind": "independent_ai_content_review",
              "reviewer": "synthetic-test-reviewer", "reviewed_at": "2026-10-21", "notes": "仅自动化测试",
              "teacher_review": False, "student_edition_confirmed": False,
              "candidate_sha256": hashlib.sha256(raw).hexdigest()}
    return raw, json.dumps(review).encode(), prepare_publication(raw, json.dumps(review).encode(), version)


def publish_book(flow, version="test-pep-book-v1"):
    _, _, prepared = prepared_book(version)
    with Session(flow.database.engine) as session:
        result = import_publication(session, prepared, publish=True)
        session.commit()
    return result


def choose(flow, version):
    path = "/v1/students/student_a/curriculum-books"
    before = flow.client.get(path).json()
    result = flow.client.put(path, json={"expected_revision": before["profile_revision"], "graph_version": version})
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.parametrize("change", [
    {"status": "not_performed"}, {"candidate_sha256": "0" * 64}, {"teacher_review": True},
    {"student_edition_confirmed": True}, {"review_kind": "author_self_review"}, {"reviewer": ""},
])
def test_publication_requires_exact_independent_non_teacher_review(change):
    raw, review, _ = prepared_book()
    altered = {**json.loads(review), **change}
    with pytest.raises(ValueError):
        prepare_publication(raw, json.dumps(altered).encode(), "test-pep-book-v1")


def test_import_publish_idempotence_and_immutable_version(database):
    _, _, prepared = prepared_book()
    with Session(database.engine) as session:
        assert import_publication(session, prepared)["status"] == "draft"
        assert import_publication(session, prepared, publish=True)["status"] == "published"
        stamp = session.get(CurriculumGraph, prepared["version"]).published_at
        assert import_publication(session, prepared, publish=True)["status"] == "published"
        assert session.get(CurriculumGraph, prepared["version"]).published_at == stamp
        changed = deepcopy(prepared)
        changed["import_digest"] = "f" * 64
        with pytest.raises(ValueError, match="different content"):
            import_publication(session, changed, publish=True)
        session.commit()


def test_review_status_alone_never_makes_a_book_usable(database):
    with Session(database.engine) as session:
        graph = session.get(CurriculumGraph, "pep-math-junior-pilot-1")
        graph.review_status = "source_verified_ai_reviewed"
        assert not graph_is_usable(graph)


def test_new_book_never_silently_replaces_legacy_student_scope(flow):
    prepare_latest_math_curriculum(flow)
    publish_book(flow)
    assert scope_for_subject(7, "数学").publisher_code == "BNUP"
    with Session(flow.database.engine) as session:
        assert published_graph(session).version == "test-bnup-math-g7-latest"
        assert published_graph(session, "student_a").version == "test-bnup-math-g7-latest"
    books = flow.client.get("/v1/students/student_a/curriculum-books").json()
    assert books["current_binding"] is None and len(books["books"]) == 1
    assert books["books"][0]["review_status"] == "source_verified_ai_reviewed"
    assert knowledge(flow)["graph_version"] == "test-bnup-math-g7-latest"
    teacher(flow)
    selected = choose(flow, "test-pep-book-v1")
    assert selected["current_binding"]["isbn"] == "9787107382468"
    page = knowledge(flow)
    assert page["graph_version"] == "test-pep-book-v1" and page["graph_review_status"] == "source_verified_ai_reviewed"
    assert page["book_identity"] == selected["current_binding"]
    assert {node["id"] for node in page["nodes"]} == {"test-pep-add", "test-pep-mul", "test-pep-chapter"}
    assert page["edges"] == []  # supports must never masquerade as a prerequisite.
    assert all(node["learning_status"] == "no_judgment" for node in page["nodes"])
    assert flow.client.get("/v1/curriculum/graph?student_id=student_a").json()["graph_version"] == "test-pep-book-v1"
    choose(flow, None)
    assert knowledge(flow)["graph_version"] == "test-bnup-math-g7-latest"


def test_reviewed_repository_candidate_preserves_typed_relations_and_source_snapshots(database):
    folder = Path(__file__).resolve().parents[1] / "xueban_backend/data/curriculum/candidates"
    name = "pep-math-g7-up-9787107382468-candidate-20261021"
    raw, review = (folder / (name + ".json")).read_bytes(), (folder / (name + ".audit.json")).read_bytes()
    prepared = prepare_publication(raw, review, "pep-math-g7-up-9787107382468-r2")
    assert len(prepared["nodes"]) == 29 and len(prepared["edges"]) == 37
    assert [edge["relation_type"] for edge in prepared["edges"]].count("prerequisite_of") == 35
    assert {edge["relation_type"] for edge in prepared["edges"]} == {"prerequisite_of", "supports", "specializes_to"}
    with Session(database.engine) as session:
        import_publication(session, prepared, publish=True)
        import_publication(session, prepared, publish=True)
        graph = session.get(CurriculumGraph, prepared["version"])
        assert graph_is_usable(graph)
        assert graph.review_metadata["candidate_snapshot"] == json.loads(raw)
        assert graph.review_metadata["publication_review"]["teacher_review"] is False
        assert published_graph(session).version == "pep-math-junior-pilot-1"


def test_selection_pins_graph_version_and_rejects_wrong_grade_or_stale_revision(flow):
    prepare_latest_math_curriculum(flow)
    publish_book(flow)
    teacher(flow)
    selected = choose(flow, "test-pep-book-v1")
    publish_book(flow, "test-pep-book-v2")
    assert knowledge(flow)["graph_version"] == "test-pep-book-v1"
    path = "/v1/students/student_a/curriculum-books"
    assert flow.client.put(path, json={"expected_revision": selected["profile_revision"] - 1,
                                     "graph_version": "test-pep-book-v2"}).status_code == 409
    assert flow.client.put(path, json={"expected_revision": selected["profile_revision"],
                                     "graph_version": "pep-math-junior-pilot-1"}).status_code == 422
    assert flow.client.put("/v1/students/student_a/learning-profile",
                           json={"expected_revision": selected["profile_revision"], "grade": 8}).status_code == 409
    choose(flow, None)
    revision = flow.client.get(path).json()["profile_revision"]
    assert flow.client.put("/v1/students/student_a/learning-profile",
                           json={"expected_revision": revision, "grade": 8}).status_code == 200
    assert flow.client.get(path).json()["books"] == []
    revision = flow.client.get(path).json()["profile_revision"]
    assert flow.client.put(path, json={"expected_revision": revision, "graph_version": "test-pep-book-v1"}).status_code == 422


def test_new_homework_snapshots_selected_isbn_but_old_homework_stays_unchanged(flow, payload):
    case = reviewed(flow)
    publish_book(flow)
    teacher(flow)
    choose(flow, "test-pep-book-v1")
    with Session(flow.database.engine) as session:
        old_course = deepcopy(session.get(Homework, flow.homework_id).curriculum)
    body = deepcopy(payload)
    body["curriculum"].update(grade=7, subject="数学", publisher_code="PEP")
    response = flow.post("/v1/students/student_a/homeworks", body)
    assert response.status_code == 201, response.text
    assert response.json()["curriculum"]["edition"] == "isbn:9787107382468"
    with Session(flow.database.engine) as session:
        assert session.get(Homework, flow.homework_id).curriculum == old_course
        student = session.get(StudentLearningProfile, "student_a")
        scope = scope_for_profile(student.document, "数学")
        assert scope.publisher_code == "PEP" and scope.isbn == "9787107382468"
        homework = session.get(Homework, response.json()["id"])
        from xueban_backend.models import Student
        question = session.get(Entity, case["question_id"])
        context = CorrectionWorker._student_learning_context(session, homework, session.get(Student, "student_a"), question)
        assert context["curriculum_context"]["status"] == "available"
        assert set(context["knowledge_point_candidates"]) == {"有理数的加减法", "有理数的乘除法"}


def test_switch_retains_old_evidence_audit_and_question_links_follow_student_book(flow):
    case = reviewed(flow)
    teacher(flow)
    previous = link(flow, case)
    learning(flow).drain()
    with Session(flow.database.engine) as session:
        evidence_id = session.scalar(select(LearningEvidence.id).where(LearningEvidence.validity == "valid"))
    publish_book(flow)
    choose(flow, "test-pep-book-v1")
    links_path = f"/v1/questions/{case['question_id']}/knowledge-links"
    assert flow.client.get(links_path).json()["items"] == []
    assert flow.client.get(links_path).json()["graph_version"] == "test-pep-book-v1"
    assert flow.client.get(f"/v1/curriculum/graph?question_id={case['question_id']}").json()["graph_version"] == "test-pep-book-v1"
    learning(flow).drain()
    with Session(flow.database.engine) as session:
        assert session.get(LearningEvidence, evidence_id).validity == "invalidated"
    choose(flow, None)
    learning(flow).drain()
    assert flow.client.get(links_path).json()["items"][0]["id"] == previous["id"]
    with Session(flow.database.engine) as session:
        assert session.get(LearningEvidence, evidence_id).validity == "invalidated"
        assert session.scalar(select(LearningEvidence.id).where(LearningEvidence.validity == "valid")) != evidence_id


def test_books_and_bound_graph_keep_student_permissions(flow):
    prepare_latest_math_curriculum(flow)
    publish_book(flow)
    other = {"Authorization": "Bearer " + flow.signing.token("subject-b")}
    for path in ("/v1/students/student_a/curriculum-books", "/v1/curriculum/graph?student_id=student_a"):
        assert flow.client.get(path, headers=other).status_code == 404
    assert flow.client.put("/v1/students/student_a/curriculum-books", headers=other,
                           json={"expected_revision": 0, "graph_version": "test-pep-book-v1"}).status_code == 404


def test_new_pep_homework_correct_answer_becomes_positive_model_evidence(flow, payload):
    from test_correction_flow import jpeg
    from test_multimodal_worker import assessment, enable_page_assessments
    prepare_latest_math_curriculum(flow)
    publish_book(flow)
    teacher(flow)
    choose(flow, "test-pep-book-v1")
    body = deepcopy(payload)
    body["curriculum"].update(grade=7, subject="数学", publisher_code="PEP")
    response = flow.post("/v1/students/student_a/homeworks", body)
    assert response.status_code == 201, response.text
    homework = response.json()
    content = jpeg()
    ticket = flow.post(f"/v1/homeworks/{homework['id']}/uploads", {
        "expected_revision": homework["revision"], "page_index": 0, "sha256": hashlib.sha256(content).hexdigest(),
        "byte_size": len(content), "mime_type": "image/jpeg", "width": 120, "height": 80}).json()
    assert flow.client.put(ticket["put_url"], content=content, headers=ticket["required_headers"]).status_code == 204
    asset = ticket["asset"]
    assert flow.post(f"/v1/assets/{asset['id']}/complete", {
        "expected_revision": asset["revision"], "sha256": asset["sha256"], "byte_size": asset["byte_size"]}).status_code == 200

    def evaluate(items):
        items[0]["subject"] = "数学"
        items[0]["assessment"] = assessment("correct")
        items[0]["assessment"]["knowledge_points"] = ["有理数的加减法"]
    enable_page_assessments(flow, evaluate)
    homework = flow.client.get(f"/v1/homeworks/{homework['id']}").json()
    assert flow.post(f"/v1/homeworks/{homework['id']}/recognitions", {
        "expected_revision": homework["revision"], "asset_ids": [asset["id"]]}).status_code == 202
    assert flow.worker.run_once()
    learning(flow).drain()
    page = knowledge(flow)
    assert page["graph_version"] == "test-pep-book-v1"
    target = next(node for node in page["nodes"] if node["id"] == "test-pep-add")
    assert target["valid_evidence_count"] == target["model_evidence_count"] == 1
    assert target["staff_verified_evidence_count"] == 0 and target["learning_status"] == "no_judgment"
    detail = flow.client.get("/v1/students/student_a/knowledge/test-pep-add").json()
    assert detail["evidence"][0]["graph_version"] == "test-pep-book-v1"
    assert detail["evidence"][0]["result"] == "correct" and detail["evidence"][0]["independent"] is False
