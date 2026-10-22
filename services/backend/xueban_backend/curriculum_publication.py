"""Validate and import a source-bound, independently AI-reviewed textbook graph."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit

from sqlalchemy import select

from .curriculum_catalog import BOOK_IDENTITY
from .learning_models import CurriculumGraph, KnowledgeEdge, KnowledgeNode

IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
REVIEW_STATUS = "source_verified_ai_reviewed"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _read(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            _require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")))


def prepare_publication(candidate_bytes, review_bytes, version):
    _require(isinstance(version, str) and IDENTIFIER.fullmatch(version), "Invalid graph version")
    candidate, review = _read(candidate_bytes), _read(review_bytes)
    _require(isinstance(candidate, dict) and isinstance(review, dict), "Candidate and review must be JSON objects")
    digest = hashlib.sha256(candidate_bytes).hexdigest()
    _require(candidate.get("schema_version") == "curriculum-research-candidate-1", "Unknown candidate schema")
    _require(review.get("status") == REVIEW_STATUS and review.get("candidate_sha256") == digest,
             "Independent review must approve these exact candidate bytes")
    _require(review.get("review_kind") == "independent_ai_content_review"
             and review.get("teacher_review") is False and review.get("student_edition_confirmed") is False,
             "AI review must not claim teacher or student-edition confirmation")
    _require(isinstance(review.get("reviewer"), str) and review["reviewer"].strip()
             and isinstance(review.get("reviewed_at"), str) and review["reviewed_at"].strip()
             and bool(review.get("notes")), "Review provenance is incomplete")
    datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
    book = candidate.get("book_identity") or {}
    _require(all(book.get(key) == BOOK_IDENTITY[key] for key in ("grade", "subject", "publisher_code", "isbn", "term"))
             and book.get("publisher") == BOOK_IDENTITY["publisher_name"] and book.get("stage") == "junior_middle",
             "Unsupported or mismatched concrete textbook")
    sources = candidate.get("sources") or []
    source_ids = [source.get("id") for source in sources]
    _require(len(source_ids) >= 2 and len(source_ids) == len(set(source_ids)), "Source IDs must be unique")
    _require(all(urlsplit(source.get("url", "")).scheme == "https" for source in sources), "Sources need HTTPS URLs")
    _require(sum(bool(SHA.fullmatch(source.get("sha256", ""))) for source in sources) >= 2,
             "At least two independently retained source checksums are required")
    chapters = (candidate.get("official_table_of_contents") or {}).get("chapters") or []
    section_map = {}
    for chapter in chapters:
        _require(chapter.get("source_id") in source_ids, "Chapter source is missing")
        for section in chapter.get("sections", []):
            _require(section.get("id") not in section_map and section.get("source_id") in source_ids,
                     "Duplicate section or missing source")
            section_map[section["id"]] = chapter
    scope = candidate.get("proposed_pilot_scope") or {}
    raw_nodes, raw_edges = scope.get("knowledge_nodes") or [], scope.get("prerequisite_edges") or []
    _require(raw_nodes and len(raw_nodes) <= 500 and len(raw_edges) <= 2000, "Invalid graph size")
    ids = [node.get("id") for node in raw_nodes]
    _require(len(ids) == len(set(ids)) and all(isinstance(i, str) and IDENTIFIER.fullmatch(i) for i in ids),
             "Invalid or duplicate node ID")
    used_chapters = {}
    nodes = []
    base_ref = {"publisher": book["publisher"], "subject": book["subject"], "grade": book["grade"],
                "stage": book["stage"], "term": book["term"], "isbn": book["isbn"],
                "textbook_revision": "isbn:" + book["isbn"]}
    for index, node in enumerate(raw_nodes):
        sections = node.get("official_section_ids") or []
        _require(sections and all(section in section_map for section in sections), "Unknown official section")
        _require(node.get("node_type") in ("concept", "skill") and node.get("content_origin") == "project_ai_authored",
                 "Node origin or type is not explicit")
        _require(isinstance(node.get("name"), str) and 0 < len(node["name"]) <= 160
                 and isinstance(node.get("observable_objective"), str) and bool(node["observable_objective"].strip()),
                 "Missing node name or objective")
        chapter = section_map[sections[0]]
        used_chapters[chapter["id"]] = chapter
        nodes.append({"id": node["id"], "node_type": node["node_type"], "parent_id": chapter["id"],
                      "name": node["name"], "definition": None, "observable_objective": node["observable_objective"],
                      "curriculum_refs": [{**base_ref, "section_id": section} for section in sections],
                      "coverage_facets": [], "misconception_codes": [],
                      "sort_order": chapter["number"] * 1000 + index + 1})
    for chapter in used_chapters.values():
        _require(IDENTIFIER.fullmatch(chapter["id"]) and chapter["id"] not in ids, "Invalid chapter ID")
        nodes.append({"id": chapter["id"], "node_type": "chapter", "parent_id": None, "name": chapter["title"],
                      "definition": "已核对官方目录标题；细粒度技能由项目 AI 整理。",
                      "observable_objective": None, "curriculum_refs": [base_ref],
                      "coverage_facets": [], "misconception_codes": [], "sort_order": chapter["number"] * 1000})
    edges, edge_ids, adjacency = [], set(), {i: [] for i in ids}
    for edge in raw_edges:
        _require(isinstance(edge.get("id"), str) and IDENTIFIER.fullmatch(edge["id"]) and edge["id"] not in edge_ids,
                 "Invalid or duplicate edge ID")
        edge_ids.add(edge["id"])
        _require(edge.get("from_id") in ids and edge.get("to_id") in ids and edge["from_id"] != edge["to_id"],
                 "Edge endpoints must exist and differ")
        relation = edge.get("relation_type")
        _require(relation in {"prerequisite_of", "supports", "specializes_to"}, "Unsupported relation")
        _require(edge.get("official_relation") is False and edge.get("propagate_mastery_or_weakness") is False
                 and edge.get("gates_learning") is False and edge.get("requires_whole_source_node_mastery") is False,
                 "Relations must not imply official authority or learning gates")
        _require(bool(edge.get("applies_when")) and bool(edge.get("rationale")), "Relation needs a scope and rationale")
        edges.append({"id": edge["id"], "from_id": edge["from_id"], "to_id": edge["to_id"],
                      "relation_type": relation, "scope": edge["applies_when"], "rationale": edge["rationale"]})
        if relation == "prerequisite_of":
            adjacency[edge["from_id"]].append(edge["to_id"])
    visiting, visited = set(), set()
    def visit(node):
        _require(node not in visiting, "Prerequisite graph contains a cycle")
        if node in visited:
            return
        visiting.add(node)
        for target in adjacency[node]:
            visit(target)
        visiting.remove(node)
        visited.add(node)
    for node in ids:
        visit(node)
    metadata = {"validation_version": "book-graph-v1", "candidate_sha256": digest,
                "review_sha256": hashlib.sha256(review_bytes).hexdigest(), "publication_review": review,
                "candidate_id": candidate["candidate_id"], "candidate_revision": candidate.get("candidate_revision"),
                "candidate_snapshot": deepcopy(candidate),
                "sources": deepcopy(sources), "node_provenance": deepcopy(raw_nodes),
                "edge_provenance": deepcopy(raw_edges),
                "coverage": {key: value for key, value in scope.items() if key not in ("knowledge_nodes", "prerequisite_edges")}}
    return {"version": version, "book_identity": deepcopy(book), "review_metadata": metadata,
            "import_digest": digest, "nodes": nodes, "edges": edges}


def import_publication(session, prepared, *, publish=False):
    """Idempotent immutable import; never retag student profiles or evidence."""
    version = prepared["version"]
    graph = session.get(CurriculumGraph, version)
    if graph is not None:
        _require(graph.import_digest == prepared["import_digest"]
                 and graph.review_metadata == prepared["review_metadata"]
                 and graph.book_identity == prepared["book_identity"],
                 "Graph version already exists with different content or review; use a new version")
        actual_nodes = list(session.scalars(select(KnowledgeNode).where(KnowledgeNode.graph_version == version)))
        actual_edges = list(session.scalars(select(KnowledgeEdge).where(KnowledgeEdge.graph_version == version)))
        def content(rows, fields):
            return sorted([{field: getattr(row, field) for field in fields} for row in rows], key=lambda row: row["id"])
        edge_fields = ("id", "from_id", "to_id", "relation_type", "scope", "rationale")
        _require(content(actual_nodes, prepared["nodes"][0]) == sorted(prepared["nodes"], key=lambda row: row["id"])
                 and content(actual_edges, edge_fields) == sorted(prepared["edges"], key=lambda row: row["id"]),
                 "Imported graph contents no longer match the immutable manifest")
        _require(graph.status != "retired", "Retired graph cannot be republished; use a new version")
    else:
        graph = CurriculumGraph(version=version, subject=prepared["book_identity"]["subject"],
                                title=prepared["book_identity"]["title"], status="draft", review_status=REVIEW_STATUS,
                                book_identity=prepared["book_identity"], review_metadata=prepared["review_metadata"],
                                import_digest=prepared["import_digest"],
                                source_note="官方资源书目和目录已核对；技能与关系由项目 AI 编写并经独立 AI 审查。"
                                            "范围见审查记录；不等于教师审核、官方逐节点图谱或学生所用印次已确认。")
        session.add(graph)
        session.flush()
        session.add_all(KnowledgeNode(graph_version=version, **node) for node in prepared["nodes"])
        session.add_all(KnowledgeEdge(graph_version=version, **edge) for edge in prepared["edges"])
    if publish and graph.status != "published":
        graph.status, graph.published_at = "published", datetime.now(timezone.utc)
    session.flush()
    return {"graph_version": version, "status": graph.status, "review_status": graph.review_status,
            "nodes": len(prepared["nodes"]), "edges": len(prepared["edges"]),
            "candidate_sha256": prepared["import_digest"]}
