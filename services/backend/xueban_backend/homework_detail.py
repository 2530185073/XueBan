"""Bounded, read-only same-snapshot homework details; no model or write path."""
from contextlib import contextmanager
import json

from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .correction_api import public_entity_read
from .correction_models import Asset, Entity, Task
from .errors import Problem

PROTOCOL = "homework-detail.v1"
MAX_CASES = 300
MAX_ROWS = 2000  # New aggregate budget, including assets and historical entities.
MAX_BYTES = 2_000_000
MAX_REPLACEMENT_DEPTH = 8  # Existing student traversal bound; never truncate.


@contextmanager
def readonly_session(engine):
    """Isolation/read-only is configured before authentication performs any SQL."""
    with engine.connect() as connection:
        postgres = connection.dialect.name == "postgresql"
        if postgres:
            connection = connection.execution_options(
                isolation_level="REPEATABLE READ", postgresql_readonly=True)
        else:
            # SQLite is an explicitly configured test substitute. Real PG
            # concurrent-snapshot tests remain required for the production claim.
            connection.exec_driver_sql("PRAGMA query_only = ON")
        try:
            with Session(bind=connection, expire_on_commit=False, autoflush=False) as session:
                yield session
                session.rollback()
        finally:
            connection.rollback()
            if not postgres:
                connection.exec_driver_sql("PRAGMA query_only = OFF")
                connection.rollback()


def _stale():
    raise Problem(409, "conflict", "stale_input", "作业详情的引用或版本不完整，请刷新后再查看。")


def _limit():
    raise Problem(409, "conflict", "detail_bundle_limit", "作业详情超出单次读取范围，请使用逐项读取。")


def _schema(contracts, name, document):
    try:
        contracts.validate(name, document)
    except Problem:
        _stale()


def _lists(document, names):
    for name in names:
        values = document[name]
        if len(values) != len(set(values)):
            _stale()


