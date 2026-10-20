"""Pure, versioned rules: correction records -> evidence -> knowledge state.

No I/O. The projector feeds full current records and stores the outputs, so a
rebuild from scratch and an incremental run produce the same answer.

Evidence rules (blueprint 02 section 4/5):
* A usable staff decision or a model candidate with a scorable verdict counts.
  The student-facing mistake review can start from the AI result; unresolved
  cases and decisions withdrawn by a later review, question or answer revision
  never do.
* A usable decision made stale only because the learner submitted a correction
  remains a true fact about that earlier attempt.
* A correction is never independent evidence. A first attempt is independent
  only when staff confirmed it was done without hints, adult or peer help.
* A wrong answer on a multi-knowledge question is negative evidence only for the
  link staff marked as primary (the located error); secondary links get it as
  unscorable. A correct answer supports every confirmed link.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import hashlib
import json
from zoneinfo import ZoneInfo

RULE_VERSION = "r1.3-evidence-source"
SHANGHAI = ZoneInfo("Asia/Shanghai")
STABLE_MIN_SPAN = timedelta(hours=24)
DELAYED_AFTER = timedelta(days=7)
REVIEW_HINT_AFTER = timedelta(days=14)
WRONG = {"incorrect", "partial"}
RESULT = {"correct": "correct", "incorrect": "incorrect", "partially_correct": "partial", "unanswered": "unscorable"}
HELP_TO_ASSISTANCE = {"none_reported": "no_recorded_help", "hint_used": "direction", "staff_help": "staff_help",
                      "answer_shown": "solution_seen", "unknown": "unknown"}


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _key(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def model_decision_usable(decision: dict) -> bool:
    """Keep provisional AI evidence, but never promote unresolved observations.

    Older single-model decisions have no verification field. They remain
    explicitly model-sourced evidence; when independent verification is
    present, it must have completed without a remaining disagreement.
    """
    if decision.get("uncertainties") or decision.get("recognition_uncertainties"):
        return False
    if decision.get("reason_code") in {"unclear_image", "recognition_conflict", "verification_conflict", "needs_staff"}:
        return False
    verification = decision.get("verification")
    if verification is not None:
        if not isinstance(verification, dict) or verification.get("status") not in {"matched", "reviewed"}:
            return False
        if verification.get("reason_codes") or verification.get("independent_readback") is not True:
            return False
    return True


def authoritative_decision(attempt_id: str, decisions: list[dict], stale_reasons: dict[str, str]) -> dict | None:
    """Keep the latest decision only, including history superseded by a new attempt."""
    candidates = [d for d in decisions if d["input_versions"]["attempt_id"] == attempt_id
                  and d["origin"] in {"staff", "model"}]
    if not candidates:
        return None
    latest = max(candidates, key=lambda d: (d["created_at"], d["id"]))
    if latest["origin"] == "model" and not model_decision_usable(latest):
        return None
    if latest["origin"] == "staff" and latest["validity"] == "confirmed":
        return latest
    if latest["validity"] == "stale" and stale_reasons.get(latest.get("invalidated_by_event_id")) == "new_attempt":
        return latest
    return latest if latest["origin"] == "model" and latest["validity"] == "candidate" else None


def hints_seen_before(attempt: dict, hints: list[dict]) -> list[dict]:
    """Hints for the same case the learner saw no later than this attempt; later help belongs to later work."""
    occurred = parse(attempt["occurred_at"])
    return [h for h in hints if h.get("first_shown_at") and parse(h["first_shown_at"]) <= occurred]


def assistance(attempt: dict, hints: list[dict], condition: str | None) -> str:
    if condition:
        return condition
    shown = hints_seen_before(attempt, hints)
    if any(h.get("exposes_answer") for h in shown):
        return "solution_seen"
    if any(h["level"] == "key_step" for h in shown):
        return "key_step"
    if shown:
        return "direction"
    help_condition = attempt.get("help_condition", "unknown")
    if help_condition in ("hint_used", "answer_shown"):
        # Set when a hint was viewed after this attempt; what the learner reported at the time is unknown.
        return "unknown"
    return HELP_TO_ASSISTANCE.get(help_condition, "unknown")


def derive_evidence(*, cases, attempts, decisions, hints, questions, links, conditions, stale_reasons):
    """Return evidence documents keyed by a content hash of every input that matters."""
    attempts_by_id = {a["id"]: a for a in attempts}
    hints_by_case = defaultdict(list)
    for hint in hints:
        hints_by_case[hint["case_id"]].append(hint)
    current_links = defaultdict(dict)
    for link in sorted(links, key=lambda row: (row["created_at"], row["id"])):
        current_links[link["question_id"]][link["knowledge_id"]] = link
    output = []
    for case in cases:
        if case.get("lifecycle", "active") != "active":
            continue
        question = questions.get(case["question_id"])
        confirmed = [l for l in current_links.get(case["question_id"], {}).values() if l["status"] == "confirmed"]
        if question is None or not confirmed:
            continue
        family = question.get("bank_question_id") or question["question_id"]
        for attempt_id in case["attempt_ids"]:
            attempt = attempts_by_id.get(attempt_id)
            if attempt is None:
                continue
            decision = authoritative_decision(attempt_id, decisions, stale_reasons)
            if decision is None:
                continue
            versions = decision["input_versions"]
            if (versions.get("question_revision") != question.get("revision")
                    or versions.get("attempt_recognition_revision") != attempt.get("recognition_revision")):
                continue
            if decision["origin"] == "model" and (
                    attempt.get("recognition_status") != "ready"
                    or attempt.get("answer_status") == "unclear"
                    or attempt.get("recognition_uncertainties")
                    or question.get("recognition_uncertainties")):
                continue
            result = RESULT.get(decision["verdict"])
            if result is None:
                continue
            event_type = "school_first_attempt" if attempt["parent_attempt_id"] is None else "correction"
            level = assistance(attempt, hints_by_case[case["id"]], conditions.get(attempt_id))
            independent = event_type == "school_first_attempt" and level == "independent" and result != "unscorable"
            for link in confirmed:
                link_result = result if (link["role"] == "primary" or result not in WRONG) else "unscorable"
                document = {
                    "knowledge_id": link["knowledge_id"], "link_id": link["id"], "link_role": link["role"],
                    "attempt_id": attempt_id, "case_id": case["id"], "homework_id": case["homework_id"],
                    "question_id": case["question_id"], "question_revision": attempt["captured_question_revision"],
                    "decision_id": decision["id"], "verdict": decision["verdict"], "event_type": event_type,
                    "result": link_result, "assistance_level": level,
                    "independent": independent and link_result != "unscorable",
                    "item_family_id": family, "occurred_at": attempt["occurred_at"],
                    "source_role": "model" if decision["origin"] == "model" else "sitting_teacher",
                    "rule_version": RULE_VERSION,
                }
                document["source_key"] = _key({k: document[k] for k in (
                    "knowledge_id", "link_id", "link_role", "attempt_id", "decision_id", "result",
                    "assistance_level", "independent", "event_type", "occurred_at", "item_family_id")})
                output.append(document)
    return output


def _local_date(value: datetime):
    return value.astimezone(SHANGHAI).date()


def compute_state(knowledge_id: str, evidence: list[dict], diagnoses: list[dict], now: datetime) -> dict:
    """Rule R1.1: explainable status, never a percentage score."""
    rows = sorted(evidence, key=lambda e: (e["occurred_at"], e["id"]))
    scorable = [e for e in rows if e["result"] != "unscorable"]
    model_count = sum(e.get("source_role") == "model" for e in rows)
    staff_count = len(rows) - model_count
    source = "mixed" if model_count and staff_count else "model" if model_count else "staff" if staff_count else "none"
    errors = [e for e in scorable if e["result"] in WRONG]
    last_error = max((parse(e["occurred_at"]) for e in errors), default=None)
    independent = [e for e in scorable if e["independent"]]
    wins = [e for e in independent if e["result"] == "correct" and (last_error is None or parse(e["occurred_at"]) > last_error)]
    win_times = [parse(e["occurred_at"]) for e in wins]
    dates = {_local_date(t) for t in win_times}
    families = {e["item_family_id"] for e in wins}
    stable = len(dates) >= 2 and len(families) >= 2 and max(win_times) - min(win_times) >= STABLE_MIN_SPAN if wins else False
    assisted_fix = any(e["result"] == "correct" and not e["independent"] and last_error and parse(e["occurred_at"]) > last_error
                       for e in scorable)
    reasons = []
    if stable:
        status = "stable_for_now"
    elif wins:
        status, reasons = "consolidating", ["independent_success_awaiting_more_dates"]
    elif errors and assisted_fix:
        status, reasons = "consolidating", ["corrected_with_help_awaiting_new_school_item"]
    elif errors:
        status = "needs_help_now"
        reasons = ["repeated_errors" if len({e["attempt_id"] for e in errors}) >= 2 else "single_error_needs_diagnosis"]
    elif scorable:
        status, reasons = "no_judgment", ["assisted_or_unverified_only"]
    else:
        status, reasons = "no_judgment", ["no_scorable_evidence"]
    first_win = min(win_times, default=None)
    delayed = next((t for t in win_times if stable and t >= first_win + DELAYED_AFTER), None)
    independent_dates = {_local_date(parse(e["occurred_at"])) for e in independent}
    unverified = sum(1 for e in scorable if not e["independent"] and e["event_type"] == "school_first_attempt"
                     and e["assistance_level"] in ("no_recorded_help", "unknown"))
    if stable:
        sufficiency = "substantial"
    elif len(independent) >= 3 and len(independent_dates) >= 2:
        sufficiency = "substantial"
    elif scorable:
        sufficiency = "limited"
    else:
        sufficiency = "insufficient"
    explanation = (f"来自 {len(independent_dates)} 个日期、{len({e['item_family_id'] for e in independent})} 道题的独立作答"
                   if independent else ("只有作业记录，独立完成情况未核实" if scorable else "暂无可评分的已核实作答"))
    if model_count:
        explanation += f"；含 {model_count} 条 AI 初判，{staff_count} 条老师核实记录"
    open_questions = []
    if unverified:
        open_questions.append({"code": "independence_unverified", "count": unverified})
    if errors and not any(d["kind"] in ("suspected_cause", "verified_weak_scope") for d in diagnoses):
        open_questions.append({"code": "cause_not_diagnosed", "count": len(errors)})
    last_success = max((parse(e["occurred_at"]) for e in independent if e["result"] == "correct"), default=None)
    return {
        "knowledge_id": knowledge_id, "learning_status": status, "reason_codes": reasons,
        "assessment_source": source, "model_evidence_count": model_count, "staff_verified_evidence_count": staff_count,
        "evidence_sufficiency": sufficiency, "sufficiency_explanation": explanation,
        "valid_evidence_count": len(rows), "scorable_evidence_count": len(scorable),
        "independent_evidence_count": len(independent), "independent_dates_count": len(independent_dates),
        "distinct_item_families_count": len({e["item_family_id"] for e in independent}),
        "observed_error_count": len(errors),
        "last_error_at": last_error.isoformat() if last_error else None,
        "last_independent_success_at": last_success.isoformat() if last_success else None,
        "delayed_verified_at": delayed.isoformat() if delayed else None,
        "review_due_at": (last_success + REVIEW_HINT_AFTER).isoformat() if last_success and status == "stable_for_now" else None,
        "diagnoses": [{"id": d["id"], "kind": d["kind"], "scope_text": d["scope_text"], "evidence_ids": d["evidence_ids"]} for d in diagnoses],
        "open_questions": open_questions, "evidence_ids": [e["id"] for e in rows], "rule_version": RULE_VERSION,
    }


def chapter_rollup(nodes: list[dict], states: dict[str, dict]) -> list[dict]:
    """Counts per chapter; a parent is never the average of its children."""
    chapters = [n for n in nodes if n["node_type"] == "chapter"]
    output = []
    for chapter in chapters:
        children = [n for n in nodes if n["parent_id"] == chapter["id"]]
        counts = defaultdict(int)
        for child in children:
            counts[states.get(child["id"], {}).get("learning_status", "no_judgment")] += 1
        if sum(v for k, v in counts.items() if k != "no_judgment"):
            output.append({"chapter_id": chapter["id"], "name": chapter["name"], "objectives": len(children),
                           "status_counts": dict(sorted(counts.items()))})
    return output
