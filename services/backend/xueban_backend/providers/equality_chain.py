"""Check explicit constant equality chains in an already-bound observation.

This is not a reader, identity validator, or authority to release a pending
result. Plain newlines never imply equality. Unsupported text is a no-op; a
correct final value cannot erase a proved error in a retained equality chain.
"""
import hashlib
import json
import re
from copy import deepcopy

from .position_arithmetic import _HEADING, _normal, _number, _reference

PROTOCOL = "constant-equality-chain.v1"
_SOURCE_ID = re.compile(r"(?:primary|secondary|visual):[0-9a-f]{64}\Z")


def _printed_expression(stem):
    if not isinstance(stem, str) or len(stem) > 1600:
        return None
    start, end = 0, len(stem.rstrip())
    if end and stem[end - 1] == "。":
        end -= 1
    heading = _HEADING.match(stem)
    if heading:
        start = heading.end()
    while start < end and stem[start].isspace():
        start += 1
    while end > start and stem[end - 1].isspace():
        end -= 1
    value = _number(stem[start:end])
    return (_reference("stem_text", stem, start, end), value) if value is not None else None


def _chain(text):
    if not isinstance(text, str) or len(text) > 1200:
        return None
    left, right = len(text) - len(text.lstrip()), len(text.rstrip())
    if left >= right:
        return None
    steps, offset = [], left
    for line_index, line in enumerate(text[left:right].splitlines(keepends=True)):
        # A second expression on a new line is not necessarily the continuation
        # of this calculation (long division, another question, or side work).
        if not line.strip() or line_index and not line.lstrip().startswith(("=", "＝")):
            return None
        separators = list(re.finditer(r"[=＝]", line))
        starts = [0, *[m.end() for m in separators]]
        ends = [*[m.start() for m in separators], len(line)]
        for segment_index, (begin, end) in enumerate(zip(starts, ends)):
            raw = line[begin:end]
            if segment_index == 0 and not raw.strip() and separators:
                continue  # An explicit leading '=' binds to the prior value.
            begin += len(raw) - len(raw.lstrip())
            end -= len(raw) - len(raw.rstrip())
            expression = line[begin:end]
            value = _number(expression)
            if value is None:
                return None
            separator = (separators[segment_index - 1] if segment_index else None)
            steps.append({"expression": _reference("answer_text", text, offset + begin, offset + end),
                          "equals": (_reference("answer_text", text, offset + separator.start(),
                                                offset + separator.end()) if separator else None),
                          "value": value})
            if len(steps) > 8:
                return None
        offset += len(line)
    if len(steps) < 2 or not any(step["equals"] for step in steps):
        return None
    return steps


