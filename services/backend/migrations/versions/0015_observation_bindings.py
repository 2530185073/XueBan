"""Bind machine knowledge attribution to verifiable historical observations.

Legacy evidence records where an old question-wide link was applied. This is
not proof that a model separately read every historical attempt. Keep that
distinction in basis=legacy_evidence; never rewrite the old evidence or links.
"""
from collections import defaultdict

from alembic import op
import sqlalchemy as sa

revision = "0015_observation_bindings"
down_revision = "0014_student_variants"
branch_labels = None
depends_on = None


def _tables():
    # Explicit migration-owned types, independent of future ORM changes. JSON
    # result/bind processing handles SQLite text and native PostgreSQL JSON.
    links = sa.table("question_knowledge_links", *(sa.column(name, kind) for name, kind in (
        ("id", sa.String), ("tenant_id", sa.String), ("student_id", sa.String),
        ("homework_id", sa.String), ("question_id", sa.String), ("question_revision", sa.Integer),
        ("knowledge_id", sa.String), ("graph_version", sa.String), ("role", sa.String),
        ("source", sa.String), ("status", sa.String), ("observation_bindings", sa.JSON(none_as_null=True)))))
    evidence = sa.table("learning_evidence", *(sa.column(name, kind) for name, kind in (
        ("tenant_id", sa.String), ("student_id", sa.String), ("campus_id", sa.String),
        ("knowledge_id", sa.String), ("validity", sa.String), ("document", sa.JSON))))
    entities = sa.table("correction_entities", sa.column("id", sa.String), sa.column("kind", sa.String),
        sa.column("homework_id", sa.String), sa.column("document", sa.JSON))
    homeworks = sa.table("homeworks", *(sa.column(name, sa.String) for name in
        ("id", "tenant_id", "student_id", "campus_id")))
    return links, evidence, entities, homeworks


def _object(value):
    return value if isinstance(value, dict) else {}


def _positive_integer(value):
    return type(value) is int and value >= 1


def _binding(connection, evidence, link, entities, homeworks, entity_cache, homework_cache):
    doc = _object(evidence["document"])
    if (not _positive_integer(link["question_revision"])
            or any(evidence[key] != link[key] for key in ("tenant_id", "student_id", "knowledge_id"))
            or any(doc.get(key) != link[key] for key in
                   ("question_id", "homework_id", "knowledge_id", "graph_version", "question_revision"))
            or type(doc.get("question_revision")) is not int
            or doc.get("link_role") != link["role"]):
        return None

    def entity(identifier, kind):
        if not isinstance(identifier, str) or not identifier:
            return None
        if identifier not in entity_cache:
            entity_cache[identifier] = connection.execute(sa.select(entities).where(
                entities.c.id == identifier)).mappings().first()
        row = entity_cache[identifier]
        return row if row and row["kind"] == kind and row["homework_id"] == link["homework_id"] else None

    if link["homework_id"] not in homework_cache:
        homework_cache[link["homework_id"]] = connection.execute(sa.select(homeworks).where(
            homeworks.c.id == link["homework_id"])).mappings().first()
    homework = homework_cache[link["homework_id"]]
    if (not homework or any(homework[key] != evidence[key] for key in
                            ("tenant_id", "student_id", "campus_id"))):
        return None
    attempt = entity(doc.get("attempt_id"), "attempt")
    decision = entity(doc.get("decision_id"), "decision")
    question = entity(link["question_id"], "question")
    if not attempt or not decision or not question:
        return None
    attempt_doc, decision_doc = _object(attempt["document"]), _object(decision["document"])
    versions = _object(decision_doc.get("input_versions"))
    recognition_revision = versions.get("attempt_recognition_revision")
    if (attempt_doc.get("id") != attempt["id"] or decision_doc.get("id") != decision["id"]
            or attempt_doc.get("question_id") != link["question_id"]
            or _object(question["document"]).get("question_id") != link["question_id"]
            or not isinstance(doc.get("case_id"), str) or not doc["case_id"]
            or attempt_doc.get("case_id") != doc["case_id"] or decision_doc.get("case_id") != doc["case_id"]
            or versions.get("question_id") != link["question_id"]
            or versions.get("question_revision") != link["question_revision"]
            or type(versions.get("question_revision")) is not int
            or versions.get("attempt_id") != attempt["id"]
            or not _positive_integer(recognition_revision)
            or type(attempt_doc.get("recognition_revision")) is not int
            or attempt_doc.get("recognition_revision") != recognition_revision):
        return None
    # Entity/decision revision is intentionally absent: new_attempt invalidates
    # a decision by increasing that revision without changing its observation.
    return {"attempt_id": attempt["id"], "attempt_recognition_revision": recognition_revision,
            "decision_id": decision["id"], "basis": "legacy_evidence"}