def detail_document(session, contracts, homework):
    """Load once, validate the complete public-reference closure, then serialize."""
    from .app import homework_json
    loaded, remaining = [], MAX_ROWS
    for model in (Entity, Asset, Task):
        rows = list(session.scalars(select(model).where(model.homework_id == homework.id)
                                   .order_by(model.id).limit(remaining + 1)))
        if len(rows) > remaining:
            _limit()
        loaded.append(rows)
        remaining -= len(rows)
    entities, assets, tasks = loaded
    by_id = {row.id: row for row in entities}
    asset_ids = {row.id for row in assets}
    jobs = {row.id: row for row in tasks}
    cases = {row.id: row for row in entities if row.kind == "case"}
    if len(cases) > MAX_CASES:
        _limit()
    scope = {"tenant_id": homework.tenant_id, "campus_id": homework.campus_id,
             "student_id": homework.student_id}

    def entity(identifier, kind):
        row = by_id.get(identifier)
        if (row is None or row.kind != kind or row.homework_id != homework.id
                or not isinstance(row.document, dict)
                or row.document.get("question_id" if kind == "question" else "id") != row.id
                or row.document.get("revision", 1) != row.revision):
            _stale()
        return row

    def regions(document, field):
        if any(region["asset_id"] not in asset_ids for region in document.get(field, [])):
            _stale()

    for asset in assets:
        if (asset.document.get("id") != asset.id
                or asset.document.get("homework_id") != homework.id
                or asset.document.get("revision") != asset.revision):
            _stale()
    # All referenced question IDs in HomeworkSession also need local identity.
    for row in entities:
        if row.kind == "question":
            question = entity(row.id, "question")
            if question.document.get("homework_id") != homework.id:
                _stale()
    for task in tasks:
        _schema(contracts, "Job", task.document)
        if (task.document["id"] != task.id or task.document["revision"] != task.revision
                or task.document["kind"] != task.kind or task.document["status"] != task.status):
            _stale()
        result = task.document["result"]
        if result:
            if result["resource_type"] == "homework":
                if result["resource_id"] != homework.id:
                    _stale()
            else:
                # Current homework tasks return same-homework correction entities.
                entity(result["resource_id"], result["resource_type"])

    for case_id, case in cases.items():
        case = entity(case_id, "case")
        document = case.document
        _schema(contracts, "CorrectionCase", document)
        if document["scope"] != scope or document["homework_id"] != homework.id:
            _stale()
        _lists(document, ("attempt_ids", "check_ids", "hint_ids", "help_request_ids", "review_ids", "replacement_case_ids"))
        entity(document["question_id"], "question")
        if document["root_attempt_id"] not in document["attempt_ids"] or document["current_attempt_id"] not in document["attempt_ids"]:
            _stale()
        for kind, field in (("attempt", "attempt_ids"), ("check", "check_ids"),
                            ("hint", "hint_ids"), ("help", "help_request_ids"), ("review", "review_ids")):
            for identifier in document[field]:
                record = entity(identifier, kind).document
                if record.get("case_id") != case_id:
                    _stale()
                if kind == "attempt" and record.get("question_id") != document["question_id"]:
                    _stale()
        for identifier in document["replacement_case_ids"]:
            entity(identifier, "case")
        decision = document["current_decision"]
        if decision:
            stored = entity(decision["id"], "decision")
            versions = decision["input_versions"]
            if (stored.document != decision
                    or decision["case_id"] != case_id or stored.document.get("case_id") != case_id
                    or versions["question_id"] != document["question_id"]
                    or versions["attempt_id"] not in document["attempt_ids"]):
                _stale()
            if decision["check_id"] is not None and decision["check_id"] not in document["check_ids"]:
                _stale()
    # A bad closure is not a capacity condition. Never omit a cycle or orphan.
    visiting, depths = set(), {}
    def depth(identifier):
        if identifier in visiting:
            _stale()
        if identifier in depths:
            return depths[identifier]
        visiting.add(identifier)
        case = cases[identifier].document
        children = case["replacement_case_ids"] if case["lifecycle"] == "superseded" else []
        value = max((1 + depth(child) for child in children), default=0)
        visiting.remove(identifier)
        if value > MAX_REPLACEMENT_DEPTH:
            _stale()
        depths[identifier] = value
        return value
    for identifier in cases:
        depth(identifier)

    homework_doc = homework_json(homework, entities=entities, assets=assets, job_ids=list(jobs))
    _schema(contracts, "HomeworkSession", homework_doc)
    if set(homework_doc["case_ids"]) != set(cases) or len(homework_doc["case_ids"]) != len(cases):
        _stale()
    questions, attempts, checks = {}, {}, {}
    for case_id in homework_doc["case_ids"]:
        case = cases[case_id]
        document = case.document
        if document["lifecycle"] != "active":
            continue
        question = entity(document["question_id"], "question")
        attempt = entity(document["current_attempt_id"], "attempt")
        q = public_entity_read(question, "question")
        a = public_entity_read(attempt, "attempt", case=case)
        _schema(contracts, "QuestionRead", q)
        _schema(contracts, "AttemptRead", a)
        if (not q["currently_effective"] or not a["currently_effective"]
                or q["record"]["revision"] != document["current_question_revision"]
                or a["record"]["root_attempt_id"] != document["root_attempt_id"]):
            _stale()
        regions(q["record"], "regions")
        regions(a["record"], "answer_regions")
        if not set(a["record"]["asset_ids"]) <= asset_ids:
            _stale()
        questions[question.id], attempts[attempt.id] = q, a
        for identifier in document["check_ids"]:
            check = entity(identifier, "check").document
            _schema(contracts, "CheckRun", check)
            versions = check["input_versions"]
            if (check["job_id"] not in jobs or versions["question_id"] != document["question_id"]
                    or versions["attempt_id"] not in document["attempt_ids"]):
                _stale()
            job = jobs[check["job_id"]]
            if (job.kind in {"check", "recognize_attempt", "recognition_recheck"}
                    and job.inputs.get("case_id") != case_id):
                _stale()
            if job.kind == "check" and job.inputs.get("check_id") != identifier:
                _stale()
            # Historical check versions remain historical; the client selects
            # matching current inputs with its existing rule, not array order.
            checks[identifier] = check
    return {"protocol": PROTOCOL, "homework": homework_doc,
            "case_records": [cases[i].document for i in homework_doc["case_ids"]],
            "questions": list(questions.values()), "attempts": list(attempts.values()),
            "checks": list(checks.values()), "jobs": [task.document for task in tasks]}


def detail_response(session, contracts, homework):
    document = detail_document(session, contracts, homework)
    _schema(contracts, "HomeworkDetailRead", document)
    body = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_BYTES:
        _limit()
    return Response(body, media_type="application/json")
