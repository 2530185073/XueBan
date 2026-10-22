"""Real SQLite/PostgreSQL migration: preserve history, never invent scope."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from conftest import migrate
from xueban_backend.correction_models import Entity
from xueban_backend.learning_models import LearningEvidence, QuestionKnowledgeLink
from xueban_backend.models import Homework


PREVIOUS = "0014_student_variants"
NOW = datetime(2026, 10, 21, 9, tzinfo=timezone.utc)
FIELD = "observation_bindings"
GRAPH = "pep-math-junior-pilot-1"
KNOWLEDGE = "kp-rational-mul-div"


def old_links():
    return sa.table("question_knowledge_links", *(
        sa.column(column.name, column.type) for column in QuestionKnowledgeLink.__table__.columns
        if column.name != FIELD))


def homework(identifier="h1", **changes):
    return dict(id=identifier, tenant_id="tenant_a", campus_id="campus_a", student_id="student_a",
                revision=1, curriculum={}, title="迁移合成作业", collection_scope="partial",
                created_at=NOW, occurred_at=NOW, processing_blocked=False, **changes)


def records(prefix="one"):
    question_id, attempt_id, decision_id, link_id = [f"{prefix}_{kind}" for kind in ("q", "a", "d", "l")]
    question = dict(id=question_id, homework_id="h1", kind="question", revision=1,
                    document={"question_id": question_id, "revision": 1})
    attempt = dict(id=attempt_id, homework_id="h1", kind="attempt", revision=2,
                   document={"id": attempt_id, "question_id": question_id, "case_id": f"{prefix}_case",
                             "recognition_revision": 1, "help_condition": "staff_help"})
    decision = dict(id=decision_id, homework_id="h1", kind="decision", revision=3,
                    document={"id": decision_id, "revision": 3, "case_id": f"{prefix}_case",
                              "origin": "model", "validity": "stale", "verdict": "incorrect",
                              "invalidated_by_event_id": "historical_new_attempt_event",
                              "input_versions": {"question_id": question_id, "question_revision": 1,
                                  "attempt_id": attempt_id, "attempt_recognition_revision": 1}})
    link = dict(id=link_id, tenant_id="tenant_a", student_id="student_a", homework_id="h1",
                question_id=question_id, question_revision=1, knowledge_id=KNOWLEDGE, graph_version=GRAPH,
                role="primary", source="model", status="confirmed", rationale="原始归因保留",
                actor_id=None, supersedes_id="old-link-reference", created_at=NOW)
    evidence = dict(id=f"{prefix}_e", tenant_id="tenant_a", campus_id="campus_a", student_id="student_a",
                    knowledge_id=KNOWLEDGE, source_key=f"source_{prefix}", validity="valid",
                    occurred_at=NOW, recorded_at=NOW, invalidated_at=None, invalidation_reason=None,
                    document={"link_id": link_id, "link_role": "primary", "knowledge_id": KNOWLEDGE,
                              "graph_version": GRAPH, "question_id": question_id, "question_revision": 1,
                              "homework_id": "h1", "case_id": f"{prefix}_case", "attempt_id": attempt_id,
                              "decision_id": decision_id, "result": "incorrect", "independent": False,
                              "source_role": "model", "rule_version": "r1.4-versioned-knowledge"})
    return {"question": question, "attempt": attempt, "decision": decision, "link": link, "evidence": evidence}


def insert_records(connection, value, *, evidence=True):
    connection.execute(Entity.__table__.insert(), [value[key] for key in ("question", "attempt", "decision")])
    connection.execute(old_links().insert(), value["link"])
    if evidence:
        connection.execute(LearningEvidence.__table__.insert(), value["evidence"])


def snapshot(connection):
    return {table.name: [dict(row) for row in connection.execute(sa.select(table).order_by(table.c.id)).mappings()]
            for table in (old_links(), LearningEvidence.__table__, Entity.__table__, Homework.__table__)}


def binding(attempt, decision):
    return {"attempt_id": attempt["id"], "attempt_recognition_revision": attempt["document"]["recognition_revision"],
            "decision_id": decision["id"], "basis": "legacy_evidence"}


def test_upgrade_downgrade_reupgrade_preserves_all_old_rows_and_deduplicates_scopes(database):
    migrate(database.url, PREVIOUS, downgrade=True)
    value = records()
    staff, no_evidence = records("staff"), records("unproven")
    staff["link"]["source"] = "staff"
    staff["link"]["actor_id"] = "actor_a"
    second_attempt, second_decision = deepcopy(value["attempt"]), deepcopy(value["decision"])
    second_attempt.update(id="one_a2", revision=4)
    second_attempt["document"].update(id="one_a2", recognition_revision=2)
    second_decision.update(id="one_d2", revision=1)
    second_decision["document"].update(id="one_d2", revision=1, validity="candidate")
    second_decision["document"]["input_versions"].update(attempt_id="one_a2", attempt_recognition_revision=2)
    second_evidence = deepcopy(value["evidence"])
    second_evidence.update(id="one_e2", source_key="source_one_2")
    second_evidence["document"].update(attempt_id="one_a2", decision_id="one_d2")
    duplicate = {**deepcopy(second_evidence), "id": "one_e2_duplicate"}
    with database.engine.begin() as connection:
        connection.execute(Homework.__table__.insert(), homework())
        insert_records(connection, value)
        insert_records(connection, staff)
        insert_records(connection, no_evidence, evidence=False)
        connection.execute(Entity.__table__.insert(), [second_attempt, second_decision])
        connection.execute(LearningEvidence.__table__.insert(), [second_evidence, duplicate])
        before = snapshot(connection)
    expected = [binding(value["attempt"], value["decision"]), binding(second_attempt, second_decision)]
    expected.sort(key=lambda row: (row["attempt_id"], row["attempt_recognition_revision"], row["decision_id"]))
    for cycle in range(2):
        migrate(database.url)
        migrate(database.url)  # Existing bindings are not rewritten by a no-op upgrade.
        assert next(column for column in sa.inspect(database.engine).get_columns("question_knowledge_links")
                    if column["name"] == FIELD)["nullable"] is True
        with Session(database.engine) as session:
            assert session.get(QuestionKnowledgeLink, value["link"]["id"]).observation_bindings == expected
            assert session.get(QuestionKnowledgeLink, staff["link"]["id"]).observation_bindings is None
            assert session.get(QuestionKnowledgeLink, no_evidence["link"]["id"]).observation_bindings is None
        with database.engine.connect() as connection:
            assert snapshot(connection) == before
            assert connection.execute(sa.text(
                "SELECT count(*) FROM question_knowledge_links WHERE observation_bindings IS NULL")).scalar_one() == 2
        if cycle == 0:
            migrate(database.url, PREVIOUS, downgrade=True)
            assert FIELD not in {c["name"] for c in sa.inspect(database.engine).get_columns("question_knowledge_links")}
            with database.engine.connect() as connection:
                assert snapshot(connection) == before


def test_invalid_or_unverifiable_legacy_rows_remain_sql_null(database):
    migrate(database.url, PREVIOUS, downgrade=True)
    mutations = {
        "invalidated_evidence": lambda r: r["evidence"].update(validity="invalidated"),
        "staff_link": lambda r: r["link"].update(source="staff"),
        "candidate_link": lambda r: r["link"].update(status="candidate"),
        "rejected_link": lambda r: r["link"].update(status="rejected"),
        "json_null_doc": lambda r: r["evidence"].update(document=None),
        "array_doc": lambda r: r["evidence"].update(document=[]),
        "string_doc": lambda r: r["evidence"].update(document="{}"),
        "missing_link": lambda r: r["evidence"]["document"].update(link_id="absent"),
        "object_link": lambda r: r["evidence"]["document"].update(link_id={"id": r["link"]["id"]}),
        "different_student": lambda r: r["evidence"].update(student_id="student_ungranted"),
        "different_tenant": lambda r: r["evidence"].update(tenant_id="tenant_b", student_id="student_b"),
        "different_campus": lambda r: r["evidence"].update(campus_id="campus_b"),
        "different_knowledge_column": lambda r: r["evidence"].update(knowledge_id="kp-rational-add-sub"),
        "link_wrong_owner": lambda r: (r["link"].update(student_id="student_ungranted"),
                                         r["evidence"].update(student_id="student_ungranted")),
        "question_wrong_homework": lambda r: r["question"].update(homework_id="h2"),
        "attempt_wrong_homework": lambda r: r["attempt"].update(homework_id="h2"),
        "decision_wrong_homework": lambda r: r["decision"].update(homework_id="h2"),
        "attempt_wrong_kind": lambda r: r["attempt"].update(kind="hint"),
        "decision_wrong_kind": lambda r: r["decision"].update(kind="hint"),
        "question_wrong_kind": lambda r: r["question"].update(kind="hint"),
        "attempt_revision_changed": lambda r: r["attempt"]["document"].update(recognition_revision=2),
        "attempt_revision_bool": lambda r: r["attempt"]["document"].update(recognition_revision=True),
        "attempt_wrong_id": lambda r: r["attempt"]["document"].update(id="different"),
        "attempt_wrong_question": lambda r: r["attempt"]["document"].update(question_id="different"),
        "attempt_wrong_case": lambda r: r["attempt"]["document"].update(case_id="different"),
        "question_wrong_id": lambda r: r["question"]["document"].update(question_id="different"),
        "decision_wrong_id": lambda r: r["decision"]["document"].update(id="different"),
        "decision_wrong_case": lambda r: r["decision"]["document"].update(case_id="different"),
        "no_input_versions": lambda r: r["decision"]["document"].update(input_versions=None),
        "array_input_versions": lambda r: r["decision"]["document"].update(input_versions=[]),
        "invalid_question_revision": lambda r: (r["link"].update(question_revision=0),
            r["evidence"]["document"].update(question_revision=0),
            r["decision"]["document"]["input_versions"].update(question_revision=0)),
    }
    for key, replacement in (("question_id", "other-question"), ("homework_id", "h2"),
                             ("knowledge_id", "other-knowledge"), ("graph_version", "other-graph"),
                             ("question_revision", "1"), ("question_revision", True),
                             ("link_role", "secondary"), ("attempt_id", "absent"),
                             ("decision_id", "absent"), ("case_id", None)):
        mutations[f"evidence_{key}_{replacement}"] = lambda r, k=key, v=replacement: r["evidence"]["document"].update({k: v})
    for key, replacement in (("question_id", "other-question"), ("question_revision", 2),
                             ("question_revision", "1"), ("question_revision", True),
                             ("attempt_id", "other-attempt"), ("attempt_recognition_revision", 0),
                             ("attempt_recognition_revision", "1"), ("attempt_recognition_revision", True)):
        mutations[f"versions_{key}_{replacement}"] = lambda r, k=key, v=replacement: r["decision"]["document"]["input_versions"].update({k: v})
    with database.engine.begin() as connection:
        connection.execute(Homework.__table__.insert(), [homework(), {**homework("h2"), "student_id": "student_ungranted"}])
        for number, mutate in enumerate(mutations.values()):
            value = records(f"bad{number}")
            mutate(value)
            insert_records(connection, value)
        control = records("control")
        insert_records(connection, control)
        before = snapshot(connection)
    migrate(database.url)
    with database.engine.connect() as connection:
        rows = list(connection.execute(sa.select(QuestionKnowledgeLink.__table__)).mappings())
        assert len(rows) == len(mutations) + 1
        for row in rows:
            assert row[FIELD] == ([binding(control["attempt"], control["decision"])] if row["id"] == control["link"]["id"] else None), row["id"]
        assert snapshot(connection) == before
        assert connection.execute(sa.text(
            "SELECT count(*) FROM question_knowledge_links WHERE observation_bindings IS NULL")).scalar_one() == len(mutations)


def test_new_json_bindings_and_true_null_round_trip_without_string_encoding(database):
    value = records()
    with database.engine.begin() as connection:
        connection.execute(Homework.__table__.insert(), homework())
        insert_records(connection, value, evidence=False)
    direct = {**binding(value["attempt"], value["decision"]), "basis": "decision"}
    with Session(database.engine) as session:
        row = session.get(QuestionKnowledgeLink, value["link"]["id"])
        assert row.observation_bindings is None
        row.observation_bindings = [direct]
        session.commit()
    with Session(database.engine) as session:
        row = session.get(QuestionKnowledgeLink, value["link"]["id"])
        assert row.observation_bindings == [direct]
        assert isinstance(row.observation_bindings, list) and isinstance(row.observation_bindings[0], dict)
        row.observation_bindings = None
        session.commit()
    with database.engine.connect() as connection:
        assert connection.execute(sa.text(
            "SELECT observation_bindings IS NULL FROM question_knowledge_links WHERE id=:id"),
            {"id": value["link"]["id"]}).scalar_one()


@pytest.mark.parametrize("stored", ["direct", "malformed", "malformed_legacy"])
def test_downgrade_refuses_to_discard_direct_or_unknown_binding_scope(database, stored):
    value = records()
    with database.engine.begin() as connection:
        connection.execute(Homework.__table__.insert(), homework())
        insert_records(connection, value)
        direct = {**binding(value["attempt"], value["decision"]), "basis": "decision"}
        content = {"direct": [direct], "malformed": {"unexpected": "object"},
                   "malformed_legacy": [{"basis": "legacy_evidence"}]}[stored]
        connection.execute(QuestionKnowledgeLink.__table__.update().values(observation_bindings=content))
        before = snapshot(connection)
    with pytest.raises(RuntimeError, match="Cannot downgrade observation bindings"):
        migrate(database.url, PREVIOUS, downgrade=True)
    with database.engine.connect() as connection:
        assert snapshot(connection) == before
        assert connection.execute(sa.select(QuestionKnowledgeLink.observation_bindings)).scalar_one() == content
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0015_observation_bindings"


def test_json_null_is_not_misread_as_string_or_legacy_binding_on_downgrade(database):
    value = records()
    with database.engine.begin() as connection:
        connection.execute(Homework.__table__.insert(), homework())
        insert_records(connection, value, evidence=False)
        connection.execute(QuestionKnowledgeLink.__table__.update().values(observation_bindings=sa.JSON.NULL))
        assert connection.execute(sa.select(QuestionKnowledgeLink.observation_bindings)).scalar_one() is None
        assert not connection.execute(sa.text("SELECT observation_bindings IS NULL FROM question_knowledge_links")).scalar_one()
    migrate(database.url, PREVIOUS, downgrade=True)
    migrate(database.url)
    with database.engine.connect() as connection:
        assert connection.execute(sa.text("SELECT observation_bindings IS NULL FROM question_knowledge_links")).scalar_one()


@pytest.mark.parametrize("change", ["candidate_withdrawal", "staff", "evidence_invalidated", "reading_changed", "scope_changed"])
def test_downgrade_refuses_legacy_scope_that_cannot_be_reconstructed(database, change):
    migrate(database.url, PREVIOUS, downgrade=True)
    value = records()
    with database.engine.begin() as connection:
        connection.execute(Homework.__table__.insert(), homework())
        insert_records(connection, value)
    migrate(database.url)
    with Session(database.engine) as session:
        link = session.get(QuestionKnowledgeLink, value["link"]["id"])
        assert link.observation_bindings == [binding(value["attempt"], value["decision"])]
        if change == "candidate_withdrawal":
            # R1 preserves provenance on the append-only withdrawal. The
            # candidate has a narrower scope than its original confirmed row.
            session.add(QuestionKnowledgeLink(**{
                **value["link"], "id": "r1-withdrawal", "status": "candidate", "supersedes_id": link.id,
                "observation_bindings": deepcopy(link.observation_bindings)}))
        elif change == "staff":
            link.source = "staff"
        elif change == "evidence_invalidated":
            session.get(LearningEvidence, value["evidence"]["id"]).validity = "invalidated"
        elif change == "reading_changed":
            attempt = session.get(Entity, value["attempt"]["id"])
            attempt.document = {**attempt.document, "recognition_revision": 2}
        else:
            link.observation_bindings = [{**link.observation_bindings[0], "decision_id": "unrecoverable-decision"}]
        session.commit()
    with database.engine.connect() as connection:
        before = snapshot(connection)
        bindings_before = dict(connection.execute(sa.select(
            QuestionKnowledgeLink.id, QuestionKnowledgeLink.observation_bindings)).all())
    with pytest.raises(RuntimeError, match="Cannot downgrade observation bindings"):
        migrate(database.url, PREVIOUS, downgrade=True)
    with database.engine.connect() as connection:
        assert snapshot(connection) == before
        assert dict(connection.execute(sa.select(
            QuestionKnowledgeLink.id, QuestionKnowledgeLink.observation_bindings)).all()) == bindings_before
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0015_observation_bindings"
