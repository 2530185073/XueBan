"""Each learner's long-term agent: traceable memories, case context and staff summaries."""
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from test_correction_flow import flow, teacher  # noqa: F401  (pytest fixture reuse)
from test_learning import learning, link, review, reviewed
from xueban_backend.agent_memory import rule_proposals
from xueban_backend.asset_store import LocalAssetStore
from xueban_backend.governance import purge_student
from xueban_backend.learning_models import AgentMemoryItem, AgentRun
from xueban_backend.providers.ark import Metadata


def evidence(i, day, result, independent=False):
    return {"id": f"ev{i}", "attempt_id": f"a{i}", "occurred_at": f"2026-10-{day:02d}T02:00:00Z", "result": result,
            "independent": independent}


def test_rules_summarize_repeated_errors_and_progress_without_inference():
    rows = {e["id"]: e for e in (evidence(1, 1, "incorrect"), evidence(2, 3, "partial"), evidence(3, 5, "correct", True),
                                 evidence(4, 8, "correct", True))}
    states = {"kp-a": {"learning_status": "needs_help_now", "evidence_ids": ["ev1", "ev2"], "last_error_at": None},
              "kp-b": {"learning_status": "stable_for_now", "evidence_ids": ["ev1", "ev3", "ev4"],
                       "last_error_at": "2026-10-01T02:00:00+00:00"},
              "kp-c": {"learning_status": "needs_help_now", "evidence_ids": ["ev1"], "last_error_at": None}}
    output = {p["knowledge_id"]: p for p in rule_proposals(states, rows, {"kp-a": "去括号", "kp-b": "合并同类项"})}
    assert set(output) == {"kp-a", "kp-b"}
    assert output["kp-a"]["category"] == "recurring_difficulty"
    assert output["kp-a"]["evidence_ids"] == ["ev1", "ev2"]
    assert output["kp-a"]["content"] == "10月1日至10月3日，「去括号」有 2 次经老师核实的错误作答。"
    assert output["kp-b"]["category"] == "progress_milestone"
    assert output["kp-b"]["evidence_ids"] == ["ev3", "ev4"]
    assert "2 个不同日期" in output["kp-b"]["content"]
    again = rule_proposals(states, rows, {})
    assert {p["rule_key"] for p in again} == {p["rule_key"] for p in output.values()}


def linked_evidence(flow):
    case = reviewed(flow)
    teacher(flow)
    case = review(flow, case)
    link(flow, case)
    learning(flow).drain()
    detail = flow.client.get("/v1/students/student_a/knowledge/kp-rational-add-sub").json()
    return case, detail["evidence"][0]["id"]


def test_student_sees_confirmed_only_and_may_set_own_preferences(flow):
    flow.ready()
    made = flow.client.post("/v1/students/student_a/memories",
                            json={"category": "interface_preference", "content": "提示先给图示再给文字"})
    assert made.status_code == 201, made.text
    assert made.json()["status"] == "confirmed" and made.json()["source"] == "student"
    forbidden = flow.client.post("/v1/students/student_a/memories", json={"category": "effective_support", "content": "多鼓励"})
    assert forbidden.status_code == 403
    assert flow.client.get("/v1/students/student_b/memories").status_code == 404
    retracted = flow.client.post(f"/v1/memories/{made.json()['id']}/retract")
    assert retracted.status_code == 200 and retracted.json()["status"] == "retracted"
    assert flow.client.get("/v1/students/student_a/memories").json()["items"] == []


