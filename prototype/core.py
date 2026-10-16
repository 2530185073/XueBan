"""Executable domain rules for a per-student learning agent.

No model calls, no real children, no production identity provider. Evidence is
append-only in this process; projections are rebuilt from the latest reviews.
Durable storage, transactions and a real authentication gateway are integration work.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from functools import wraps
import json
from threading import RLock
from uuid import uuid4
from zoneinfo import ZoneInfo


class DomainError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


@dataclass(frozen=True)
class Actor:
    id: str
    tenant_id: str
    campus_ids: tuple[str, ...]
    role: str
    student_ids: tuple[str, ...] = ()


def stamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise DomainError(422, "invalid_timestamp") from None
    if parsed.tzinfo is None:
        raise DomainError(422, "timezone_required")
    return parsed.astimezone(timezone.utc)


def uid(prefix: str) -> str:
    return prefix + "_" + uuid4().hex[:16]


def snapshot_read(fn):
    @wraps(fn)
    def guarded(self, *args, **kwargs):
        with self.lock:
            return fn(self, *args, **kwargs)
    return guarded


class Kernel:
    POLICY = "demo-rules-v2-custody"
    GRAPH = "math-demo-v1"

    def __init__(self):
        self.lock = RLock()
        self.students = {
            "lin": {"id": "lin", "name": "小林（示例）", "tenant_id": "demo-school", "campus_id": "east", "revision": 0, "next_lesson": "equation", "agent_id": "agent-lin"},
            "yu": {"id": "yu", "name": "小雨（示例）", "tenant_id": "demo-school", "campus_id": "east", "revision": 0, "next_lesson": "equation", "agent_id": "agent-yu"},
            "west": {"id": "west", "name": "另一校区示例", "tenant_id": "demo-school", "campus_id": "west", "revision": 0, "next_lesson": "equation", "agent_id": "agent-west"},
            "other": {"id": "other", "name": "另一托管运营方示例", "tenant_id": "other-school", "campus_id": "east", "revision": 0, "next_lesson": "equation", "agent_id": "agent-other"},
        }
        self.nodes = {
            "sign": {"label": "带符号运算", "prerequisites": []},
            "distribution": {"label": "乘法分配律", "prerequisites": ["sign"]},
            "equation": {"label": "含括号的一元一次方程", "prerequisites": ["distribution"]},
            "denominator": {"label": "去分母解方程", "prerequisites": ["equation"]},
        }
        self.questions = {
            "sign-a": {"node_id": "sign", "template": "negative_plus", "prompt": "计算 -3+7"},
            "sign-b": {"node_id": "sign", "template": "negative_multiply", "prompt": "计算 -2×(-4)"},
            "dist-a": {"node_id": "distribution", "template": "expand_positive", "prompt": "展开 3(x-2)"},
            "dist-b": {"node_id": "distribution", "template": "expand_negative", "prompt": "展开 -2(a+3)"},
            "dist-c": {"node_id": "distribution", "template": "expand_positive", "prompt": "展开 4(y-3)"},
            "dist-d": {"node_id": "distribution", "template": "mixed_expansion", "prompt": "化简 2(x-1)-3(x+2)"},
            "equation-a": {"node_id": "equation", "template": "equation_brackets", "prompt": "解方程 3(x-2)=12"},
        }
        self.attempts, self.reviews, self.events = [], [], []
        self.plans, self.reports, self.interventions = {}, {}, {}
        self.idempotency = {}

    def authorize(self, actor: Actor, student_id: str, roles: tuple[str, ...] | None = None):
        student = self.students.get(student_id)
        if not student or student["tenant_id"] != actor.tenant_id or student["campus_id"] not in actor.campus_ids:
            raise DomainError(404, "student_not_found")
        if actor.role in ("student", "guardian") and student_id not in actor.student_ids:
            raise DomainError(404, "student_not_found")
        if roles and actor.role not in roles:
            raise DomainError(403, "role_not_allowed")
        return student

    def command(self, actor, student_id, key, payload, expected_revision, roles, action):
        with self.lock:
            student = self.authorize(actor, student_id, roles)
            if not key or len(key) > 128:
                raise DomainError(422, "idempotency_key_required")
            cache_key = (actor.tenant_id, student["campus_id"], student_id, actor.id, payload["command"], key)
            digest = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if cache_key in self.idempotency:
                old_hash, response = self.idempotency[cache_key]
                if old_hash != digest:
                    raise DomainError(409, "idempotency_payload_conflict")
                artifact_id = response.get("id")
                if artifact_id in self.reports:
                    return deepcopy(self.reports[artifact_id])
                if artifact_id in self.plans:
                    return deepcopy(self.plans[artifact_id])
                return deepcopy(response)
            if expected_revision != student["revision"]:
                raise DomainError(409, "student_revision_conflict")
            result = action(student)
            self.idempotency[cache_key] = (digest, deepcopy(result))
            return deepcopy(result)

    def append(self, student, actor, kind, data):
        student["revision"] += 1
        event = {"id": uid("ev"), "tenant_id": student["tenant_id"], "campus_id": student["campus_id"], "student_id": student["id"], "revision": student["revision"], "actor_id": actor.id, "event_type": kind, "payload": deepcopy(data), "recorded_at": datetime.now(timezone.utc).isoformat()}
        self.events.append(event)
        effective_at = data.get("occurred_at", event["recorded_at"])
        if kind.startswith("review."):
            effective_at = next(a["occurred_at"] for a in self.attempts if a["id"] == data["attempt_id"])
        for collection in (self.plans, self.reports):
            for artifact in collection.values():
                if artifact["student_id"] == student["id"]:
                    if collection is self.plans or stamp(effective_at) < stamp(artifact["period_end"]):
                        artifact["stale"] = True
        return event

    def submit_attempt(self, actor, student_id, key, expected_revision, question_id, answer, occurred_at, stage="homework", assistance="none"):
        payload = dict(command="submit_attempt", question_id=question_id, answer=answer, occurred_at=occurred_at, stage=stage, assistance=assistance)
        def action(student):
            if question_id not in self.questions:
                raise DomainError(422, "unknown_question")
            if stage not in ("homework", "correction", "practice", "assessment", "preview") or assistance not in ("none", "hint", "answer_seen"):
                raise DomainError(422, "invalid_attempt_condition")
            instant = stamp(occurred_at)
            if instant > datetime.now(timezone.utc) + timedelta(minutes=5):
                raise DomainError(422, "future_attempt")
            a = {"id": uid("attempt"), "student_id": student_id, "question_id": question_id, "answer": answer, "occurred_at": instant.isoformat(), "stage": stage, "assistance": assistance}
            self.attempts.append(a)
            event = self.append(student, actor, "attempt.submitted", a)
            return {**a, "revision": event["revision"], "status": "pending_review"}
        return self.command(actor, student_id, key, payload, expected_revision, ("student", "teacher"), action)

    def review(self, actor, attempt_id, key, expected_revision, correct, independence_confirmed, reason, supersedes=None, retracted=False):
        attempt = next((a for a in self.attempts if a["id"] == attempt_id), None)
        if not attempt:
            raise DomainError(404, "attempt_not_found")
        payload = dict(command="review", attempt_id=attempt_id, correct=correct, independence_confirmed=independence_confirmed, reason=reason, supersedes=supersedes, retracted=retracted)
        def action(student):
            previous = [r for r in self.reviews if r["attempt_id"] == attempt_id]
            last = previous[-1] if previous else None
            if (last and supersedes != last["id"]) or (not last and supersedes):
                raise DomainError(409, "review_revision_conflict")
            if not reason.strip():
                raise DomainError(422, "review_reason_required")
            r = {"id": uid("review"), "student_id": student["id"], "attempt_id": attempt_id, "correct": correct, "independence_confirmed": independence_confirmed, "reason": reason, "supersedes": supersedes, "retracted": retracted, "reviewer_id": actor.id}
            self.reviews.append(r)
            event = self.append(student, actor, "review.corrected" if last else "review.confirmed", r)
            r["evidence_id"] = event["id"]
            return {**r, "revision": event["revision"]}
        return self.command(actor, attempt["student_id"], key, payload, expected_revision, ("teacher",), action)

    def observation(self, actor, student_id, key, expected_revision, behavior, observed, note, occurred_at):
        payload = dict(command="observation", behavior=behavior, observed=observed, note=note, occurred_at=occurred_at)
        def action(student):
            if behavior not in ("self_check", "task_start", "help_request", "review_followthrough"):
                raise DomainError(422, "unknown_observable_behavior")
            if stamp(occurred_at) > datetime.now(timezone.utc) + timedelta(minutes=5):
                raise DomainError(422, "future_observation")
            event = self.append(student, actor, "behavior.observed", {k: v for k, v in payload.items() if k != "command"})
            return {"evidence_id": event["id"], "revision": event["revision"]}
        return self.command(actor, student_id, key, payload, expected_revision, ("teacher", "coach"), action)

    @snapshot_read
    def graph(self, actor, student_id, as_of=None):
        student = self.authorize(actor, student_id, ("student", "teacher", "coach", "admin"))
        now = stamp(as_of) if as_of else datetime.now(timezone.utc)
        latest = {r["attempt_id"]: r for r in self.reviews if r["student_id"] == student_id}
        grouped = {k: [] for k in self.nodes}
        seen = set()
        attempts = sorted((a for a in self.attempts if a["student_id"] == student_id), key=lambda a: a["occurred_at"])
        for a in attempts:
            if stamp(a["occurred_at"]) >= now:
                continue
            r = latest.get(a["id"])
            new_question = a["question_id"] not in seen
            seen.add(a["question_id"])
            if not r or r["retracted"]:
                continue
            q = self.questions[a["question_id"]]
            independent = r["independence_confirmed"] and a["assistance"] == "none" and a["stage"] in ("homework", "practice", "assessment") and new_question
            grouped[q["node_id"]].append({"at": stamp(a["occurred_at"]), "correct": r["correct"], "independent": independent, "template": q["template"], "evidence_id": r["evidence_id"], "source": {"attempt_id": a["id"], "question_id": a["question_id"], "question": q["prompt"], "occurred_at": a["occurred_at"], "stage": a["stage"], "assistance": a["assistance"], "independent": independent, "new_question": new_question, "correct": r["correct"], "review_id": r["id"], "review_note": r["reason"], "evidence_id": r["evidence_id"]}})
        nodes = []
        for node_id, node in self.nodes.items():
            rows = grouped[node_id]
            valid = [r for r in rows if r["independent"]]
            errors = [r for r in valid if not r["correct"]]
            last_error = max((r["at"] for r in errors), default=None)
            wins = [r for r in valid if r["correct"] and (last_error is None or r["at"] > last_error)]
            dates = {r["at"].astimezone(ZoneInfo("Asia/Shanghai")).date() for r in wins}
            templates = {r["template"] for r in wins}
            first_win = min((r["at"] for r in wins), default=None)
            last_win = max((r["at"] for r in wins), default=None)
            stable = len(dates) >= 2 and len(templates) >= 2 and last_win - first_win >= timedelta(hours=24)
            if stable:
                state = "stable_for_now"
            elif wins:
                state = "consolidating"
            elif len(errors) >= 2:
                state = "needs_support"
            elif errors:
                state = "needs_diagnosis"
            elif rows:
                state = "awaiting_independent_check"
            else:
                state = "insufficient_evidence"
            anchor = first_win
            delayed = stable and bool(anchor) and any(r["at"] >= anchor + timedelta(days=7) for r in wins)
            recent = max((r["at"] for r in valid), default=None)
            conflicting = bool(last_win and any(not r["correct"] and not r["independent"] and r["at"] > last_win for r in rows))
            nodes.append({"id": node_id, **deepcopy(node), "state": state, "independent_count": len(valid), "verified_attempt_count": len(rows), "observed_error_count": sum(not r["correct"] for r in rows), "independent_error_count": len(errors), "successful_dates_after_error": len(dates), "template_coverage_after_error": len(templates), "delayed_verified": delayed, "review_due": bool(recent and now - recent >= timedelta(days=14)), "conflicting_evidence": conflicting, "evidence_sufficiency": "moderate" if stable and not conflicting else "limited", "evidence_ids": [r["evidence_id"] for r in rows], "evidence_sources": [r["source"] for r in rows]})
        if actor.role == "coach":
            for n in nodes:
                n["evidence_sources"] = []  # Observation-only role receives a summary.
        return {"student_id": student_id, "agent_id": student["agent_id"], "graph_version": self.GRAPH, "policy_version": self.POLICY, "evidence_revision": student["revision"], "nodes": nodes, "as_of": now.isoformat()}

    @snapshot_read
    def context(self, actor, student_id):
        student = self.authorize(actor, student_id, ("student", "teacher", "coach", "admin"))
        graph = self.graph(actor, student_id)
        observations = [{"id": e["id"], **e["payload"]} for e in self.events if e["student_id"] == student_id and e["event_type"] == "behavior.observed"][-5:]
        return {"student_agent": {"id": student["agent_id"], "student_id": student_id, "tenant_id": student["tenant_id"], "campus_id": student["campus_id"]}, "knowledge_graph": graph, "verified_observations": observations, "next_lesson": student["next_lesson"], "allowed_tools": ["read_context", "get_knowledge_subgraph", "search_approved_content", "propose_plan", "request_human_review"], "mode": "deterministic_reference_no_llm"}

    @snapshot_read
    def preview(self, actor, student_id):
        student = self.authorize(actor, student_id, ("student", "teacher", "coach"))
        target = student["next_lesson"]
        graph = self.graph(actor, student_id)
        prerequisites, pending = set(), list(self.nodes[target]["prerequisites"])
        while pending:
            current = pending.pop()
            if current in prerequisites:
                continue
            prerequisites.add(current)
            pending.extend(self.nodes[current]["prerequisites"])
        checks = [n for n in graph["nodes"] if n["id"] in prerequisites and (n["state"] != "stable_for_now" or n["review_due"] or n["conflicting_evidence"])]
        return {"next_lesson": target, "progress_source": "teacher_confirmed_demo_fixture", "prerequisite_checks": checks, "allocated_minutes": 0, "status": "context_only_not_assigned", "notice": "仅供坐班答疑参考；未知节点不自动加题。学校布置的预习按原任务协助，额外预习须通过选做门禁。"}

    def plan(self, actor, student_id, key, expected_revision, minutes=0, school_tasks_complete=False, child_willing=False, has_capacity=False):
        payload = dict(command="plan", minutes=minutes, school_tasks_complete=school_tasks_complete, child_willing=child_willing, has_capacity=has_capacity)
        def action(student):
            if not 0 <= minutes <= 10:
                raise DomainError(422, "invalid_time_budget")
            graph = self.graph(actor, student_id)
            order = {"needs_support": 0, "needs_diagnosis": 1, "awaiting_independent_check": 2, "consolidating": 3, "insufficient_evidence": 4, "stable_for_now": 5}
            gate = school_tasks_complete and child_willing and has_capacity and minutes > 0
            remaining, tasks = minutes, []
            for n in sorted(graph["nodes"] if gate else [], key=lambda x: (0 if x["conflicting_evidence"] else order[x["state"]], not x["review_due"])):
                if len(tasks) >= 1 or remaining < 5:
                    break
                if not n["verified_attempt_count"]:
                    continue
                if n["state"] == "stable_for_now" and not n["review_due"] and not n["conflicting_evidence"]:
                    continue
                kind = "homework_followup" if n["state"] == "needs_support" else "optional_clarification"
                if n["review_due"] and n["state"] == "stable_for_now":
                    kind = "delayed_review"
                tasks.append({"node_id": n["id"], "task_type": kind, "task_origin": "optional", "skippable": True, "carry_over_required": False, "minutes": 5, "reason": "conflicting_evidence" if n["conflicting_evidence"] else n["state"], "evidence_ids": n["evidence_ids"]})
                remaining -= 5
            result = {"id": uid("plan"), "student_id": student_id, "source_revision": student["revision"], "policy_version": self.POLICY, "status": "optional_suggestion" if tasks else "schoolwork_and_rest_first", "stale": False, "tasks": tasks, "time_budget_minutes": minutes, "allocated_minutes": minutes - remaining, "extra_minutes": minutes - remaining, "school_tasks_complete": school_tasks_complete, "child_willing": child_willing, "has_capacity": has_capacity, "notice": "先完成学校原任务与必要休息；选做可随时跳过，无惩罚或补做欠账。演示门禁由坐班身份记录，生产须核验当前上下文。"}
            self.plans[result["id"]] = result
            return result
        return self.command(actor, student_id, key, payload, expected_revision, ("teacher",), action)

    def intervention(self, actor, student_id, key, expected_revision, domain, target, action_text):
        payload = dict(command="intervention", domain=domain, target=target, action_text=action_text)
        def action(student):
            if domain not in ("academic", "habit") or (domain == "academic" and actor.role != "teacher"):
                raise DomainError(403, "academic_intervention_requires_teacher")
            if domain == "academic" and target not in self.nodes:
                raise DomainError(422, "unknown_knowledge_node")
            result = {"id": uid("intervention"), "student_id": student_id, "domain": domain, "target": target, "action": action_text, "owner": actor.id, "status": "planned", "verified_by_evidence": []}
            self.interventions[result["id"]] = result
            event = self.append(student, actor, "intervention.planned", result)
            return {**result, "revision": event["revision"]}
        return self.command(actor, student_id, key, payload, expected_revision, ("teacher", "coach"), action)

    def report(self, actor, student_id, key, expected_revision, period_start, period_end):
        payload = dict(command="report", period_start=period_start, period_end=period_end)
        def action(student):
            start, end = stamp(period_start), stamp(period_end)
            if start >= end or end > datetime.now(timezone.utc) + timedelta(minutes=5):
                raise DomainError(422, "invalid_report_period")
            graph = self.graph(actor, student_id, as_of=period_end)
            period_events = [e for e in self.events if e["student_id"] == student_id and e["event_type"] == "attempt.submitted" and start <= stamp(e["payload"]["occurred_at"]) < end]
            findings = []
            for n in graph["nodes"]:
                if not n["verified_attempt_count"] or (n["state"] == "stable_for_now" and not n["conflicting_evidence"]):
                    continue
                if n["conflicting_evidence"]:
                    interpretation = "不同条件下表现有差异，先核实作答帮助与题型，暂不归因。"
                elif n["state"] == "needs_support":
                    interpretation = "已核实的独立作答中反复出错，仅说明这些题型值得关注。"
                elif n["state"] == "needs_diagnosis":
                    interpretation = "出现一次独立错误，尚不足以判定持续薄弱。"
                elif n["state"] == "consolidating":
                    interpretation = "已有独立正确证据，可在后续学校作业中继续观察。"
                else:
                    interpretation = "已有作答记录，独立完成条件或持续表现尚待核实。"
                findings.append({"knowledge_id": n["id"], "knowledge_name": n["label"], "observed_errors": n["observed_error_count"], "reviewed_attempts": n["verified_attempt_count"], "independent_errors": n["independent_error_count"], "independent_attempts": n["independent_count"], "interpretation": interpretation, "cause": "未核实，不根据结果自动推断粗心、态度或能力。", "scope": "仅限已记录题目及截止时间前的证据，不代表整章或全部学校作业。", "sources": deepcopy(n["evidence_sources"]), "next_step": "在后续学校作业遇到相关问题时，由坐班老师答疑并观察；家庭无需额外加题。"})
            result = {"id": uid("report"), "student_id": student_id, "period_start": period_start, "period_end": period_end, "source_revision": student["revision"], "status": "draft", "stale": False, "published_by": None, "snapshot": {"period_attempt_count": len(period_events), "knowledge_as_of_period_end": graph["nodes"], "focus_items": findings, "unknown_knowledge_ids": [n["id"] for n in graph["nodes"] if not n["verified_attempt_count"]], "assessment_required": False, "limitations": "示例规则；状态和来源包含期末之前历史证据，周期提交量不等于全部学校作业完成率。没有证据不表示薄弱，不为填图谱强制测验；观察变化不代表因果提分。"}}
            self.reports[result["id"]] = result
            return result
        return self.command(actor, student_id, key, payload, expected_revision, ("teacher",), action)

    def publish(self, actor, report_id, key, expected_revision):
        report = self.reports.get(report_id)
        if not report:
            raise DomainError(404, "report_not_found")
        payload = dict(command="publish", report_id=report_id)
        def action(student):
            if report["stale"]:
                raise DomainError(409, "stale_report_regenerate_required")
            if report["status"] != "draft":
                raise DomainError(409, "report_already_published")
            report["status"], report["published_by"] = "published", actor.id
            return report
        return self.command(actor, report["student_id"], key, payload, expected_revision, ("teacher",), action)

    @snapshot_read
    def read_report(self, actor, report_id):
        report = self.reports.get(report_id)
        if not report:
            raise DomainError(404, "report_not_found")
        self.authorize(actor, report["student_id"])
        if actor.role == "coach":
            raise DomainError(403, "academic_report_requires_authorized_role")
        if actor.role in ("guardian", "student") and report["status"] != "published":
            raise DomainError(404, "report_not_found")
        return deepcopy(report)
