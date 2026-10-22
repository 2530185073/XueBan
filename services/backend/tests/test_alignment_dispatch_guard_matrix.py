"""Frozen pre-dispatch validator oracle across 320 synthetic guard cases.

The digest was generated offline from commit 3601092's evaluate_alignment,
before adding the dispatch precheck. No baseline checkout, git, API, private
archive or filesystem fixture is needed when running this regression.
"""
from copy import deepcopy
import hashlib
import json
import random

from test_question_units_integration import bound, question
from test_semantic_alignment import observation, pair, response
from xueban_backend.providers import semantic_alignment as alignment


def guard_cases():
    rng = random.Random(217)
    for unit in ("answer_slot", "question"):
        for iteration in range(160):
            left = [observation(str(i+1), f"计算{i+1}+1") for i in range(3)]
            right = [observation(str(i+11), f"求值{i+1}+1") for i in range(3)]
            if unit == "question":
                left = [bound(question(str(i+1), stem_text=f"已知边长a={i+1}，求面积____与周长____")) for i in range(3)]
                right = [bound(question(str(i+11), stem_text=f"已知边长a={i+1}，求面积____与周长____")) for i in range(3)]
            if iteration % 4 == 0:
                for row in right:
                    row["stem_text"] = (row["stem_text"].replace("=", "=-") if unit == "question"
                                        else row["stem_text"].replace("+", "-"))
            proposals = [pair(left[i], right[i]) for i in range(3)]
            variant = iteration % 10
            if variant == 1:
                proposals.append(deepcopy(proposals[0]))
            elif variant == 2:
                proposals[0]["primary_source_id"] = "unknown-source"
            elif variant == 3:
                proposals[1]["same_question"] = False
            elif variant == 4:
                proposals[2]["primary_target_count"] = 2
            elif variant == 5:
                proposals[0]["primary_slot"] = 9
            elif variant == 6:
                proposals[1]["primary_quote"] = "fabricated quote"
            elif variant == 7:
                proposals[2].update(match_type="reading_disagreement", conditions_same=False)
            elif variant == 8:
                proposals[1]["secondary_source_id"] = proposals[0]["primary_source_id"]
            elif variant == 9:
                proposals[0]["explanation"] = ""
            rng.shuffle(proposals)
            payload = response(*proposals)
            if iteration % 11 == 0:
                payload["unmatched_source_ids"] = [alignment.source_id("primary", left[0])]
            yield unit, iteration, payload, left, right


def results_digest(results):
    return hashlib.sha256(json.dumps(results, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def test_320_guard_cases_preserve_full_baseline_results_and_never_skip_an_accepted_pair():
    results = []
    accepted = skipped = 0
    for unit, iteration, payload, left, right in guard_cases():
        before = deepcopy((payload, left, right))
        result = alignment.evaluate_alignment(payload, left, right, target_unit=unit)
        results.append(result)
        decision = alignment.alignment_dispatch(left, right, target_unit=unit)
        if decision["decision"] == "not_dispatched":
            assert result["pairs"] == [], (unit, iteration, "false skip")
            skipped += 1
        if result["pairs"]:
            assert decision["decision"] == "dispatch", (unit, iteration)
        assert (payload, left, right) == before
        accepted += len(result["pairs"])
    assert len(results) == 320 and accepted == 468 and skipped == 80
    assert results_digest(results) == "ed1ecb719d7f5c56eeac6cc209ae9d7386d1d3a653e3cb2b0767dc05709986bf"