def test_staff_memory_requires_valid_evidence_and_is_invalidated_when_evidence_withdrawn(flow):
    case, evidence_id = linked_evidence(flow)
    missing = flow.client.post("/v1/students/student_a/memories",
                               json={"category": "recurring_difficulty", "content": "异号相加符号易错", "knowledge_id": "kp-rational-add-sub"})
    assert missing.status_code == 422
    foreign = flow.client.post("/v1/students/student_a/memories",
                               json={"category": "recurring_difficulty", "content": "异号相加符号易错", "evidence_ids": ["evidence_x"]})
    assert foreign.status_code == 422
    made = flow.client.post("/v1/students/student_a/memories",
                            json={"category": "recurring_difficulty", "content": "异号相加时符号易错", "knowledge_id": "kp-rational-add-sub",
                                  "evidence_ids": [evidence_id]})
    assert made.status_code == 201, made.text
    support = flow.client.post("/v1/students/student_a/memories",
                               json={"category": "support_agreement", "content": "先自己读题两遍再求助"}).json()
    context = flow.client.get(f"/v1/cases/{case['id']}/agent-context").json()
    assert {t["kind"] for t in context["tips"]} == {"past_difficulty", "agreed_support"}
    assert all("答案" not in t["text"] for t in context["tips"])
    assert context["knowledge"][0]["learning_status"] == "needs_help_now"
    assert flow.client.get(f"/v1/cases/{case['id']}/agent-context").json()["run_id"] == context["run_id"]
    review(flow, flow.client.get(f"/v1/cases/{case['id']}").json(), action="replace", verdict="correct")
    learning(flow).drain()
    items = {m["id"]: m for m in flow.client.get("/v1/students/student_a/memories?include_inactive=true").json()["items"]}
    assert items[made.json()["id"]]["status"] == "invalidated"
    assert items[made.json()["id"]]["status_reason"] == "evidence_withdrawn"
    assert items[support["id"]]["status"] == "confirmed"
    with Session(flow.database.engine) as session:
        assert session.get(AgentRun, context["run_id"]).status == "stale"
    fresh = flow.client.get(f"/v1/cases/{case['id']}/agent-context").json()
    assert [t["kind"] for t in fresh["tips"]] == ["agreed_support"]


class SummaryClient:
    def __init__(self, output):
        self.settings = SimpleNamespace(enabled=True)
        self.output, self.calls = output, []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        assert "1 + 1" not in kwargs["instructions"]
        return SimpleNamespace(data=self.output(kwargs), metadata=Metadata("m", "m", None, None, None, 1, ()))


def test_staff_summary_cites_given_evidence_and_only_proposes_memories(flow):
    case = reviewed(flow)
    assert flow.client.post("/v1/students/student_a/agent-runs", json={"purpose": "staff_summary"}).status_code == 403
    teacher(flow)
    empty = flow.client.post("/v1/students/student_a/agent-runs", json={"purpose": "staff_summary"})
    assert empty.status_code == 202
    learning(flow).drain()
    [run] = flow.client.get("/v1/students/student_a/agent-runs").json()["items"]
    assert run["output"]["status"] == "needs_evidence"
    case = review(flow, case)
    link(flow, case)
    learning(flow).drain()

    def output(kwargs):
        ids = kwargs["output_schema"]["properties"]["claims"]["items"]["properties"]["evidence_ids"]["items"]["enum"]
        return {"status": "ready", "summary": "有理数加减有一次经核实的错误。", "next_action": "订正时先写出符号法则。",
                "claims": [{"text": "1 道加法题答错", "evidence_ids": ids[:1], "certainty": "high"}],
                "memory_proposals": [{"category": "effective_support", "content": "先口述符号法则再计算", "evidence_ids": ids[:1]}]}
    client = SummaryClient(output)
    queued = flow.client.post("/v1/students/student_a/agent-runs", json={"purpose": "staff_summary"}).json()
    assert flow.client.post("/v1/students/student_a/agent-runs", json={"purpose": "staff_summary"}).json()["id"] == queued["id"]
    learning(flow, client).drain()
    assert len(client.calls) == 1
    payload = client.calls[0]["text"]
    assert "student_a" not in payload and "learner_1" in payload
    latest = flow.client.get("/v1/students/student_a/agent-runs").json()["items"][0]
    assert latest["status"] == "completed" and latest["output"]["claims"][0]["evidence_ids"] == latest["context"]["evidence_ids"][:1]
    proposed = [m for m in flow.client.get("/v1/students/student_a/memories").json()["items"] if m["source"] == "model"]
    assert [m["status"] for m in proposed] == ["proposed"]
    confirmed = flow.client.post(f"/v1/memories/{proposed[0]['id']}/confirm")
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed"
    assert flow.client.post(f"/v1/memories/{proposed[0]['id']}/confirm").status_code == 409


def test_purge_removes_agent_records(flow):
    case, evidence_id = linked_evidence(flow)
    flow.client.post("/v1/students/student_a/memories",
                     json={"category": "effective_support", "content": "图示讲解更容易理解", "evidence_ids": [evidence_id]})
    flow.client.get(f"/v1/cases/{case['id']}/agent-context")
    with Session(flow.database.engine) as session:
        purge_student(session, LocalAssetStore(flow.cfg.asset_storage_dir), "tenant_a", "student_a")
        session.commit()
        for model in (AgentMemoryItem, AgentRun):
            assert session.scalar(select(func.count()).select_from(model)) == 0
