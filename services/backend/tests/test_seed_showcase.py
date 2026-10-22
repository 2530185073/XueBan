"""The showcase seeder writes only raw facts into its own tenant, and its stories land on the intended statuses."""
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from scripts import seed_showcase as S
from xueban_backend.learning_rules import compute_state

NOW = datetime(2026, 10, 22, 5, 0, tzinfo=timezone.utc)
CHAPTERS = [f"pep-g7-up-ch{i}" for i in range(1, 7)]
SIZES = [7, 13, 9, 8, 19, 19]


def skills():
    out, k = [], 0
    for chapter, size in zip(CHAPTERS, SIZES):
        for _ in range(size):
            out.append({"id": f"draft-pep7-s{k}", "ch": chapter, "name": f"技能{k}"})
            k += 1
    return out


def evidence_for(story):
    rows = []
    for i, (kind, days, hours) in enumerate(S.STORY[story]):
        rows.append({"id": f"e{i}", "occurred_at": (NOW - timedelta(days=days) + timedelta(hours=hours)).isoformat(),
                     "result": "correct" if kind in ("ok", "fix") else "incorrect", "independent": kind == "ok",
                     "event_type": "correction" if kind == "fix" else "school_first_attempt", "item_family_id": f"fam{i}", "attempt_id": f"a{i}",
                     "source_role": "sitting_teacher" if kind == "ok" else "model",
                     "assistance_level": "independent" if kind == "ok" else ("direction" if kind == "fix" else "no_recorded_help")})
    return rows


EXPECTED = {"stable": "stable_for_now", "stable2": "stable_for_now", "improving": "stable_for_now", "consolidating": "consolidating",
            "fixed": "consolidating", "needs_help": "needs_help_now", "struggling": "needs_help_now", "none": "no_judgment"}


@pytest.mark.parametrize("story,status", sorted(EXPECTED.items()))
def test_every_story_lands_on_its_intended_status_under_the_real_rules(story, status):
    assert compute_state("k", evidence_for(story), [], NOW)["learning_status"] == status


def test_plan_covers_every_skill_with_a_known_story_and_a_varied_map():
    plan = S.plan_for(skills())
    assert len(plan) == 75 and set(plan.values()) <= set(S.STORY)
    statuses = Counter(EXPECTED[v] for v in plan.values())
    assert all(statuses[s] >= 5 for s in ("stable_for_now", "consolidating", "needs_help_now", "no_judgment"))


def test_events_are_ordered_in_the_past_and_corrections_follow_their_error():
    events = S.build_events(S.plan_for(skills()), skills(), NOW)
    assert events and all(e["when"] < NOW for e in events)
    assert [e["when"] for e in events] == sorted(e["when"] for e in events)
    by_skill = {}
    for e in events:
        by_skill.setdefault(e["skill"], []).append(e)
    for items in by_skill.values():
        errors = [e["when"] for e in items if e["kind"] == "bad"]
        fixes = [e["when"] for e in items if e["kind"] == "fix"]
        if fixes:
            assert min(fixes) > min(errors)


def test_the_tenant_is_isolated_and_the_student_is_openly_fictional():
    assert S.TENANT == "demo_showcase" and S.CAMPUS.startswith("demo_showcase")
    assert "虚构" in S.STUDENT_NAME and "演示" in S.STUDENT_NAME
    assert S.TENANT not in {"local_demo", "local_quality", "qa_roles_20261021"}


def test_every_chapter_has_questions_with_a_wrong_and_a_right_answer_that_differ():
    for chapter in CHAPTERS:
        for k in range(8):
            stem, wrong, right = S.question_for({"ch": chapter}, k)
            assert stem and wrong and right and wrong != right


def test_placeholder_images_are_valid_synthetic_pngs():
    from PIL import Image
    import io
    content, width, height = S.placeholder_png("计算：(-18) ÷ 9")
    with Image.open(io.BytesIO(content)) as image:
        assert image.format == "PNG" and image.size == (width, height)


def test_no_model_or_network_code_is_imported_by_the_seeder():
    source = open(S.__file__, encoding="utf-8").read()
    for forbidden in ("httpx", "requests", "urllib", "providers", "ARK_API_KEY", "ZYAI"):
        assert forbidden not in source


def test_evidence_and_states_are_computed_never_inserted():
    """Status must come from the real rules. The seeder may read those tables, but must never add rows to them."""
    source = open(S.__file__, encoding="utf-8").read()
    assert "project_student(" in source
    for table_model in ("LearningEvidence(", "StudentKnowledgeState(", "LearningProjection("):
        assert table_model not in source, f"seeder constructs {table_model} directly"
