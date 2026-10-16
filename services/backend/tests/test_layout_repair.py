"""Staff layout repair over real HTTP routes, migrated DB and the durable worker."""
from xueban_backend.providers import ProviderError

from test_correction_flow import flow, teacher  # noqa: F401  (pytest fixture reuse)


def repair(flow, supersede, regions, key=None):
    homework = flow.homework()
    return flow.post(f"/v1/homeworks/{flow.homework_id}/layout-revisions", {
        "expected_revision": homework["revision"],
        "supersede_questions": supersede,
        "replacement_regions": regions,
        "reason": "合成样例：老师拆分错切题目",
    }, key)


def region(asset_id, x, y, width=50, height=30):
    return {"asset_id": asset_id, "x": x, "y": y, "width": width, "height": height, "coordinate_space": "original_pixels"}


def test_split_supersedes_old_case_and_creates_replacements(flow):
    case = flow.ready()
    question = flow.client.get(f"/v1/questions/{case['question_id']}").json()["record"]
    asset_id = question["regions"][0]["asset_id"]
    assert repair(flow, [], [{"regions": [region(asset_id, 0, 0)], "stem_text": None}]).status_code == 403

    teacher(flow)
    stale = flow.post(f"/v1/homeworks/{flow.homework_id}/layout-revisions", {"expected_revision": 1, "supersede_questions": [], "replacement_regions": [{"regions": [region(asset_id, 0, 0)], "stem_text": None}], "reason": "过期版本"})
    assert stale.status_code == 409
    outside = repair(flow, [], [{"regions": [region(asset_id, 100, 70)], "stem_text": None}])
    assert outside.status_code == 422

    response = repair(flow, [{"question_id": question["question_id"], "expected_revision": question["revision"]}], [
        {"regions": [region(asset_id, 0, 0)], "stem_text": "1 + 1 = ?"},
        {"regions": [region(asset_id, 60, 40)], "stem_text": None},
    ])
    assert response.status_code == 202, response.text
    job = response.json()["job"]
    assert job["kind"] == "repair_layout"

    # The old case stops guiding the student immediately, before the worker runs.
    old_case = flow.client.get(f"/v1/cases/{case['id']}").json()
    assert old_case["lifecycle"] == "superseded"
    attempt = flow.client.get(f"/v1/attempts/{case['current_attempt_id']}").json()["record"]
    blocked = flow.post(f"/v1/cases/{case['id']}/checks", {"expected_revision": old_case["revision"], "attempt_id": attempt["id"], "question_revision": old_case["current_question_revision"], "attempt_recognition_revision": attempt["recognition_revision"]})
    assert blocked.status_code == 409

    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded"
    assert flow.pipeline.calls.count("recognize") == 3

    homework = flow.homework()
    cases = [flow.client.get(f"/v1/cases/{identifier}").json() for identifier in homework["case_ids"]]
    active = [c for c in cases if c["lifecycle"] == "active"]
    assert len(active) == 2
    old_case = flow.client.get(f"/v1/cases/{case['id']}").json()
    assert sorted(old_case["replacement_case_ids"]) == sorted(c["id"] for c in active)
    old_question = flow.client.get(f"/v1/questions/{case['question_id']}").json()
    assert old_question["record"]["lifecycle"] == "superseded"
    assert len(old_question["record"]["replacement_question_ids"]) == 2
    for new_case in active:
        new_question = flow.client.get(f"/v1/questions/{new_case['question_id']}").json()["record"]
        assert new_question["source"] == "staff_layout"
        assert new_question["stem_text"] == "1 + 1 = ?"
        new_attempt = flow.client.get(f"/v1/attempts/{new_case['current_attempt_id']}").json()["record"]
        assert new_attempt["answer_text"] == "3"
        assert new_attempt["recognition_status"] == "ready"
        assert new_attempt["parent_attempt_id"] is None
        for answer in new_attempt["answer_regions"]:
            assert answer["x"] + answer["width"] <= 120 and answer["y"] + answer["height"] <= 80

    # Replacement cases are ordinary cases: checks run on them.
    job = flow.start_check(active[0])
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{job['id']}").json()["status"] == "succeeded"


def test_failed_repair_never_revives_superseded_case_and_can_be_retried(flow):
    case = flow.ready()
    teacher(flow)
    question = flow.client.get(f"/v1/questions/{case['question_id']}").json()["record"]
    asset_id = question["regions"][0]["asset_id"]
    flow.pipeline.error = ProviderError("invalid_output")
    response = repair(flow, [{"question_id": question["question_id"], "expected_revision": question["revision"]}], [{"regions": [region(asset_id, 0, 0)], "stem_text": None}])
    assert response.status_code == 202
    assert flow.worker.run_once()
    assert flow.client.get(f"/v1/jobs/{response.json()['job']['id']}").json()["status"] == "failed"
    assert flow.client.get(f"/v1/cases/{case['id']}").json()["lifecycle"] == "superseded"

    # Superseded questions cannot be superseded again; an empty list adds back.
    again = repair(flow, [{"question_id": question["question_id"], "expected_revision": question["revision"] + 1}], [{"regions": [region(asset_id, 0, 0)], "stem_text": None}])
    assert again.status_code == 409
    flow.pipeline.error = None
    retry = repair(flow, [], [{"regions": [region(asset_id, 0, 0)], "stem_text": None}])
    assert retry.status_code == 202
    assert flow.worker.run_once()
    active = [c for c in (flow.client.get(f"/v1/cases/{i}").json() for i in flow.homework()["case_ids"]) if c["lifecycle"] == "active"]
    assert len(active) == 1 and active[0]["id"] != case["id"]


def test_ambiguous_crop_recognition_requires_staff(flow):
    case = flow.ready()
    teacher(flow)
    asset_id = flow.client.get(f"/v1/questions/{case['question_id']}").json()["record"]["regions"][0]["asset_id"]
    flow.pipeline.extra_item_label = "2"
    response = repair(flow, [], [{"regions": [region(asset_id, 0, 0)], "stem_text": "补录题干"}])
    assert response.status_code == 202
    assert flow.worker.run_once()
    new_case = [c for c in (flow.client.get(f"/v1/cases/{i}").json() for i in flow.homework()["case_ids"]) if c["id"] != case["id"]][0]
    attempt = flow.client.get(f"/v1/attempts/{new_case['current_attempt_id']}").json()["record"]
    assert attempt["recognition_status"] == "needs_revision"
    assert attempt["answer_text"] is None
    question = flow.client.get(f"/v1/questions/{new_case['question_id']}").json()["record"]
    assert question["stem_text"] == "补录题干"