def check_equality_chain(source, *, source_id):
    """Return only a proved error; never modify text, infer a reading, or mark ready."""
    if (not isinstance(source, dict) or source.get("answer_status") != "present"
            or source.get("uncertainties") or source.get("options")
            or source.get("target_unit", "answer_slot") != "answer_slot"
            or source.get("question_type", "calculation") != "calculation"
            or source.get("subject", "数学") != "数学"
            or re.search(r"#[1-9][0-9]*$", _normal(str(source.get("label", ""))))
            or not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id)):
        return None
    printed = _printed_expression(source.get("stem_text"))
    steps = _chain(source.get("answer_text"))
    if printed is None or steps is None:
        return None
    printed_ref, expected = printed
    # A bare initial expression must repeat this printed expression, not just
    # have the same value as another exercise. A leading '=' instead explicitly
    # continues the printed expression already bound by the caller's gates.
    compact = lambda text: re.sub(r"\s+", "", _normal(text))
    if (steps[0]["equals"] is None
            and compact(steps[0]["expression"]["text"]) != compact(printed_ref["text"])):
        return None
    wrong = [index for index, step in enumerate(steps) if step["value"] != expected]
    if not wrong:
        return None  # Never promote an old incorrect/unknown model judgment.
    first = wrong[0]
    expression = steps[first]["expression"]["text"]
    explanation = f"过程第{first + 1}项“{expression}”的值为{steps[first]['value']}，与原式的值{expected}不等。"
    final_note = (f"最终结果{expected}正确，但不能抵消中间等号不成立的问题。"
                  if steps[-1]["value"] == expected else "最终结果也与原式不一致。")
    summary = "按原转写中的显式等号逐项核验：" + explanation + final_note
    method = explanation + f"请从这一项重新计算，保持每个等号两端相等；原式结果为{expected}。"
    previous, audit_steps = expected, []
    for index, step in enumerate(steps, 1):
        audit_steps.append({"index": index, "expression": step["expression"], "equals": step["equals"],
                            "value": str(step["value"]), "expected_value": str(expected),
                            "matches_printed_value": step["value"] == expected,
                            "equals_previous_value": step["value"] == previous if step["equals"] else None})
        previous = step["value"]
    observed = {key: source.get(key) for key in ("stem_text", "answer_text", "answer_status")}
    return {"verdict": "incorrect", "judgment_reason_code": "other", "correct_answer": str(expected),
            "correction_method": method, "basis_summary": summary,
            "audit": {"protocol": PROTOCOL, "source_id": source_id,
                      "source_text_sha256": hashlib.sha256(json.dumps(observed, ensure_ascii=False,
                          sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                      "printed_expression": printed_ref, "steps": audit_steps,
                      "first_inconsistent_step": first + 1, "final_value_matches": steps[-1]["value"] == expected,
                      "observation_rewritten": False,
                      "authority": "bounded_arithmetic_after_existing_source_and_visual_gates"}}


def _check_agreed_chains(primary, secondary):
    from .reconciliation import observation_record, source_id
    if not isinstance(primary, dict) or not isinstance(secondary, dict):
        return None
    records = [observation_record(source) for source in (primary, secondary)]
    if (primary.get("label") != secondary.get("label")
            or {key: value for key, value in records[0].items() if key != "answer_text"}
               != {key: value for key, value in records[1].items() if key != "answer_text"}):
        return None
    calculations = [check_equality_chain(source, source_id=source_id(role, source))
                    for role, source in (("primary", primary), ("secondary", secondary))]
    if any(calculation is None for calculation in calculations):
        return None
    def signature(calculation):
        # Same final result is insufficient. Every original expression and its
        # explicit equality role must agree, including each wrong intermediate.
        return [{**{key: value for key, value in step.items() if key not in {"expression", "equals"}},
                 "expression": step["expression"]["text"],
                 "equals": step["equals"] is not None} for step in calculation["audit"]["steps"]]
    if signature(calculations[0]) != signature(calculations[1]):
        return None
    result = deepcopy(calculations[0])
    result["basis_summary"] = "两份完整独立识读的原计算链一致。" + result["basis_summary"]
    result["audit"] = {"protocol": "dual-source-constant-equality-chain.v1",
        "applied_verdict": "incorrect",
        "reason": "both_bound_equality_chains_prove_same_error",
        "additional_model_calls": 0, "observation_rewritten": False,
        "sources": [{"role": role, "source_id": source_id(role, source),
                     "label": source["label"], "observation": deepcopy(record),
                     "original_verdict": source["verdict"],
                     "calculation": {**deepcopy(calculation["audit"]),
                         "authority": "bounded_arithmetic_after_bound_dual_source_gates"}}
                    for role, source, record, calculation in zip(
                        ("primary", "secondary"), (primary, secondary), records, calculations, strict=True)]}
    return result


def check_matched_equality_chain(primary, secondary):
    """Negative-only agreement check; caller must pass the complete matched gates."""
    if (not isinstance(primary, dict) or not isinstance(secondary, dict)
            or primary.get("verdict") != "correct" or secondary.get("verdict") != "correct"):
        return None
    result = _check_agreed_chains(primary, secondary)
    if result is not None:
        result["audit"].update(protocol="matched-constant-equality-chain.v1", previous_verdict="correct")
        for source in result["audit"]["sources"]:
            source["calculation"]["authority"] = "bounded_arithmetic_after_matched_dual_source_gates"
    return result


def check_reviewed_equality_chain(primary, secondary, *, selected_source, selected_source_id):
    """Two identical chains behind a resolved review not requiring visual proof.

    Unlike the matched path, the original judges may disagree. The complete
    observations must still independently prove the same error, and the actual
    selected source must be one of them. This does not waive any review gate.
    """
    from .reconciliation import source_id
    original = {"primary": primary, "secondary": secondary}.get(selected_source)
    if (not isinstance(original, dict) or source_id(selected_source, original) != selected_source_id
            or any(not isinstance(source, dict) or source.get("verdict") not in
                   {"correct", "incorrect", "partially_correct"} for source in (primary, secondary))):
        return None
    result = _check_agreed_chains(primary, secondary)
    if result is not None:
        result["audit"].update(protocol="reviewed-dual-source-constant-equality-chain.v1",
                               selected_source=selected_source, source_id=selected_source_id)
        for source in result["audit"]["sources"]:
            source["calculation"]["authority"] = "bounded_arithmetic_after_reviewed_dual_source_gates"
    return result
