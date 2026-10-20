"""Supported student curriculum scopes.

This catalog deliberately stores curriculum metadata, not textbook scans or
copyrighted textbook pages.  ``revision_policy='publisher_latest'`` means a
graph is valid only when its source has been checked against the publisher's
current catalogue.  A missing publisher is never treated as a match.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path


@dataclass(frozen=True)
class CurriculumScope:
    grade: int
    subject: str
    publisher_code: str
    publisher_name: str
    # The app resolves the active edition against the current official
    # catalogue.  It must never fall back to the older GitHub scans.
    revision_policy: str = "publisher_latest"
    textbook_revision: str = "publisher_latest"
    isbn: str | None = None
    term: str | None = None
    graph_version: str | None = None


BOOK_IDENTITY = {"grade": 7, "subject": "数学", "publisher_code": "PEP",
                 "publisher_name": "人民教育出版社", "isbn": "9787107382468", "term": "up",
                 "title": "义务教育教科书·数学七年级上册"}


_PUBLISHERS = {
    "pep": ("PEP", "人民教育出版社"),
    "人教版": ("PEP", "人民教育出版社"),
    "renjiaoban": ("PEP", "人民教育出版社"),
    "人民教育出版社": ("PEP", "人民教育出版社"),
    "peoples education press": ("PEP", "人民教育出版社"),
    "bnup": ("BNUP", "北京师范大学出版社"),
    "北师大版": ("BNUP", "北京师范大学出版社"),
    "北京师范大学出版社": ("BNUP", "北京师范大学出版社"),
    "pm": ("PM", "人民音乐出版社"),
    "人民音乐出版社": ("PM", "人民音乐出版社"),
    "pfa": ("PFA", "人民美术出版社"),
    "人民美术出版社": ("PFA", "人民美术出版社"),
    "csp": ("CSP", "科学教育出版社"),
    "教科版": ("ECSP", "教育科学出版社"),
    "科学教育出版社": ("CSP", "科学教育出版社"),
    "ecsp": ("ECSP", "教育科学出版社"),
    "教育科学出版社": ("ECSP", "教育科学出版社"),
    "jsfst": ("JSFST", "江苏凤凰科学技术出版社"),
    "江苏凤凰科学技术出版社": ("JSFST", "江苏凤凰科学技术出版社"),
    "sstp": ("SSTP", "陕西旅游出版社"),
    "陕西旅游出版社": ("SSTP", "陕西旅游出版社"),
}


# Only the combinations explicitly supplied by the product owner are enabled.
# Keep this table exhaustive: adding a subject requires a deliberate product
# decision and a curriculum graph, rather than silently falling through.
_SPECS = (
    # Grade 5
    (5, "体育与健康", "pep"), (5, "艺术音乐", "pm"),
    (5, "科学", "csp"), (5, "语文", "pep"), (5, "数学", "pep"), (5, "英语", "sstp"),
    # Grade 6
    (6, "美术", "pfa"), (6, "科学", "ecsp"), (6, "语文", "pep"),
    (6, "数学", "pep"), (6, "英语", "sstp"),
    # Grade 7
    (7, "语文", "pep"), (7, "历史", "pep"), (7, "英语", "pep"),
    (7, "政治", "pep"), (7, "地理", "pep"), (7, "生物", "jsfst"), (7, "数学", "bnup"),
    # Grade 8
    (8, "政治", "pep"), (8, "物理", "jsfst"), (8, "地理", "pep"),
    (8, "历史", "pep"), (8, "数学", "bnup"), (8, "英语", "pep"),
    (8, "语文", "pep"), (8, "生物", "jsfst"),
)
_SUBJECT_ALIASES = {
    "math": "数学", "mathematics": "数学", "小学数学": "数学", "初中数学": "数学",
    "chinese": "语文", "language arts": "语文", "english": "英语", "history": "历史",
    "geography": "地理", "biology": "生物", "生物学": "生物", "physics": "物理", "politics": "政治",
    "道德与法治": "政治", "道法": "政治", "科学教育": "科学", "art": "美术", "music": "艺术音乐",
    "艺术·音乐": "艺术音乐", "艺术音乐": "艺术音乐", "艺术·美术": "美术",
    "physical education": "体育与健康", "体育": "体育与健康",
}


def _key(value: object) -> str:
    return str(value or "").strip().casefold()


def canonical_subject(value: object) -> str | None:
    key = _key(value)
    if not key:
        return None
    canonical = _SUBJECT_ALIASES.get(key, str(value).strip())
    return canonical if any(_key(subject) == _key(canonical) for _, subject, _ in _SPECS) else None


def publisher_code(value: object) -> str | None:
    item = _PUBLISHERS.get(_key(value))
    return item[0] if item else None


def publisher_name(value: object) -> str | None:
    item = _PUBLISHERS.get(_key(value))
    return item[1] if item else None


def supported_scope(grade: object, subject: object, publisher: object) -> CurriculumScope | None:
    """Return a scope only for an explicitly configured grade/subject/publisher."""
    if type(grade) is not int or not 1 <= grade <= 12:
        return None
    subject_key = _key(canonical_subject(subject))
    code = publisher_code(publisher)
    if not subject_key or code is None:
        return None
    # Explicit PEP selection is supported without changing the legacy default
    # BNUP scope used by already enrolled students.
    if grade == 7 and subject_key == _key("数学") and code == "PEP":
        return CurriculumScope(grade, "数学", code, "人民教育出版社")
    for item_grade, item_subject, item_publisher in _SPECS:
        if item_grade == grade and _key(item_subject) == subject_key and _PUBLISHERS[item_publisher][0] == code:
            name = _PUBLISHERS[item_publisher][1]
            return CurriculumScope(grade, item_subject, code, name)
    return None


def scope_for_subject(grade: object, subject: object) -> CurriculumScope | None:
    canonical = canonical_subject(subject)
    if canonical is None:
        return None
    matches = [(item_grade, item_subject, publisher) for item_grade, item_subject, publisher in _SPECS
               if item_grade == grade and _key(item_subject) == _key(canonical)]
    if len(matches) != 1:
        return None
    _, name, publisher = matches[0]
    code, publisher_label = _PUBLISHERS[publisher]
    return CurriculumScope(grade, name, code, publisher_label)


def scopes_for_grade(grade: object) -> tuple[CurriculumScope, ...]:
    if type(grade) is not int:
        return ()
    return tuple(scope_for_subject(grade, subject) for item_grade, subject, _ in _SPECS if item_grade == grade)


def scope_for_profile(document: dict | None, subject: object) -> CurriculumScope | None:
    document = document or {}
    binding = document.get("curriculum_binding") or {}
    canonical = canonical_subject(subject)
    if binding and canonical == binding.get("subject"):
        if document.get("grade") != binding.get("grade") or any(
                binding.get(key) != BOOK_IDENTITY[key] for key in ("grade", "subject", "publisher_code", "isbn", "term")):
            return None
        return CurriculumScope(binding["grade"], canonical, binding["publisher_code"], BOOK_IDENTITY["publisher_name"],
                               "explicit_book", "isbn:" + binding["isbn"], binding["isbn"], binding["term"],
                               binding.get("graph_version"))
    return scope_for_subject(document.get("grade"), subject)


def scopes_for_profile(document: dict | None) -> tuple[CurriculumScope, ...]:
    document = document or {}
    return tuple(scope for previous in scopes_for_grade(document.get("grade"))
                 if (scope := scope_for_profile(document, previous.subject)) is not None)


def curriculum_matches_scope(curriculum: dict, scope: CurriculumScope) -> bool:
    return (curriculum.get("grade") == scope.grade
            and curriculum.get("edition") == scope.textbook_revision
            and (curriculum.get("subject") is None or canonical_subject(curriculum["subject"]) == scope.subject)
            and (curriculum.get("publisher_code") is None or curriculum["publisher_code"] == scope.publisher_code))


def curriculum_stage(grade: int | None) -> str | None:
    if grade is None:
        return None
    return "primary" if grade <= 6 else "junior_middle"


def scope_payload(scope: CurriculumScope) -> dict:
    if scope.isbn:
        # This identifies a source-verified resource snapshot. It does not
        # assert the publisher's latest edition or any teacher approval.
        return {"grade": scope.grade, "subject": scope.subject, "publisher_code": scope.publisher_code,
                "publisher_name": scope.publisher_name, "revision_policy": "explicit_book",
                "textbook_revision": scope.textbook_revision, "stage": curriculum_stage(scope.grade),
                "catalog_status": "available", "catalog_verified_at": "2026-10-21",
                "catalog_candidates": [], "isbn": scope.isbn, "graph_version": scope.graph_version,
                "books": [{"term": scope.term, "title": BOOK_IDENTITY["title"], "isbn": scope.isbn,
                           "catalog_year": None, "updated_at": None, "revised_2022_standard": True,
                           "contents_review_status": "source_verified_ai_reviewed"}]}
    catalog = _official_catalog()
    requested = next((item for item in catalog.get("requested_scopes", [])
                      if item.get("grade") == scope.grade
                      and canonical_subject(item.get("subject")) == scope.subject
                      and publisher_code(item.get("publisher")) == scope.publisher_code), None)
    records = _official_books()
    matching = [row for row in records if row.get("grade") == scope.grade
                and canonical_subject(row.get("subject")) == scope.subject
                and publisher_code(row.get("publisher")) == scope.publisher_code]
    books = []
    for row in matching:
        official = row["official"]
        books.append({"term": row["term"], "title": official["title"],
                      "isbn": official.get("isbn"), "catalog_year": official.get("catalog_year"),
                      "updated_at": official.get("updated_at"),
                      "revised_2022_standard": official.get("revised_2022_standard", False),
                      "contents_review_status": row["contents_review_status"]})
    books.sort(key=lambda item: ({"up": 0, "down": 1, "full": 2}.get(item["term"], 3), item["title"]))
    legacy_candidates = [row for row in records if row.get("grade") == scope.grade
                         and canonical_subject(row.get("subject")) == scope.subject]
    catalog_status = (requested.get("status") if requested else
                      "publisher_mismatch" if legacy_candidates and not matching else
                      "available" if len(books) == 2 else "incomplete" if books else "unavailable")
    return {"grade": scope.grade, "subject": scope.subject, "publisher_code": scope.publisher_code,
            "publisher_name": scope.publisher_name, "revision_policy": scope.revision_policy,
            "textbook_revision": "publisher_latest", "stage": curriculum_stage(scope.grade),
            "catalog_status": catalog_status, "catalog_verified_at": catalog.get("verified_at"),
            "catalog_candidates": requested.get("candidate_versions", []) if requested else [], "books": books}


@lru_cache(maxsize=1)
def _official_catalog() -> dict:
    root = Path(__file__).resolve().parents[3] / "curriculum"
    try:
        catalog = json.loads((root / "official_catalog.json").read_text())
        return catalog if isinstance(catalog, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


@lru_cache(maxsize=1)
def _official_books() -> tuple[dict, ...]:
    """Read actual latest editions; legacy local scans are never version evidence."""
    root = Path(__file__).resolve().parents[3] / "curriculum"
    try:
        catalog = _official_catalog()
        if catalog.get("schema_version", 0) >= 2:
            return tuple({"grade": book.get("grade"), "subject": book.get("subject"),
                          "publisher": book.get("publisher"), "term": book.get("term"),
                          "official": book, "contents_review_status": "unreviewed"}
                         for book in catalog.get("books", []) if isinstance(book, dict))
        inventory = json.loads((root / "textbooks.json").read_text())
    except (OSError, json.JSONDecodeError):
        return ()
    catalog = catalog if isinstance(catalog, dict) else {}
    catalog_by_id = {book["id"]: book for book in catalog.get("books", []) if isinstance(book, dict)}
    rows = []
    for book in inventory.get("books", []):
        official = catalog_by_id.get(book.get("id"))
        if not official:
            continue
        current = book.get("current_official") or {}
        edition = current.get("edition") or {}
        rows.append({"grade": book.get("grade"), "subject": book.get("subject"),
                     "publisher": book.get("publisher"), "term": book.get("term"),
                     "official": official,
                     "contents_review_status": edition.get("contents_review_status", "unavailable")})
    return tuple(rows)


def graph_is_latest_reviewed(graph) -> bool:
    """Only graphs checked against a current publisher catalogue may tag students."""
    return graph is not None and graph.review_status == "publisher_latest_reviewed"


def graph_is_usable(graph) -> bool:
    if graph_is_latest_reviewed(graph) and not getattr(graph, "book_identity", None):
        return True
    if graph is None or graph.review_status != "source_verified_ai_reviewed" or graph.status != "published":
        return False
    book, review = getattr(graph, "book_identity", None) or {}, getattr(graph, "review_metadata", None) or {}
    approval = review.get("publication_review") or {}
    digest = getattr(graph, "import_digest", None)
    return (all(book.get(key) == BOOK_IDENTITY[key] for key in ("grade", "subject", "publisher_code", "isbn", "term"))
            and review.get("validation_version") == "book-graph-v1"
            and isinstance(digest, str) and len(digest) == 64 and review.get("candidate_sha256") == digest
            and bool(review.get("sources"))
            and approval.get("status") == "source_verified_ai_reviewed"
            and approval.get("candidate_sha256") == digest
            and approval.get("review_kind") == "independent_ai_content_review"
            and approval.get("teacher_review") is False and approval.get("student_edition_confirmed") is False)


def node_matches_scope(node, scope: CurriculumScope) -> bool:
    refs = node.curriculum_refs if isinstance(node.curriculum_refs, list) else []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        ref_publisher = publisher_code(ref.get("publisher"))
        ref_grade = ref.get("grade")
        if (ref_publisher == scope.publisher_code and ref_grade == scope.grade
                and canonical_subject(ref.get("subject")) == scope.subject
                and ref.get("textbook_revision") == scope.textbook_revision
                and (scope.isbn is None or (ref.get("isbn") == scope.isbn and ref.get("term") == scope.term))
                and ref.get("stage") == curriculum_stage(scope.grade)):
            return True
    return False


def supported_scopes() -> tuple[CurriculumScope, ...]:
    return tuple(CurriculumScope(grade, subject, _PUBLISHERS[code][0], _PUBLISHERS[code][1])
                 for grade, subject, code in _SPECS)
