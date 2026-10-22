"""Production service metrics: pipeline latency, staff waiting time and capacity.

Offline evaluation measures model quality on a frozen labeled split; it cannot see
queueing, retries or how long a learner waits for a person. These figures come
from the durable task ledger and correction records of one campus and window.
They are operational observations, never a quality or mastery claim.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
import math
from typing import Annotated

from fastapi import Header, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .account_models import PrincipalCampusGrant
from .auth import TokenVerifier
from .correction_models import Entity, Task
from .errors import Problem
from .models import Homework
from .staff import needs_staff

MAX_WINDOW = timedelta(days=31)
# Records belong to a homework; their own timestamps decide window membership.
# A help request or retry rarely arrives more than this long after upload.
HOMEWORK_LOOKBACK = timedelta(days=30)
TERMINAL = {"succeeded", "failed", "superseded", "cancelled"}


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _seconds(start: str | None, end: str | None) -> float | None:
    a, b = _parse(start), _parse(end)
    return None if a is None or b is None or b < a else (b - a).total_seconds()


def percentile(values, fraction):
    """Nearest-rank, matching evals/correction so offline and live figures align."""
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(fraction * len(values)) - 1)]


def distribution(values):
    values = [v for v in values if v is not None]
    return {"count": len(values), "p50": percentile(values, 0.5), "p95": percentile(values, 0.95),
            "max": max(values) if values else None}


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "rate": numerator / denominator if denominator else None}


def backlog(entities):
    """Work waiting for a person right now, regardless of the reporting window."""
    attempts = {e.id: e.document for e in entities if e.kind == "attempt"}
    questions = {e.id: e.document for e in entities if e.kind == "question"}
    queue = Counter()
    for entity in entities:
        if entity.kind == "case":
            reason = needs_staff(entity.document, attempts.get(entity.document["current_attempt_id"]),
                                 questions.get(entity.document["question_id"]))
            if reason:
                queue[reason] += 1
    return {"open_help_requests": sum(1 for e in entities if e.kind == "help" and e.document["status"] in {"waiting", "claimed"}),
            "review_queue": dict(sorted(queue.items())), "review_queue_total": sum(queue.values())}


def summarize(tasks, entities, window_start, window_end):
    inside = lambda stamp: stamp is not None and window_start <= stamp < window_end  # noqa: E731
    by_kind = {}
    for task in tasks:
        doc = task.document
        if not inside(_parse(doc.get("created_at"))):
            continue
        row = by_kind.setdefault(task.kind, {"statuses": Counter(), "failures": Counter(), "end_to_end": [],
                                             "provider": [], "executions": 0})
        row["statuses"][task.status] += 1
        if task.status in TERMINAL:
            row["end_to_end"].append(_seconds(doc.get("created_at"), doc.get("updated_at")))
        if doc.get("failure"):
            row["failures"][doc["failure"]["code"]] += 1
        for execution in (task.audit or {}).get("executions", []):
            row["executions"] += 1
            latency = (execution.get("metadata") or {}).get("latency_ms")
            if latency is not None:
                row["provider"].append(latency / 1000)
    tasks_out = {kind: {
        "created": sum(row["statuses"].values()), "statuses": dict(sorted(row["statuses"].items())),
        "failure_codes": dict(sorted(row["failures"].items())), "provider_executions": row["executions"],
        "end_to_end_seconds": distribution(row["end_to_end"]),
        "provider_latency_seconds": distribution(row["provider"]),
    } for kind, row in sorted(by_kind.items())}

    decisions = [e.document for e in entities if e.kind == "decision" and e.document.get("origin") == "model"
                 and inside(_parse(e.document.get("created_at")))]
    verdicts = Counter(d["verdict"] for d in decisions)
    reviews = [e.document for e in entities if e.kind == "review" and inside(_parse(e.document.get("created_at")))]
    actions = Counter(r["action"] for r in reviews)
    helps = [e.document for e in entities if e.kind == "help" and inside(_parse(e.document.get("created_at")))]
    return {
        "tasks": tasks_out,
        "model_candidates": {
            "decisions": len(decisions), "verdicts": dict(sorted(verdicts.items())),
            "unable_to_judge_share": ratio(verdicts["unable_to_judge"], len(decisions)),
        },
        "staff_reviews": {
            "reviews": len(reviews), "actions": dict(sorted(actions.items())),
            # A replaced or retracted model candidate is the live signal of a model error.
            "overturn_share": ratio(actions["replace"] + actions["retract"],
                                    actions["confirm"] + actions["replace"] + actions["retract"]),
        },
        "help_requests": {
            "created": len(helps), "statuses": dict(sorted(Counter(h["status"] for h in helps).items())),
            "wait_to_claim_seconds": distribution([_seconds(h["created_at"], h.get("claimed_at")) for h in helps]),
            "wait_to_complete_seconds": distribution([_seconds(h["created_at"], h.get("completed_at")) for h in helps]),
            "unclaimed_in_window": sum(1 for h in helps if not h.get("claimed_at")),
        },
    }


def install_service_metrics(app, engine, cfg):
    verifier = TokenVerifier(cfg)

    @app.get("/v1/admin/service-metrics", operation_id="getServiceMetrics")
    def service_metrics(campus_id: Annotated[str, Query(min_length=1, max_length=64)],
                        window_start: Annotated[datetime, Query(alias="from")],
                        window_end: Annotated[datetime, Query(alias="to")],
                        authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise Problem(401, "authorization", "authentication_required", "需要有效的 Bearer 访问凭证。")
        start = window_start if window_start.tzinfo else window_start.replace(tzinfo=timezone.utc)
        end = window_end if window_end.tzinfo else window_end.replace(tzinfo=timezone.utc)
        if not start < end or end - start > MAX_WINDOW:
            raise Problem(422, "validation", "invalid_request", "统计窗口必须为左闭右开且不超过 31 天。")
        with Session(engine) as session:
            actor = verifier.actor(authorization[7:], session)
            granted = session.scalar(select(PrincipalCampusGrant.campus_id).where(
                PrincipalCampusGrant.principal_id == actor.id, PrincipalCampusGrant.tenant_id == actor.tenant_id,
                PrincipalCampusGrant.campus_id == campus_id, PrincipalCampusGrant.active.is_(True)))
            if "campus_admin" not in actor.roles or granted is None:
                raise Problem(403, "authorization", "access_denied", "仅获该校区授权的管理员可查看运行指标。")
            campus = (Homework.tenant_id == actor.tenant_id, Homework.campus_id == campus_id)
            homeworks = select(Homework.id).where(*campus, Homework.created_at >= start - HOMEWORK_LOOKBACK,
                                                  Homework.created_at < end)
            tasks = list(session.scalars(select(Task).where(Task.homework_id.in_(homeworks))))
            entities = list(session.scalars(select(Entity).where(
                Entity.homework_id.in_(homeworks), Entity.kind.in_(("decision", "review", "help")))))
            live = select(Homework.id).where(*campus, Homework.processing_blocked.is_(False))
            waiting = list(session.scalars(select(Entity).where(
                Entity.homework_id.in_(live), Entity.kind.in_(("help", "case", "attempt", "question")))))
            body = summarize(tasks, entities, start, end)
            body["current_backlog"] = backlog(waiting)
        return {"campus_id": campus_id, "window": {"from": start.isoformat(), "to": end.isoformat(),
                                                    "timezone_note": "UTC instants; [from, to)"},
                "percentile_method": "nearest_rank", **body}
