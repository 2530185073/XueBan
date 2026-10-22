"""Per-student explicit book selection; legacy students retain their old graph."""
from sqlalchemy import select

from .curriculum_catalog import graph_is_usable
from .learning_models import CurriculumGraph, StudentLearningProfile


def resolve_graph(session, student_id=None):
    profile = session.get(StudentLearningProfile, student_id) if student_id else None
    binding = (profile.document.get("curriculum_binding") or {}) if profile else {}
    if binding:
        graph = session.get(CurriculumGraph, binding.get("graph_version"))
        if graph is None or graph.status != "published" or not graph_is_usable(graph):
            return None
        book = graph.book_identity or {}
        if profile.document.get("grade") != book.get("grade") or any(
                binding.get(key) != book.get(key) for key in ("grade", "subject", "publisher_code", "isbn", "term")):
            return None
        return graph
    # A later PEP book publication must never switch legacy BNUP students.
    return session.scalar(select(CurriculumGraph).where(
        CurriculumGraph.status == "published", CurriculumGraph.book_identity.is_(None))
        .order_by(CurriculumGraph.published_at.desc(), CurriculumGraph.version))


def book_payload(graph):
    book = graph.book_identity or {}
    return {"graph_version": graph.version, "title": book.get("title", graph.title),
            "subject": book.get("subject"), "grade": book.get("grade"),
            "publisher_code": book.get("publisher_code"), "publisher_name": book.get("publisher"),
            "isbn": book.get("isbn"), "term": book.get("term"), "review_status": graph.review_status,
            "review_label": "目录来源已核对，知识点关系由 AI 整理",
            "source_note": graph.source_note}
