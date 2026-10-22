"""Pure selection of staff question links and observation-bound machine links.

Bindings identify an observation; they do not judge an answer, prove that a
model read historical work, or establish independent learning. Callers select
the authoritative decision and filter the student's question before calling.
"""
from collections.abc import Mapping
from datetime import datetime, timezone


BINDING_FIELDS = frozenset(("attempt_id", "attempt_recognition_revision", "decision_id", "basis"))
BINDING_BASES = frozenset(("decision", "legacy_evidence"))
IDENTITY_FIELDS = ("attempt_id", "attempt_recognition_revision", "decision_id")


def _field(row, name, default=None):
    return row.get(name, default) if isinstance(row, Mapping) else getattr(row, name, default)


def _document(row):
    document = _field(row, "document", row)
    return document if isinstance(document, Mapping) else {}


def _identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 64 and value == value.strip()


def _valid_binding(value):
    return (isinstance(value, Mapping) and set(value) == BINDING_FIELDS
            and _identifier(value.get("attempt_id")) and _identifier(value.get("decision_id"))
            and type(value.get("attempt_recognition_revision")) is int
            and value["attempt_recognition_revision"] > 0
            and isinstance(value.get("basis"), str) and value["basis"] in BINDING_BASES)


def observation_binding(attempt, decision, *, basis="decision"):
    """Build a checked identity without a mutable decision revision.

    A decision may become stale solely because a new attempt was submitted;
    its revision then increases but its historical observation stays the same.
    Whether that decision still counts belongs to authoritative_decision.
    """
    a, d = _document(attempt), _document(decision)
    result = {"attempt_id": a.get("id"), "attempt_recognition_revision": a.get("recognition_revision"),
              "decision_id": d.get("id"), "basis": basis}
    versions = d.get("input_versions")
    if (not _valid_binding(result) or not isinstance(versions, Mapping)
            or versions.get("attempt_id") != result["attempt_id"]
            or versions.get("attempt_recognition_revision") != result["attempt_recognition_revision"]):
        raise ValueError("invalid_observation_binding")
    for left, right in ((a.get("case_id"), d.get("case_id")),
                        (a.get("question_id"), versions.get("question_id"))):
        if (any(value is not None and not _identifier(value) for value in (left, right))
                or left is not None and right is not None and left != right):
            raise ValueError("invalid_observation_binding")
    # Entity wrappers must agree with their own documents. Do not add a stale
    # wrapper's identity to otherwise valid input from another observation.
    if any(_field(row, "id") != document.get("id") for row, document in ((attempt, a), (decision, d))):
        raise ValueError("invalid_observation_binding")
    return result


def binding_matches(bindings, attempt, decision):
    """Unknown legacy provenance never becomes a current observation binding."""
    if not isinstance(bindings, list):
        return False
    try:
        expected = observation_binding(attempt, decision)
    except ValueError:
        return False
    return any(_valid_binding(row) and all(row[key] == expected[key] for key in IDENTITY_FIELDS)
               for row in bindings)


def _order(row):
    value = _field(row, "created_at")
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime) or not _identifier(_field(row, "id")):
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc), _field(row, "id")
    except ValueError:
        return None


def select_current_links(links, *, question_revision, graph_version, attempt=None, decision=None):
    """Return original rows; latest is evaluated within each authority/scope.

    The input must belong to one question. Staff confirmation or rejection is
    a separate question-level authority, so a later machine row cannot replace
    it. A confirmed staff primary takes precedence over all machine attribution.
    No attempt/decision means only staff links can be selected.
    """
    if (type(question_revision) is not int or question_revision <= 0
            or not isinstance(graph_version, str) or not graph_version):
        return {}
    links = list(links)
    if any(not _identifier(_field(row, "question_id")) for row in links):
        return {}
    question_ids = {_field(row, "question_id") for row in links}
    if len(question_ids) > 1:
        return {}
    a, d = _document(attempt), _document(decision)
    versions = d.get("input_versions")
    versions = versions if isinstance(versions, Mapping) else {}
    for question_id in (a.get("question_id"), versions.get("question_id")):
        if question_id is not None and (not _identifier(question_id) or question_ids and question_id not in question_ids):
            return {}
    latest_staff, latest_model = {}, {}
    ordered = [(key, row) for row in links if (key := _order(row)) is not None]
    for _, row in sorted(ordered, key=lambda pair: pair[0]):
        if (type(_field(row, "question_revision")) is not int
                or _field(row, "question_revision") != question_revision or _field(row, "graph_version") != graph_version
                or _field(row, "status") not in ("confirmed", "candidate", "rejected")
                or _field(row, "role") not in ("primary", "secondary")
                or not _identifier(_field(row, "knowledge_id"))):
            continue
        source = _field(row, "source")
        if source == "staff":
            latest_staff[_field(row, "knowledge_id")] = row
        elif source == "model" and binding_matches(_field(row, "observation_bindings"), attempt, decision):
            latest_model[_field(row, "knowledge_id")] = row
    if any(_field(row, "status") == "confirmed" and _field(row, "role") == "primary"
           for row in latest_staff.values()):
        latest_model = {}
    return {**latest_model, **latest_staff}
