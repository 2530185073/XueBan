"""Corrected-photo identity regressions from the self-authored evening sheet.

The HTTP/database/worker flow is real; only the reader/checker is injected.
No runtime configuration, private photo or model credentials are loaded.
"""
from copy import deepcopy
from dataclasses import replace

import pytest
from sqlalchemy.orm import Session

from test_correction_flow import flow  # noqa: F401
from test_user_original_worker import original_policy
from xueban_backend.correction_models import Entity


def response_slot(prompt="解："):
    return [{"index": 1, "prompt": prompt, "answer_text": "4", "answer_status": "present"}]


def reader(flow, stem, slots, answer="4", options=()):
    flow.pipeline.stem = stem
    original_policy(flow.pipeline)
    state = {"slots": slots, "answer": answer, "options": list(options)}
    recognize = flow.pipeline.recognize

    def recognize_with_slots(**context):
        result = recognize(**context)
        for item in result.data["items"]:
            item.update(answer_slots=deepcopy(state["slots"]), answer_text=state["answer"],
                        options=deepcopy(state["options"]))
        return replace(result, data=deepcopy(result.data))

    flow.pipeline.recognize = recognize_with_slots
    return state


def correct(flow, case, stem, state, *, slots=(), answer="-4", options=()):
    asset = flow.uploaded()
    response = flow.post(f"/v1/cases/{case['id']}/attempts", {
        "expected_revision": case["revision"], "parent_attempt_id": case["current_attempt_id"],
        "question_revision": case["current_question_revision"], "asset_ids": [asset["id"]],
        "occurred_at": "2026-10-26T10:00:00Z", "reported_help": "unknown",
    })
    assert response.status_code == 202, response.text
    flow.pipeline.stem = stem
    state.update(slots=list(slots), answer=answer, options=list(options))
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{response.json()['job']['id']}").json()["status"] == "succeeded"
    current = flow.client.get(f"/v1/cases/{case['id']}").json()
    attempt = flow.client.get(f"/v1/attempts/{current['current_attempt_id']}").json()["record"]
    return current, attempt


@pytest.mark.parametrize("original,replacement,prompt,answer", [
    ("计算：(-12) ÷ 3。\n1. 解：", "计算：(-12)÷3。", "解：",
     "解：异号两数相除，商为负。\n(-12)÷3=-(12÷3)=-4"),
    ("解方程：2x + 3 = 11。\n1. 答：", "解方程：2x + 3 = 11。", "答：",
     "解：2x = 11 - 3\n2x = 8\nx = 4\n检验：2×4+3=11"),
])
def test_old_response_label_and_math_spacing_allow_same_question_recheck(flow, original, replacement, prompt, answer):
    state = reader(flow, original, response_slot(prompt))
    case = flow.ready()
    current, attempt = correct(flow, case, replacement, state, answer=answer)
    assert attempt["recognition_status"] == "ready"
    assert attempt["answer_text"] == answer
    assert attempt["parent_attempt_id"] == case["current_attempt_id"]
    assert flow.client.get(f"/v1/questions/{case['question_id']}").json()["record"]["stem_text"] == original
    flow.pipeline.verdict = "correct"
    flow.start_check(current)
    assert flow.worker.run_once()
    current = flow.client.get(f"/v1/cases/{case['id']}").json()
    assert current["current_decision"]["verdict"] == "correct"
    assert current["current_decision"]["validity"] == "candidate"
    assert current["progress"] != "verified_resolved"
    # The next correction still binds against the original question/slots,
    # even though its immediate parent no longer has a response-label slot.
    _, repeated = correct(flow, current, replacement, state, answer=answer)
    assert repeated["recognition_status"] == "ready"
    assert repeated["parent_attempt_id"] == attempt["id"]


def test_new_photo_response_label_is_compared_using_its_own_slot(flow):
    state = reader(flow, "计算：(-12)÷3。", [])
    case = flow.ready()
    _, attempt = correct(flow, case, "计算：(-12) ÷ 3。\n1. 解：", state, slots=response_slot())
    assert attempt["recognition_status"] == "ready"


@pytest.mark.parametrize("original,replacement", [
    ("计算：(-12) ÷ 3。", "计算：12÷3。"),
    ("计算：(-12) ÷ 3。", "计算：(-12)÷4。"),
    ("计算：3(x-2)。", "计算：3x-2。"),
    ("计算：x²。", "计算：x2。"),
    ("计算：1 2。", "计算：12。"),
    ("计算：x 2。", "计算：x2。"),
    ("已知x>0，计算：x+2。", "计算：x+2。"),
])
def test_response_label_comparison_preserves_conditions_and_math_tokens(flow, original, replacement):
    state = reader(flow, original + "\n1. 解：", response_slot())
    case = flow.ready()
    current, attempt = correct(flow, case, replacement, state)
    assert attempt["recognition_status"] == "needs_revision"
    assert attempt["answer_text"] is None
    assert current["current_decision"] is None
    flow.pipeline.verdict = "correct"
    flow.start_check(current)
    assert flow.worker.run_once()
    decision = flow.client.get(f"/v1/cases/{case['id']}").json()["current_decision"]
    assert decision["verdict"] == "unable_to_judge"


@pytest.mark.parametrize("mode", ["missing-slots", "stale-question", "stale-recognition", "substantive-prompt", "multiple-slots"])
def test_only_single_version_bound_generic_label_can_be_removed(flow, mode):
    slots = response_slot("当x>0时求值：" if mode == "substantive-prompt" else "解：")
    if mode == "multiple-slots":
        slots.append({**slots[0], "index": 2, "prompt": "答："})
    suffix = "\n".join(f"{s['index']}. {s['prompt']}" for s in slots)
    state = reader(flow, "计算：(-12) ÷ 3。\n" + suffix, slots)
    case = flow.ready()
    if mode in ("missing-slots", "stale-question", "stale-recognition"):
        with Session(flow.database.engine) as session, session.begin():
            root = session.get(Entity, case["root_attempt_id"])
            annotations = deepcopy(root.annotations)
            if mode == "missing-slots":
                annotations["answer_slots"] = []
            else:
                annotations["slots_question_revision" if mode == "stale-question" else "slots_recognition_revision"] = 99
            root.annotations = annotations
    _, attempt = correct(flow, case, "计算：(-12)÷3。", state)
    assert attempt["recognition_status"] == "needs_revision"
    assert attempt["answer_text"] is None


def test_same_stem_with_changed_choice_conditions_is_not_bound(flow):
    options = [{"label": "A", "text": "x>0"}, {"label": "B", "text": "x<0"}]
    state = reader(flow, "选择正确条件。", [], options=options)
    case = flow.ready()
    _, attempt = correct(flow, case, "选择正确条件。", state,
                         options=[{"label": "A", "text": "x<0"}, {"label": "B", "text": "x>0"}])
    assert attempt["recognition_status"] == "needs_revision"
    assert attempt["answer_text"] is None


def test_same_normalized_stem_still_needs_matching_label_when_source_is_ambiguous(flow):
    state = reader(flow, "计算：(-12) ÷ 3。\n1. 解：", response_slot())
    flow.pipeline.extra_item_label = "2"
    case = flow.ready()
    flow.pipeline.extra_item_label = None
    flow.pipeline.label = "2"  # New photo belongs to the other identical question.
    _, attempt = correct(flow, case, "计算：(-12)÷3。", state)
    assert attempt["recognition_status"] == "needs_revision"
    assert attempt["answer_text"] is None
