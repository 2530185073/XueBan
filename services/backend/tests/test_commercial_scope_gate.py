"""Tests for the curriculum hard gate around correction checks.

These tests intentionally use the existing synthetic transport helper.  A
blocked curriculum context must be rejected before any provider request, while
the legacy ``curriculum_context=None`` path and an authorized but unverified
edition remain usable.
"""

import json

import pytest

from test_correction_pipeline import checked, context, pipeline
from xueban_backend.curriculum_catalog import scope_for_subject, scope_payload
from xueban_backend.providers import ProviderError


def _assert_blocked(result, *, expected_marker):
    data = result.data
    assert data["validity"] == "needs_review"
    assert data["verdict"] == "unable_to_judge"
    assert data["reason_code"] == "needs_staff"
    assert data["knowledge_points"] == []
    assert data["correct_answer"] is None
    assert data["correction_method"] is None
    assert data["basis"]
    assert expected_marker in data["basis"][0]["summary"]
    assert "answer_checks" not in data
    assert result.metadata.attempts == ()


@pytest.mark.parametrize("role", ["check", "recheck"])
def test_missing_curriculum_scope_blocks_without_provider_call(role):
    ctx = context()
    ctx.update(
        student_grade=None,
        curriculum_context={"scope": None, "status": "available"},
    )
    requests = []

    with pipeline([checked()], requests) as flow:
        result = getattr(flow, role)(**ctx)

    assert requests == []
    _assert_blocked(result, expected_marker="先在学生档案设置已支持的年级")
    assert "学科" in result.data["basis"][0]["summary"]


@pytest.mark.parametrize("role", ["check", "recheck"])
@pytest.mark.parametrize("status", ["unsupported_scope", "unresolved_subject"])
def test_rejected_curriculum_status_blocks_without_provider_call(role, status):
    ctx = context()
    ctx.update(
        student_grade=6,
        curriculum_context={"scope": {"grade": 6, "subject": "数学"}, "status": status},
    )
    requests = []

    with pipeline([checked()], requests) as flow:
        result = getattr(flow, role)(**ctx)

    assert requests == []
    _assert_blocked(result, expected_marker="请先在学生档案设置已支持的年级"
                    if status == "unresolved_subject"
                    else "范围内")


@pytest.mark.parametrize("role", ["check", "recheck"])
def test_none_curriculum_context_preserves_legacy_provider_call(role):
    ctx = context()
    requests = []

    with pipeline([checked()], requests) as flow:
        result = getattr(flow, role)(**ctx)

    assert len(requests) == 1
    assert result.data["verdict"] == "incorrect"
    assert result.data["correct_answer"] == "4"


@pytest.mark.parametrize("role", ["check", "recheck"])
def test_unverified_edition_can_judge_original_question_but_has_no_candidate_tags(role):
    scope = scope_payload(scope_for_subject(6, "数学"))
    curriculum = {
        "scope": scope,
        "status": "unverified_edition",
        "revision_policy": "publisher_latest",
    }
    ctx = context()
    ctx.update(
        student_grade=6,
        knowledge_point_candidates=[],
        curriculum_context=curriculum,
    )
    requests = []

    with pipeline([checked()], requests) as flow:
        result = getattr(flow, role)(**ctx)

    assert len(requests) == 1
    assert result.data["verdict"] == "incorrect"
    assert result.data["correct_answer"] == "4"
    assert result.data["knowledge_points"] == []
    task = requests[0]["messages"][1]["content"][0]["text"]
    assert json.loads(task)["task_input"]["student_learning_context"]["knowledge_point_candidates"] == []


@pytest.mark.parametrize("role", ["check", "recheck"])
def test_invalid_input_cannot_bypass_curriculum_gate(role):
    ctx = context()
    ctx["question"]["regions"] = []
    ctx["curriculum_context"] = {"scope": None, "status": "unsupported_scope"}
    requests = []

    with pipeline([], requests) as flow, pytest.raises(ProviderError, match="^invalid_input$") as failure:
        getattr(flow, role)(**ctx)

    assert requests == []
    assert failure.value.attempts == ()
