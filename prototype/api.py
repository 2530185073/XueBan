"""Local reference HTTP adapter. X-Demo-Actor is NOT production authentication."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .core import Actor, DomainError, Kernel


ACTORS = {
    "teacher-east": Actor("teacher-east", "demo-school", ("east",), "teacher"),
    "coach-east": Actor("coach-east", "demo-school", ("east",), "coach"),
    "parent-lin": Actor("parent-lin", "demo-school", ("east",), "guardian", ("lin",)),
    "student-lin": Actor("student-lin", "demo-school", ("east",), "student", ("lin",)),
    "teacher-west": Actor("teacher-west", "demo-school", ("west",), "teacher"),
    "teacher-other": Actor("teacher-other", "other-school", ("east",), "teacher"),
}


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)


class AttemptIn(Command):
    question_id: str
    answer: str = Field(max_length=2000)
    occurred_at: str
    stage: Literal["homework", "correction", "practice", "assessment", "preview"] = "practice"
    assistance: Literal["none", "hint", "answer_seen"] = "none"


class ReviewIn(Command):
    correct: bool
    independence_confirmed: bool = False
    reason: str = Field(min_length=1, max_length=1000)
    supersedes: str | None = None
    retracted: bool = False


class ObservationIn(Command):
    behavior: Literal["self_check", "task_start", "help_request", "review_followthrough"]
    observed: bool
    note: str = Field(max_length=1000)
    occurred_at: str


class PlanIn(Command):
    minutes: int = Field(default=20, ge=5, le=45)


class InterventionIn(Command):
    domain: Literal["academic", "habit"]
    target: str
    action_text: str = Field(min_length=1, max_length=1000)


class ReportIn(Command):
    period_start: str
    period_end: str


class ProgressIn(Command):
    next_lesson: str


def actor(x_demo_actor: str = Header(..., description="演示身份选择器，不是生产认证；仅允许固定合成身份")):
    if x_demo_actor not in ACTORS:
        raise DomainError(401, "unknown_demo_actor")
    return ACTORS[x_demo_actor]


def arguments(body: Command):
    values = body.model_dump()
    values["key"] = values.pop("idempotency_key")
    return values


def seed(kernel: Kernel):
    teacher = ACTORS["teacher-east"]
    now = datetime.now(timezone.utc)
    examples = [
        ("lin", "dist-a", False, 12, "3x-2"),
        ("lin", "dist-b", True, 9, "-2a-6"),
        ("lin", "dist-d", True, 2, "-x-8"),
        ("lin", "sign-a", False, 1, "-10"),
        ("yu", "dist-a", False, 3, "3x-2"),
        ("yu", "dist-b", False, 1, "-2a+6"),
        ("yu", "sign-a", True, 10, "4"),
        ("yu", "sign-b", True, 2, "8"),
    ]
    for index, (student_id, question_id, correct, days, answer) in enumerate(examples):
        attempt = kernel.submit_attempt(teacher, student_id, f"seed-a-{index}", kernel.students[student_id]["revision"], question_id, answer, (now - timedelta(days=days)).isoformat(), "assessment", "none")
        kernel.review(teacher, attempt["id"], f"seed-r-{index}", kernel.students[student_id]["revision"], correct, True, "示例老师已核对；全部为合成样本")
    kernel.observation(ACTORS["coach-east"], "lin", "seed-observation", kernel.students["lin"]["revision"], "self_check", True, "提交前按清单检查一次符号；单次观察", now.isoformat())


def create_app(with_seed: bool = True):
    app = FastAPI(title="学伴个人学习智能体参考内核", version="0.1.0-demo", description="仅用于本机合成数据演示。无真实模型、OCR、持久化或生产认证。OpenAPI 是已实现参考子集，完整产品接口见研发规格。")
    app.state.kernel = Kernel()
    if with_seed:
        seed(app.state.kernel)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code}, "demo": True})

    @app.get("/health", tags=["system"])
    def health():
        return {"status": "ok", "demo": True, "storage": "in-memory", "model_calls": False}

    @app.get("/v1/students", tags=["students"])
    def students(who: Actor = Depends(actor)):
        result = []
        for student_id in app.state.kernel.students:
            try:
                result.append(dict(app.state.kernel.authorize(who, student_id)))
            except DomainError:
                continue
        return {"items": result}

    @app.get("/v1/students/{student_id}/knowledge-graph", tags=["graph"])
    def graph(student_id: str, who: Actor = Depends(actor)):
        return app.state.kernel.graph(who, student_id)

    @app.get("/v1/students/{student_id}/agent-context", tags=["agent"])
    def context(student_id: str, who: Actor = Depends(actor)):
        return app.state.kernel.context(who, student_id)

    @app.get("/v1/students/{student_id}/growth-events", tags=["evidence"])
    def growth(student_id: str, who: Actor = Depends(actor)):
        k = app.state.kernel
        k.authorize(who, student_id, ("teacher", "student", "admin"))
        return {"items": [e for e in k.events if e["student_id"] == student_id], "notice": "活动事件不自动代表提升；提升须看复测与图谱证据。"}

    @app.post("/v1/students/{student_id}/attempts", status_code=201, tags=["evidence"])
    def attempt(student_id: str, body: AttemptIn, who: Actor = Depends(actor)):
        return app.state.kernel.submit_attempt(who, student_id, **arguments(body))

    @app.post("/v1/attempts/{attempt_id}/reviews", status_code=201, tags=["review"])
    def review(attempt_id: str, body: ReviewIn, who: Actor = Depends(actor)):
        return app.state.kernel.review(who, attempt_id, **arguments(body))

    @app.post("/v1/students/{student_id}/observations", status_code=201, tags=["coach"])
    def observation(student_id: str, body: ObservationIn, who: Actor = Depends(actor)):
        return app.state.kernel.observation(who, student_id, **arguments(body))

    @app.post("/v1/students/{student_id}/plans", status_code=201, tags=["agent"])
    def plan(student_id: str, body: PlanIn, who: Actor = Depends(actor)):
        return app.state.kernel.plan(who, student_id, **arguments(body))

    @app.get("/v1/students/{student_id}/preview", tags=["learning"])
    def preview(student_id: str, who: Actor = Depends(actor)):
        return app.state.kernel.preview(who, student_id)

    @app.post("/v1/students/{student_id}/course-progress", tags=["learning"])
    def progress(student_id: str, body: ProgressIn, who: Actor = Depends(actor)):
        k = app.state.kernel
        def update(student):
            if body.next_lesson not in k.nodes:
                raise DomainError(422, "unknown_lesson")
            student["next_lesson"] = body.next_lesson
            e = k.append(student, who, "course_progress.confirmed", {"next_lesson": body.next_lesson})
            return {"revision": e["revision"], "next_lesson": body.next_lesson}
        return k.command(who, student_id, body.idempotency_key, {"command": "course_progress", "next_lesson": body.next_lesson}, body.expected_revision, ("teacher",), update)

    @app.post("/v1/students/{student_id}/interventions", status_code=201, tags=["intervention"])
    def intervention(student_id: str, body: InterventionIn, who: Actor = Depends(actor)):
        return app.state.kernel.intervention(who, student_id, **arguments(body))

    @app.post("/v1/students/{student_id}/reports", status_code=201, tags=["reports"])
    def report(student_id: str, body: ReportIn, who: Actor = Depends(actor)):
        return app.state.kernel.report(who, student_id, **arguments(body))

    @app.post("/v1/reports/{report_id}/publish", tags=["reports"])
    def publish(report_id: str, body: Command, who: Actor = Depends(actor)):
        return app.state.kernel.publish(who, report_id, **arguments(body))

    @app.get("/v1/reports/{report_id}", tags=["reports"])
    def get_report(report_id: str, who: Actor = Depends(actor)):
        return app.state.kernel.read_report(who, report_id)

    return app


app = create_app()
