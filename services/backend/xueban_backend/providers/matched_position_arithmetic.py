"""A negative-only arithmetic check of two already matched observations.

This is not a matcher or a new reading. The caller must first pass the existing
whole-page gates. Each immutable source is checked independently; a missing or
different position/value/conclusion is a no-op, never a new review request.
"""
from copy import deepcopy

from .position_arithmetic import check_position_arithmetic
from .reconciliation import observation_record, source_id

PROTOCOL = "matched-numeric-position-arithmetic.v1"


def check_matched_positions(primary, secondary):
    """Return only an agreed downgrade of two original correct judgments."""
    if (not isinstance(primary, dict) or not isinstance(secondary, dict)
            or primary.get("verdict") != "correct" or secondary.get("verdict") != "correct"):
        return None
    records = [observation_record(source) for source in (primary, secondary)]
    # Do not use normalized matcher equality to reinterpret different printed
    # structures. Explicit answer formatting can differ only if the separately
    # parsed numbered positions below have exactly the same original values.
    if (primary.get("label") != secondary.get("label")
            or {k: v for k, v in records[0].items() if k != "answer_text"}
               != {k: v for k, v in records[1].items() if k != "answer_text"}):
        return None
    results = [check_position_arithmetic(source, source_id=source_id(role, source))
               for role, source in (("primary", primary), ("secondary", secondary))]
    if any(result is None for result in results):
        return None

    def positions(result):
        return [{"index": position["index"],
                 "printed_segment": position["printed_segment"]["text"],
                 "placeholder": position["placeholder"]["text"],
                 "student_answer": position["student_answer"]["text"],
                 "substituted_value": position["substituted_value"],
                 "expected_value": position["expected_value"],
                 "verdict": position["verdict"],
                 "correct_answer": check["correct_answer"]}
                for position, check in zip(result["audit"]["positions"], result["answer_checks"], strict=True)]

    if (results[0]["verdict"] != results[1]["verdict"]
            or positions(results[0]) != positions(results[1])):
        return None
    result = deepcopy(results[0])
    result["basis_summary"] = "两份独立识读的逐空原值一致。" + result["basis_summary"]
    if len(result["basis_summary"]) > 2000:
        return None
    result["audit"] = {
        "protocol": PROTOCOL, "previous_verdict": "correct", "applied_verdict": result["verdict"],
        "reason": "both_bound_position_calculations_prove_same_error",
        "observation_rewritten": False, "additional_model_calls": 0,
        "sources": [{"role": role, "source_id": source_id(role, source),
                     "label": source["label"], "observation": deepcopy(record),
                     "original_verdict": source["verdict"],
                     "calculation": {**deepcopy(calculation["audit"]),
                         "authority": "bounded_arithmetic_after_matched_dual_source_gates"}}
                    for role, source, record, calculation in zip(
                        ("primary", "secondary"), (primary, secondary), records, results, strict=True)],
    }
    return result