def _reconstruct_bindings(connection, links, evidence, entities, homeworks):
    """The exact scopes an upgrade can recover from currently valid evidence."""
    old_links = {row["id"]: row for row in connection.execute(sa.select(links).where(
        links.c.source == "model", links.c.status == "confirmed")).mappings()}
    bindings = defaultdict(dict)
    entity_cache, homework_cache = {}, {}
    for record in connection.execute(sa.select(evidence).where(evidence.c.validity == "valid")).mappings():
        link_id = _object(record["document"]).get("link_id")
        link = old_links.get(link_id) if isinstance(link_id, str) else None
        if link is None:
            continue
        binding = _binding(connection, record, link, entities, homeworks, entity_cache, homework_cache)
        if binding:
            key = (binding["attempt_id"], binding["attempt_recognition_revision"], binding["decision_id"])
            bindings[link_id][key] = binding
    return bindings


def upgrade():
    op.add_column("question_knowledge_links", sa.Column(
        "observation_bindings", sa.JSON(none_as_null=True), nullable=True))
    connection = op.get_bind()
    links, evidence, entities, homeworks = _tables()
    bindings = _reconstruct_bindings(connection, links, evidence, entities, homeworks)
    for link_id, values in bindings.items():
        connection.execute(links.update().where(links.c.id == link_id).values(
            observation_bindings=[values[key] for key in sorted(values)]))


def downgrade():
    connection = op.get_bind()
    links, evidence, entities, homeworks = _tables()
    recoverable = _reconstruct_bindings(connection, links, evidence, entities, homeworks)
    for row in connection.execute(sa.select(links)).mappings():
        value = row["observation_bindings"]
        if value is None:
            continue
        # "legacy_evidence" describes provenance, not reversibility. A later
        # R1 withdrawal can copy that basis onto an observation-scoped
        # candidate, which the old runtime would apply to the whole question.
        # Likewise invalidated evidence or a replaced reading cannot rebuild
        # an older confirmed scope. Refuse loss before dropping any column.
        if row["source"] != "model" or row["status"] != "confirmed" or not isinstance(value, list) or any(
                not isinstance(binding, dict)
                or set(binding) != {"attempt_id", "attempt_recognition_revision", "decision_id", "basis"}
                or binding.get("basis") != "legacy_evidence"
                or not _positive_integer(binding.get("attempt_recognition_revision"))
                or any(not isinstance(binding.get(key), str) or not binding[key]
                       for key in ("attempt_id", "decision_id")) for binding in value):
            raise RuntimeError("Cannot downgrade observation bindings while direct or unknown attribution exists")
        stored = {(binding["attempt_id"], binding["attempt_recognition_revision"], binding["decision_id"]): binding
                  for binding in value}
        if stored != recoverable.get(row["id"], {}):
            raise RuntimeError("Cannot downgrade observation bindings that current valid evidence cannot reconstruct")
    with op.batch_alter_table("question_knowledge_links") as batch:
        batch.drop_column("observation_bindings")
