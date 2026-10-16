import asyncio
import httpx
from .api import create_app


def test_http_identity_scope_and_graph():
    async def run():
        app = create_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/health")).json()["demo"]
            assert (await client.get("/v1/students")).status_code == 422
            assert (await client.get("/v1/students", headers={"X-Demo-Actor":"invalid"})).status_code == 401
            parent = {"X-Demo-Actor":"parent-lin"}
            assert len((await client.get("/v1/students", headers=parent)).json()["items"]) == 1
            teacher = {"X-Demo-Actor":"teacher-east"}
            assert (await client.get("/v1/students/other/knowledge-graph", headers=teacher)).status_code == 404
            graph = (await client.get("/v1/students/lin/knowledge-graph", headers=teacher)).json()
            assert graph["agent_id"] == "agent-lin"
            assert graph["evidence_revision"] > 0
    asyncio.run(run())


def test_http_refuses_forged_fields_and_invalid_dates():
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(False)), base_url="http://test") as client:
            h = {"X-Demo-Actor":"student-lin"}
            body = {"idempotency_key":"a", "expected_revision":0, "question_id":"dist-a", "answer":"0", "occurred_at":"bad"}
            result = await client.post("/v1/students/lin/attempts", headers=h, json=body)
            assert result.status_code == 422
            assert result.json()["error"]["code"] == "invalid_timestamp"
            body["reviewed"] = True
            assert (await client.post("/v1/students/lin/attempts", headers=h, json=body)).status_code == 422
    asyncio.run(run())


def test_http_plan_defaults_to_zero_extra_work_and_limits_budget():
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(False)), base_url="http://test") as client:
            h = {"X-Demo-Actor": "teacher-east"}
            body = {"idempotency_key": "default", "expected_revision": 0}
            response = await client.post("/v1/students/lin/plans", headers=h, json=body)
            assert response.status_code == 201
            assert response.json()["extra_minutes"] == 0
            assert response.json()["tasks"] == []
            response = await client.post("/v1/students/lin/plans", headers=h, json={**body, "minutes": 11})
            assert response.status_code == 422
    asyncio.run(run())
