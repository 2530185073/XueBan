"""Option brackets are presentation only with a fully bound single choice."""
from copy import deepcopy
import pytest

from test_two_model_pipeline import correct
from test_whole_page_pipeline import FakeClient, picture
from xueban_backend.providers import whole_page, reconciliation
from xueban_backend.providers.two_model import TwoModelWholePagePipeline


def choice(answer="A", **changes):
    return correct("7", **{"stem_text": "下列数中最小的是____", "answer_text": answer,
                      "question_type": "single_choice", "answer_status": "present",
                      "options": [{"label": "A", "text": "-2"}, {"label": "B", "text": "1"}],
                      "uncertainties": [], "correct_answer": None, "correction_method": None, **changes})


@pytest.mark.parametrize("a,b", [("A", "(A)"), ("(A)", "A"), ("（A）", "A"),
                                  ("A", "\t(A)\n"), ("(B)", "（B）")])
def test_option_wrapper_compares_without_changing_sources(a, b):
    first, second = choice(a), choice(b)
    before = deepcopy((first, second))
    hashes = [reconciliation.source_id(role, row) for role, row in zip(("primary", "secondary"), before)]
    assert whole_page._answer_equal(first, second)
    assert (first, second) == before
    assert [reconciliation.source_id(role, row) for role, row in zip(("primary", "secondary"), (first, second))] == hashes


@pytest.mark.parametrize("answer", ["(B)", "(C)", "(AB)", "A、B", "A或B", "答案A", "(A)因为最小",
                                   "((A))", "[A]", "A.", "A。", "(a)", "(1)", "(A+B)", "-A",
                                   "( A )", "(A）", "（A)"])
def test_nonliteral_or_other_option_is_not_wrapper_equivalence(answer):
    assert not whole_page._answer_equal(choice(), choice(answer))


@pytest.mark.parametrize("changes", [
    {"question_type": "multiple_choice"}, {"question_type": "fill_blank"}, {"question_type": "calculation"},
    {"question_type": "unknown"}, {"answer_status": "unclear"}, {"answer_status": "blank"},
    {"uncertainties": ["字母模糊"]}, {"uncertainties": None}, {"options": []},
    {"options": [{"label": "A", "text": "-2"}]},
    {"options": [{"label": "A", "text": "-2"}, {"label": "A", "text": "1"}]},
    {"options": [{"label": "A", "text": "-3"}, {"label": "B", "text": "1"}]},
    {"options": [{"label": "A", "text": ""}, {"label": "B", "text": "1"}]},
    {"options": [{"label": "A", "text": "-2"}, {"label": "b", "text": "1"}]},
    {"stem_text": "下列数中最大的是____"}, {"stem_text": ""}, {"label": "8"},
])
def test_complete_same_question_context_required(changes):
    second = choice("(A)")
    second.update(changes)
    assert not whole_page._answer_equal(choice(), second)


def test_only_wrapper_targets_avoid_extra_review_and_keep_raw_answers():
    primary, secondary = choice("A"), choice("(A)")
    review = FakeClient("review", {})
    pipeline = TwoModelWholePagePipeline(FakeClient("first", {"items": [primary]}),
                                        FakeClient("second", {"items": [secondary]}),
                                        reconciliation_client=review)
    result = pipeline.recognize(images=(picture(),))
    item = result.data["items"][0]
    assert item["recognition_status"] == "ready"
    assert item["assessment"]["verification"]["status"] == "matched"
    assert item["assessment"]["verification"]["independent_judgments"] == 2
    assert item["answer_text"] == "A"
    assert review.calls == []
    calls = result.data["multimodel_audit"]["calls"]
    assert len(calls) == len(result.metadata.attempts) == 2
    assert calls[0]["items"][0]["answer_text"] == "A"
    assert calls[1]["items"][0]["answer_text"] == "(A)"


@pytest.mark.parametrize("changes", [{"verdict": "incorrect"}, {"uncertainties": ["字母模糊"]},
                                      {"stem_text": "下列数中最大的是____"}])
def test_wrapper_does_not_clear_other_conflicts(changes):
    secondary = choice("(A)", **changes)
    pipeline = TwoModelWholePagePipeline(FakeClient("first", {"items": [choice()]}),
                                        FakeClient("second", {"items": [secondary]}))
    item = pipeline.recognize(images=(picture(),)).data["items"][0]
    assert item["recognition_status"] == "needs_revision"
    assert item["assessment"]["verdict"] == "unable_to_judge"
