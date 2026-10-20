"""Worker curriculum context uses only product-approved, current textbook scopes."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from xueban_backend import curriculum_catalog as catalog
from xueban_backend.learning_models import CurriculumGraph, KnowledgeNode, StudentLearningProfile
from xueban_backend.models import Student
from xueban_backend.worker import CorrectionWorker


# Independent product whitelist, including both distinct science publishers.
APPROVED = (
    (5, "体育与健康", "PEP", "人民教育出版社"),
    (5, "艺术音乐", "PM", "人民音乐出版社"),
    (5, "科学", "CSP", "科学教育出版社"),
    (5, "语文", "PEP", "人民教育出版社"),
    (5, "数学", "PEP", "人民教育出版社"),
    (5, "英语", "SSTP", "陕西旅游出版社"),
    (6, "美术", "PFA", "人民美术出版社"),
    (6, "科学", "ECSP", "教育科学出版社"),
    (6, "语文", "PEP", "人民教育出版社"),
    (6, "数学", "PEP", "人民教育出版社"),
    (6, "英语", "SSTP", "陕西旅游出版社"),
    (7, "语文", "PEP", "人民教育出版社"),
    (7, "历史", "PEP", "人民教育出版社"),
    (7, "英语", "PEP", "人民教育出版社"),
    (7, "政治", "PEP", "人民教育出版社"),
    (7, "地理", "PEP", "人民教育出版社"),
    (7, "生物", "JSFST", "江苏凤凰科学技术出版社"),
    (7, "数学", "BNUP", "北京师范大学出版社"),
    (8, "政治", "PEP", "人民教育出版社"),
    (8, "物理", "JSFST", "江苏凤凰科学技术出版社"),
    (8, "地理", "PEP", "人民教育出版社"),
    (8, "历史", "PEP", "人民教育出版社"),
    (8, "数学", "BNUP", "北京师范大学出版社"),
    (8, "英语", "PEP", "人民教育出版社"),
    (8, "语文", "PEP", "人民教育出版社"),
    (8, "生物", "JSFST", "江苏凤凰科学技术出版社"),
)


@pytest.fixture
def learning_session(database):
    with Session(database.engine) as session:
        # Do not depend on the production pilot graph being published/reviewed.
        for graph in session.scalars(select(CurriculumGraph)):
            graph.status = "draft"
        session.flush()
        yield session


def context(session, *, grade=7, subject="数学", curriculum=None, annotations=None,
            has_profile=True, has_question=True):
    student = session.get(Student, "student_a")
    if has_profile:
        profile = session.get(StudentLearningProfile, student.id)
        if profile is None:
            profile = StudentLearningProfile(
                student_id=student.id, tenant_id=student.tenant_id, revision=1,
                document={"grade": grade}, updated_by="actor_a",
                updated_at=datetime.now(timezone.utc),
            )
            session.add(profile)
        else:
            profile.document = {"grade": grade}
        session.flush()
    homework = SimpleNamespace(curriculum=(
        {"grade": grade, "edition": "publisher_latest"} if curriculum is None else curriculum
    ))
    question = SimpleNamespace(document={"subject": subject}, annotations=annotations)
    return CorrectionWorker._student_learning_context(
        session, homework, student, question if has_question else None,
    )


def add_graph(session, *, reviewed=True):
    graph = CurriculumGraph(
        version="test-commercial-latest", subject="数学", title="合成教材图谱",
        status="published", review_status="publisher_latest_reviewed" if reviewed else "pilot_seed_unreviewed",
        published_at=datetime.now(timezone.utc),
    )
    session.add(graph)
    session.flush()
    return graph


def add_node(session, graph, identifier, *, refs=None, node_type="concept", sort_order=0):
    session.add(KnowledgeNode(
        graph_version=graph.version, id=identifier, name=identifier, node_type=node_type,
        curriculum_refs=deepcopy(refs if refs is not None else [{
            "grade": 7, "subject": "数学", "publisher": "北京师范大学出版社",
            "textbook_revision": "publisher_latest", "stage": "junior_middle",
        }]),
        coverage_facets=[], misconception_codes=[], sort_order=sort_order,
    ))
    session.flush()


@pytest.mark.parametrize("grade,subject,code,publisher", APPROVED)
def test_all_declared_subjects_keep_their_textbook_scope_without_a_graph(
        learning_session, grade, subject, code, publisher):
    result = context(learning_session, grade=grade, subject=subject)
    scope = result["curriculum_context"]["scope"]
    expected = catalog.scope_payload(catalog.scope_for_subject(grade, subject))
    assert scope == expected
    assert (scope["grade"], scope["subject"], scope["publisher_code"], scope["publisher_name"]) == (
        grade, subject, code, publisher,
    )
    assert result["student_grade"] == grade
    assert result["knowledge_point_candidates"] == []
    assert result["curriculum_context"]["revision_policy"] == "publisher_latest"
    assert result["curriculum_context"]["status"] == (
        "available" if scope["catalog_status"] == "available" else "unverified_edition"
    )


@pytest.mark.parametrize("grade", [None, 0, 4, 9, 12, "7", 7.0, True])
def test_unknown_or_unsupported_profile_grade_never_uses_homework_grade(learning_session, grade):
    result = context(learning_session, grade=grade,
                     curriculum={"grade": 7, "edition": "publisher_latest"})
    assert result == {
        "student_grade": None, "knowledge_point_candidates": [],
        "curriculum_context": {"scope": None, "status": "unsupported_scope",
                               "revision_policy": "publisher_latest"},
    }


def test_missing_profile_does_not_infer_grade_from_homework(learning_session):
    result = context(learning_session, has_profile=False)
    assert result["student_grade"] is None
    assert result["knowledge_point_candidates"] == []
    assert result["curriculum_context"]["scope"] is None
    assert result["curriculum_context"]["status"] == "unsupported_scope"


@pytest.mark.parametrize("subject", [None, "", "unknown", "化学", "综合"])
def test_unknown_subject_never_falls_back_to_homework_subject(learning_session, subject):
    result = context(learning_session, subject=subject, curriculum={
        "grade": 7, "subject": "数学", "publisher_code": "BNUP", "edition": "publisher_latest",
    })
    assert result["student_grade"] == 7
    assert result["knowledge_point_candidates"] == []
    assert result["curriculum_context"] == {
        "scope": None, "status": "unresolved_subject", "revision_policy": "publisher_latest",
    }


def test_missing_question_returns_unresolved_subject(learning_session):
    result = context(learning_session, has_question=False)
    assert result["curriculum_context"]["status"] == "unresolved_subject"
    assert result["curriculum_context"]["scope"] is None


@pytest.mark.parametrize("grade,subject", [(5, "物理"), (6, "历史"), (7, "物理"), (8, "科学")])
def test_known_subject_outside_declared_grade_scope_is_unsupported(learning_session, grade, subject):
    result = context(learning_session, grade=grade, subject=subject)
    assert result["curriculum_context"]["status"] == "unsupported_scope"
    assert result["curriculum_context"]["scope"] is None
    assert result["knowledge_point_candidates"] == []


@pytest.mark.parametrize("grade,subject,publisher", [
    (5, "科学", "ECSP"), (6, "科学", "CSP"), (7, "数学", "PEP"),
    (5, "英语", "PEP"), (7, "生物", "PEP"), (7, "语文", "UNKNOWN"),
])
def test_explicit_unsupported_publisher_is_not_silently_replaced(learning_session, grade, subject, publisher):
    result = context(learning_session, grade=grade, subject=subject, curriculum={
        "grade": grade, "subject": subject, "publisher_code": publisher, "edition": "publisher_latest",
    })
    assert result["curriculum_context"]["status"] == "unsupported_scope"
    assert result["curriculum_context"]["scope"] is None
    assert result["knowledge_point_candidates"] == []


def test_explicit_homework_subject_conflict_is_not_a_verified_scope(learning_session):
    result = context(learning_session, subject="语文", curriculum={
        "grade": 7, "subject": "数学", "publisher_code": "BNUP", "edition": "publisher_latest",
    })
    assert result["curriculum_context"]["status"] == "unsupported_scope"
    assert result["curriculum_context"]["scope"] is None


@pytest.mark.parametrize("subject,override,canonical,publisher", [
    ("math", None, "数学", "BNUP"),
    ("数学", "chinese", "语文", "PEP"),
    (None, "道德与法治", "政治", "PEP"),
    ("math", "生物学", "生物", "JSFST"),
])
def test_subject_alias_and_manual_override_select_the_full_user_scope(
        learning_session, subject, override, canonical, publisher):
    result = context(learning_session, subject=subject, annotations={"subject_override": override})
    assert result["curriculum_context"]["status"] == "available"
    assert result["curriculum_context"]["scope"]["subject"] == canonical
    assert result["curriculum_context"]["scope"]["publisher_code"] == publisher


@pytest.mark.parametrize("curriculum", [
    {}, {"grade": None, "edition": "publisher_latest"},
    {"grade": 6, "edition": "publisher_latest"}, {"grade": 7},
    {"grade": 7, "edition": "2013"}, [],
])
def test_unverified_homework_edition_keeps_approved_range_but_not_graph_tags(learning_session, curriculum):
    graph = add_graph(learning_session)
    add_node(learning_session, graph, "不可在未验证版本放行")
    result = context(learning_session, curriculum=curriculum)
    assert result["curriculum_context"]["status"] == "unverified_edition"
    assert result["curriculum_context"]["scope"]["publisher_code"] == "BNUP"
    assert result["curriculum_context"]["scope"]["grade"] == 7
    assert result["knowledge_point_candidates"] == []


@pytest.mark.parametrize("catalog_status", ["publisher_mismatch", "incomplete", "unavailable"])
def test_unverified_catalog_does_not_become_available_from_a_reviewed_graph(
        learning_session, monkeypatch, catalog_status):
    graph = add_graph(learning_session)
    add_node(learning_session, graph, "不可替代教材目录核验")
    monkeypatch.setattr(catalog, "_official_catalog", lambda: {
        "requested_scopes": [{"grade": 7, "subject": "数学", "publisher": "北京师范大学出版社",
                              "status": catalog_status}],
    })
    result = context(learning_session)
    assert result["curriculum_context"]["status"] == "unverified_edition"
    assert result["curriculum_context"]["scope"]["catalog_status"] == catalog_status
    assert result["knowledge_point_candidates"] == []


@pytest.mark.parametrize("graph_state", ["missing", "unreviewed", "empty", "out_of_scope"])
def test_no_graph_candidates_does_not_remove_available_textbook_scope(learning_session, graph_state):
    if graph_state != "missing":
        graph = add_graph(learning_session, reviewed=graph_state != "unreviewed")
        if graph_state in {"unreviewed", "out_of_scope"}:
            add_node(learning_session, graph, "不应匹配", refs=[{
                "grade": 6, "subject": "数学", "publisher": "人民教育出版社",
                "textbook_revision": "publisher_latest", "stage": "primary",
            }])
    result = context(learning_session)
    assert result["knowledge_point_candidates"] == []
    assert result["curriculum_context"]["status"] == "available"
    assert result["curriculum_context"]["scope"]["publisher_code"] == "BNUP"
    assert result["curriculum_context"]["scope"]["books"]


def test_reviewed_graph_candidates_still_require_matching_grade_subject_publisher_and_revision(learning_session):
    graph = add_graph(learning_session)
    base_ref = {"grade": 7, "subject": "数学", "publisher": "北京师范大学出版社",
                "textbook_revision": "publisher_latest", "stage": "junior_middle"}
    for index, change in enumerate([
            {"grade": 8}, {"subject": "语文"}, {"publisher": "人民教育出版社"},
            {"textbook_revision": "2013"}, {"stage": "primary"}]):
        add_node(learning_session, graph, f"不匹配{index}", refs=[{**base_ref, **change}])
    add_node(learning_session, graph, "章节不是候选标签", node_type="chapter")
    add_node(learning_session, graph, "有理数加减", sort_order=2)
    add_node(learning_session, graph, "有理数运算", node_type="skill", sort_order=1)
    result = context(learning_session)
    assert result["knowledge_point_candidates"] == ["有理数运算", "有理数加减"]
    assert result["curriculum_context"]["status"] == "available"
