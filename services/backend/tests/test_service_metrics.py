"""Live service metrics: task latency, model abstention, staff waiting and backlog."""
from datetime import datetime, timedelta, timezone

from test_correction_flow import flow  # noqa: F401  (pytest fixture reuse)
from test_staff import staff_setup
from xueban_backend.service_metrics import distribution, percentile


def window():
    now = datetime.now(timezone.utc)
    return {"from": (now - timedelta(days=1)).isoformat(), "to": (now + timedelta(hours=1)).isoformat()}


def test_admin_sees_pipeline_and_staff_metrics(flow, tmp_path):
    case = flow.ready()
    flow.start_check(case)
    assert flow.worker.run_once()
    help_response = flow.post(f"/v1/cases/{case['id']}/help-requests", {
        "expected_revision": flow.client.get(f"/v1/cases/{case['id']}").json()["revision"],
        "attempt_id": case["current_attempt_id"], "kind": "appeal", "note": "请老师看看"})
    assert help_response.status_code == 201, help_response.text
    _, client, admin = staff_setup(flow, tmp_path)

    response = client.get("/v1/admin/service-metrics", params={"campus_id": "campus_a", **window()}, headers=admin)
    assert response.status_code == 200, response.text
    body = response.json()
    check = body["tasks"]["check"]
    assert check["created"] == 1 and check["statuses"] == {"succeeded": 1}
    assert check["end_to_end_seconds"]["count"] == 1
    assert check["provider_executions"] == 1
    assert body["model_candidates"]["decisions"] == 1
    assert body["help_requests"]["created"] == 1 and body["help_requests"]["unclaimed_in_window"] == 1
    assert body["help_requests"]["wait_to_claim_seconds"]["count"] == 0
    assert body["current_backlog"]["open_help_requests"] == 1
    assert body["current_backlog"]["review_queue_total"] >= 1
    assert body["percentile_method"] == "nearest_rank"

    other = client.get("/v1/admin/service-metrics", params={"campus_id": "campus_b", **window()}, headers=admin)
    assert other.status_code == 403
    teacher = flow.client.get("/v1/admin/service-metrics", params={"campus_id": "campus_a", **window()})
    assert teacher.status_code in (401, 403)
    now = datetime.now(timezone.utc)
    too_long = {"from": (now - timedelta(days=40)).isoformat(), "to": now.isoformat()}
    assert client.get("/v1/admin/service-metrics", params={"campus_id": "campus_a", **too_long}, headers=admin).status_code == 422
    client.close()


def test_percentiles_are_nearest_rank():
    assert percentile([], 0.5) is None
    assert percentile([5, 1, 3, 2, 4], 0.5) == 3
    assert percentile(list(range(1, 21)), 0.95) == 19
    assert distribution([None, 2.0, 1.0]) == {"count": 2, "p50": 1.0, "p95": 2.0, "max": 2.0}
