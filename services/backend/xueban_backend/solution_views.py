"""Record an explicit solution reveal against the exact answer the student saw.

Use a shown Hint so the existing timeline keeps the time of help: revealing a
solution after an independent answer must not rewrite that answer's conditions.
"""
import hashlib
from typing import Annotated

from fastapi import Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import Actor
from .contracts import Contracts
from .correction_core import (get_entity, iso, reserve_idempotency, save_entity,
                              update_case)
from .correction_models import Entity
from .errors import Problem
from .models import Homework
from .student_locks import lock_student
from . import recognition_recheck


class SolutionViewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    source_decision_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    source_decision_revision: int = Field(ge=1)


def install_solution_views(app, cfg, database_session, current_actor):
    from .app import require_student

    contracts = Contracts(cfg.contracts_dir)
    ActorDep = Annotated[Actor, Depends(current_actor)]
    SessionDep = Annotated[Session, Depends(database_session)]
    KeyDep = Annotated[str, Header(alias="Idempotency-Key", min_length=8,
                                  max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]

    @app.post("/v1/cases/{case_id}/solution-view", operation_id="recordSolutionView")
    def reveal(case_id: str, payload: SolutionViewInput, actor: ActorDep,
               session: SessionDep, idempotency_key: KeyDep):
        if "student" not in actor.roles:
            raise Problem(403, "authorization", "forbidden", "请由学生本人打开解答。")
        case = get_entity(session, case_id, "case")
        homework = session.get(Homework, case.homework_id)
        if homework is None or homework.tenant_id != actor.tenant_id:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        require_student(session, actor, homework.student_id)
        student_id = homework.student_id
        lock_student(session, student_id, tenant_id=actor.tenant_id)
        homework = session.scalar(select(Homework).where(Homework.id == homework.id)
                                  .with_for_update().execution_options(populate_existing=True))
        if homework is None or homework.student_id != student_id or homework.tenant_id != actor.tenant_id:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        require_student(session, actor, student_id)
        case = session.scalar(select(Entity).where(Entity.id == case_id, Entity.kind == "case")
                              .with_for_update().execution_options(populate_existing=True))
        if homework.processing_blocked or case is None or case.document.get("lifecycle", "active") != "active":
            raise Problem(409, "conflict", "processing_blocked", "这份作业已停止处理，请返回作业列表。")
        recognition_recheck.assert_no_active(session, case)
        if case.document["progress"] in {"rechecking", "editing"}:
            raise Problem(409, "conflict", "revision_conflict", "这道题正在重新检查，请等结果更新后再查看解答。")
        current = case.document.get("current_decision")
        if (case.document["current_attempt_id"] != payload.attempt_id or not current
                or current["id"] != payload.source_decision_id
                or current["revision"] != payload.source_decision_revision
                or current["validity"] not in {"candidate", "confirmed"}):
            raise Problem(409, "conflict", "revision_conflict", "这道题的作答或判断已更新，请刷新后再查看解答。")
        attempt = get_entity(session, payload.attempt_id, "attempt")
        question = get_entity(session, case.document["question_id"], "question")
        source = get_entity(session, payload.source_decision_id, "decision")
        versions = source.document["input_versions"]
        if (source.homework_id != homework.id or source.revision != payload.source_decision_revision
                or source.document.get("validity") not in {"candidate", "confirmed"}
                or versions["attempt_id"] != attempt.id
                or versions["attempt_recognition_revision"] != attempt.document["recognition_revision"]
                or versions["question_id"] != question.id
                or versions["question_revision"] != question.revision):
            raise Problem(409, "conflict", "revision_conflict", "解答对应的题目版本已更新，请刷新后重试。")
        parts = [str(source.document.get(key) or "").strip()
                 for key in ("correct_answer", "correction_method")]
        for item in source.document.get("answer_checks", []):
            parts.extend(str(item.get(key) or "").strip()
                         for key in ("correct_answer", "explanation"))
        if not any(parts):
            raise Problem(409, "conflict", "solution_unavailable", "这道题暂时没有可查看的解答，请请教老师。")
        body = payload.model_dump(mode="json")
        record, replay = reserve_idempotency(session, actor, student_id,
                                             f"POST /v1/cases/{case_id}/solution-view",
                                             idempotency_key, body)
        if replay is not None:
            return replay
        binding = f"{case_id}:{attempt.id}:{source.id}:{source.revision}"
        hint_id = "hint_" + hashlib.sha256(binding.encode()).hexdigest()[:32]
        hint = session.get(Entity, hint_id)
        if hint is None:
            timestamp = iso()
            hint = save_entity(session, contracts, homework.id, "hint", {
                "id": hint_id, "revision": 1, "case_id": case_id,
                "attempt_id": attempt.id, "source_decision_id": source.id,
                "source_decision_revision": source.revision, "level": "key_step",
                "content": "\n\n".join(part for part in parts if part)[:4000],
                "exposes_answer": True, "source_is_provisional": source.document["validity"] != "confirmed",
                "validity": "active", "first_shown_at": timestamp, "created_at": timestamp,
            })
            hint.annotations = {"source": "solution_view"}
            update_case(session, contracts, case, hint_ids=[*case.document["hint_ids"], hint_id])
        if attempt.document["help_condition"] != "answer_shown":
            save_entity(session, contracts, homework.id, "attempt", {
                **attempt.document, "revision": attempt.revision + 1, "help_condition": "answer_shown",
            }, row=attempt)
        result = {"case_id": case_id, **body, "exposed": True}
        record.response_json = result
        session.commit()
        return result
