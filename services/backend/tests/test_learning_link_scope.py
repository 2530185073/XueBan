"""Knowledge attribution follows observations; staff authority stays question-wide."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from xueban_backend.learning_link_scope import binding_matches, observation_binding, select_current_links


def observation(attempt_id="a1", recognition_revision=1, decision_id="d1"):
    attempt = {"id": attempt_id, "question_id": "q1", "recognition_revision": recognition_revision}
    decision = {"id": decision_id, "revision": 1, "input_versions": {
        "question_id": "q1", "question_revision": 1, "attempt_id": attempt_id,
        "attempt_recognition_revision": recognition_revision}}
    return attempt, decision


def row(identifier, *, source="model", knowledge_id="kp-add", role="primary", status="confirmed",
        bindings=None, when="2026-10-21T00:00:00Z", **changes):
    return {"id": identifier, "question_id": "q1", "question_revision": 1, "graph_version": "g1",
            "knowledge_id": knowledge_id, "source": source, "role": role, "status": status,
            "observation_bindings": bindings, "created_at": when, **changes}


def current(rows, attempt=None, decision=None, **changes):
    return select_current_links(rows, question_revision=changes.get("question_revision", 1),
                                graph_version=changes.get("graph_version", "g1"), attempt=attempt, decision=decision)


def test_binding_checks_identity_but_not_mutable_decision_revision():
    attempt, decision = observation()
    expected = observation_binding(attempt, decision)
    decision["revision"] = 17  # A later submission may stale this same historical decision.
    assert observation_binding(attempt, decision) == expected
    assert binding_matches([expected], attempt, decision)
    wrapped = SimpleNamespace(id="d1", document=decision)
    assert observation_binding(SimpleNamespace(id="a1", document=attempt), wrapped) == expected
    with pytest.raises(ValueError, match="invalid_observation_binding"):
        observation_binding(SimpleNamespace(id="another-attempt", document=attempt), wrapped)


def test_case_or_question_mismatch_does_not_bind_machine_attribution():
    attempt, decision = observation()
    bound = row("bound", bindings=[observation_binding(attempt, decision)])
    assert current([bound], {**attempt, "case_id": "c1"}, {**decision, "case_id": "c2"}) == {}
    with pytest.raises(ValueError, match="invalid_observation_binding"):
        observation_binding({**attempt, "question_id": "q2"}, decision)


@pytest.mark.parametrize("changed", [
    {"attempt_id": "a2"}, {"attempt_recognition_revision": 2}, {"decision_id": "d2"},
    {"basis": "unknown"}, {"basis": []}, {"attempt_recognition_revision": True},
    {"attempt_recognition_revision": 0}, {"decision_id": " d1"}, {"extra": "untrusted"},
])
def test_binding_fails_closed_for_mismatch_or_malformed_entry(changed):
    attempt, decision = observation()
    binding = {**observation_binding(attempt, decision), **changed}
    assert not binding_matches([binding], attempt, decision)


@pytest.mark.parametrize("bindings", [None, {}, [], [None], ["a1"], [{"attempt_id": "a1"}]])
def test_unknown_legacy_source_is_not_a_current_binding(bindings):
    attempt, decision = observation()
    assert not binding_matches(bindings, attempt, decision)


def test_latest_machine_status_is_selected_within_observation_scope():
    a1, d1 = observation()
    a2, d2 = observation("a2", 1, "d2")
    b1, b2 = observation_binding(a1, d1), observation_binding(a2, d2)
    one = row("one", bindings=[b1])
    two = row("two", bindings=[b2])
    retired_two = row("retired-two", bindings=[b2], status="candidate", when="2026-10-21T01:00:00Z")
    unbound = row("unknown-newer", when="2026-10-21T02:00:00Z")
    assert current([one, two, retired_two, unbound], a1, d1) == {"kp-add": one}
    assert current([one, two, retired_two, unbound], a2, d2) == {"kp-add": retired_two}
    assert current([one, retired_two, unbound]) == {}
    assert current([one], a1, {**d1, "input_versions": []}) == {}


def test_staff_primary_overrides_all_machine_points_regardless_of_row_time():
    attempt, decision = observation()
    staff = row("staff", source="staff", knowledge_id="kp-divide")
    machine = row("machine", bindings=[observation_binding(attempt, decision)], when="2026-10-21T02:00:00Z")
    assert current([machine, staff], attempt, decision) == {"kp-divide": staff}
    assert current([machine, staff]) == {"kp-divide": staff}


def test_staff_secondary_preserves_other_machine_primary_and_rejection_vetoes_same_node():
    attempt, decision = observation()
    staff = row("staff", source="staff", role="secondary", knowledge_id="kp-divide")
    rejected = row("rejected", source="staff", status="rejected")
    machine = row("machine", bindings=[observation_binding(attempt, decision)], when="2026-10-21T02:00:00Z")
    assert current([machine, staff], attempt, decision) == {"kp-add": machine, "kp-divide": staff}
    assert current([machine, staff, rejected], attempt, decision) == {"kp-add": rejected, "kp-divide": staff}


def test_graph_question_revision_and_question_identity_are_isolated():
    attempt, decision = observation()
    bound = row("bound", bindings=[observation_binding(attempt, decision)])
    assert current([bound], attempt, decision, graph_version="g2") == {}
    assert current([bound], attempt, decision, question_revision=2) == {}
    assert current([bound, {**bound, "id": "other-question", "question_id": "q2"}], attempt, decision) == {}
    assert current([bound], {**attempt, "question_id": "q2"}, decision) == {}
    assert current([{**bound, "question_id": []}], attempt, decision) == {}


def test_selection_retains_original_orm_row_and_does_not_mutate_legacy_binding():
    attempt, decision = observation()
    binding = observation_binding(attempt, decision, basis="legacy_evidence")
    values = row("legacy", bindings=[binding], when=datetime(2026, 10, 21, tzinfo=timezone.utc))
    before = deepcopy(values)
    orm = SimpleNamespace(**values)
    assert current([orm], attempt, decision)["kp-add"] is orm
    assert vars(orm) == before
    assert binding["basis"] == "legacy_evidence"
