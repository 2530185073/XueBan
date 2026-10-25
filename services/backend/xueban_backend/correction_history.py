"""Read the real parent chain without reviving withdrawn judgments."""
from .learning_rules import authoritative_decision


def prior_attempts(case, attempts, decisions, stale_reasons):
    """Return prior submissions and their usable judgments, newest first.

    A changed current/root ID alone does not establish a correction. Every
    parent must belong to this case and the chain must reach its saved root.
    Decisions replaced by review or invalidated by input edits stay withdrawn.
    """
    current_id, root_id = case.get("current_attempt_id"), case.get("root_attempt_id")
    if not current_id or not root_id or current_id == root_id:
        return []
    current, seen, result = attempts.get(current_id), {current_id}, []
    if (not current or current.get("case_id") != case.get("id")
            or current.get("question_id") != case.get("question_id")):
        return []
    matching = [d for d in decisions if d.get("case_id") == case.get("id")
                and d.get("input_versions", {}).get("question_revision") == case.get("current_question_revision")]
    while current:
        parent_id = current.get("parent_attempt_id")
        parent = attempts.get(parent_id)
        if (not parent or parent_id in seen or parent.get("case_id") != case.get("id")
                or parent.get("question_id") != case.get("question_id")):
            return []
        seen.add(parent_id)
        decision = authoritative_decision(parent_id, matching, stale_reasons)
        result.append((parent, decision))
        if parent_id == root_id:
            return result
        current = parent
    return []
