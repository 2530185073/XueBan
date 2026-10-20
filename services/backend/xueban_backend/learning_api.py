"""Knowledge graph, evidence and per-student state endpoints.

Students read their own map; campus staff read and maintain inputs. Every write
is append-only and guarded by the identifier of the row it replaces, so a stale
screen gets 409 instead of silently overwriting a colleague's decision.
"""
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import Actor
from .correction_models import Entity, Task
from .contracts import Contracts
from .errors import Problem
from .learning_models import (AttemptCondition, KnowledgeDiagnosis, KnowledgeEdge, KnowledgeNode, LearningEvidence,
                              LearningProjection, QuestionKnowledgeLink, StudentKnowledgeState, StudentLearningProfile)
from .correction_core import (invalidate, iso, remember_idempotent, replay_idempotent, save_entity,
                              set_task, update_case)
from .learning_projection import OptionalKey, fingerprints, new_id, published_graph
from .learning_rules import RULE_VERSION, chapter_rollup, hints_seen_before, model_decision_usable
from .models import Homework, Student
from .curriculum_catalog import (canonical_subject, curriculum_stage, graph_is_latest_reviewed,
                                 node_matches_scope, scope_for_subject, scope_payload, scopes_for_grade,
                                 supported_scopes, supported_scope)

KID = r"^[a-z][a-z0-9_-]{2,63}$"
STAFF = {"sitting_teacher", "campus_admin"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileInput(Strict):
    expected_revision: int = Field(ge=0)
    grade: int | None = Field(default=None, ge=1, le=12)
    term: Literal["upper", "lower"] | None = None
    textbook_revision: str | None = Field(default=None, max_length=40)
    current_knowledge_ids: list[Annotated[str, Field(pattern=KID)]] = Field(default_factory=list, max_length=20)
    note: str | None = Field(default=None, max_length=500)


class LinkInput(Strict):
    knowledge_id: str = Field(pattern=KID)
    role: Literal["primary", "secondary"]
    status: Literal["confirmed", "rejected"]
    expected_link_id: str | None = Field(default=None, max_length=64)
    rationale: str | None = Field(default=None, max_length=500)


class QuestionSubjectInput(Strict):
    expected_question_revision: int = Field(ge=1)
    subject: str = Field(min_length=1, max_length=40)


class ConditionInput(Strict):
    assistance_level: Literal["independent", "direction", "key_step", "solution_seen", "staff_help", "unknown"]
    expected_condition_id: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=500)


class DiagnosisInput(Strict):
    kind: Literal["suspected_cause", "verified_weak_scope"]
    scope_text: str = Field(min_length=2, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)


def _staff(actor: Actor):
    if not set(actor.roles) & STAFF:
        raise Problem(403, "authorization", "forbidden", "此操作需要已获该学生授权的坐班老师。")


def _node_dict(node: KnowledgeNode) -> dict:
    return {"id": node.id, "node_type": node.node_type, "parent_id": node.parent_id, "name": node.name,
            "definition": node.definition, "observable_objective": node.observable_objective,
            "curriculum_refs": node.curriculum_refs, "coverage_facets": node.coverage_facets,
            "misconception_codes": node.misconception_codes, "sort_order": node.sort_order}


def _link_dict(link: QuestionKnowledgeLink, names: dict) -> dict:
    return {"id": link.id, "question_id": link.question_id, "question_revision": link.question_revision,
            "knowledge_id": link.knowledge_id, "knowledge_name": names.get(link.knowledge_id), "role": link.role,
            "source": link.source, "status": link.status, "rationale": link.rationale, "actor_id": link.actor_id,
            "supersedes_id": link.supersedes_id, "created_at": link.created_at.isoformat()}


def diagnosis_dict(row: KnowledgeDiagnosis) -> dict:
    return {"id": row.id, "knowledge_id": row.knowledge_id, "kind": row.kind, "scope_text": row.scope_text,
            "evidence_ids": row.evidence_ids, "status": row.status, "actor_id": row.actor_id, "created_at": row.created_at.isoformat()}


def current_links(session, question_id) -> dict[str, QuestionKnowledgeLink]:
    current = {}
    for link in session.scalars(select(QuestionKnowledgeLink).where(QuestionKnowledgeLink.question_id == question_id)
                                .order_by(QuestionKnowledgeLink.created_at, QuestionKnowledgeLink.id)):
        current[link.knowledge_id] = link
    return current


def install_learning(app, engine, cfg, database_session, current_actor):
    from .app import require_student
    ActorDep = Annotated[Actor, Depends(current_actor)]
    SessionDep = Annotated[Session, Depends(database_session)]
    contracts = Contracts(cfg.contracts_dir)

    def graph_nodes(session):
        graph = published_graph(session)
        if graph is None:
            raise Problem(503, "service", "service_unavailable", "尚未发布课程知识图谱。")
        nodes = list(session.scalars(select(KnowledgeNode).where(KnowledgeNode.graph_version == graph.version)
                                     .order_by(KnowledgeNode.sort_order)))
        return graph, nodes

    def student_grade(session, student_id: str) -> int | None:
        profile = session.get(StudentLearningProfile, student_id)
        grade = profile.document.get("grade") if profile else None
        return grade if type(grade) is int and 5 <= grade <= 8 else None

    @app.get("/v1/curriculum/catalog", operation_id="getCurriculumCatalog")
    def curriculum_catalog(actor: ActorDep, session: SessionDep):
        return {"revision_policy": "publisher_latest", "grades": sorted({scope.grade for scope in supported_scopes()}),
                "subjects": {str(grade): [scope_payload(scope) for scope in scopes_for_grade(grade)]
                             for grade in sorted({scope.grade for scope in supported_scopes()})},
                "notice": "课程版本按国家中小学智慧教育平台最新目录；尚未完成知识点校对的学科只累计作答数据。"}

    @app.get("/v1/students/{student_id}/curriculum", operation_id="getStudentCurriculum")
    def student_curriculum(student_id: str, actor: ActorDep, session: SessionDep):
        require_student(session, actor, student_id)
        grade = student_grade(session, student_id)
        scopes = scopes_for_grade(grade) if grade is not None else ()
        return {"student_id": student_id, "grade": grade, "stage": curriculum_stage(grade),
                "subjects": [scope_payload(scope) for scope in scopes],
                "notice": "如果年级未登记，请先选择年级；学科出版社未提供的范围不会创建知识图谱。"}

    def question_scope(session, actor, question_id):
        question = session.get(Entity, question_id)
        if question is None or question.kind != "question":
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        homework = session.get(Homework, question.homework_id)
        student = require_student(session, actor, homework.student_id)
        return question, homework, student

    @app.get("/v1/curriculum/graph", operation_id="getCurriculumGraph")
    def curriculum_graph(actor: ActorDep, session: SessionDep):
        graph, nodes = graph_nodes(session)
        edges = session.scalars(select(KnowledgeEdge).where(KnowledgeEdge.graph_version == graph.version).order_by(KnowledgeEdge.id))
        return {"graph_version": graph.version, "title": graph.title, "review_status": graph.review_status,
                "source_note": graph.source_note, "nodes": [_node_dict(n) for n in nodes],
                "edges": [{"id": e.id, "from_id": e.from_id, "to_id": e.to_id, "relation_type": e.relation_type,
                           "scope": e.scope, "rationale": e.rationale} for e in edges]}

    @app.get("/v1/students/{student_id}/learning-profile", operation_id="getLearningProfile")
    def get_profile(student_id: str, actor: ActorDep, session: SessionDep):
        require_student(session, actor, student_id)
        row = session.get(StudentLearningProfile, student_id)
        if row is None:
            return {"student_id": student_id, "revision": 0, "grade": None, "term": None, "textbook_revision": None,
                    "current_knowledge_ids": [], "note": None, "updated_at": None}
        return {"student_id": student_id, "revision": row.revision, **row.document, "updated_at": row.updated_at.isoformat()}

    @app.put("/v1/students/{student_id}/learning-profile", operation_id="putLearningProfile")
    def put_profile(student_id: str, payload: ProfileInput, actor: ActorDep, session: SessionDep):
        if payload.textbook_revision not in (None, "publisher_latest"):
            raise Problem(422, "validation", "invalid_request", "教材版本只能使用当前出版社最新版。")
        student_self_service = "student" in actor.roles and actor.bound_student_id == student_id
        if student_self_service:
            if payload.grade not in {5, 6, 7, 8} or payload.term is not None or payload.current_knowledge_ids or payload.note is not None:
                raise Problem(422, "validation", "invalid_request", "学生本人只能设置已纳入范围的年级。")
        else:
            _staff(actor)
        student = require_student(session, actor, student_id)
        if not student_self_service:
            _, nodes = graph_nodes(session)
            known = {n.id for n in nodes}
            if not set(payload.current_knowledge_ids) <= known:
                raise Problem(422, "validation", "invalid_request", "当前课次必须引用已发布的知识点。")
        row = session.scalar(select(StudentLearningProfile).where(StudentLearningProfile.student_id == student_id).with_for_update())
        revision = row.revision if row else 0
        if payload.expected_revision != revision:
            raise Problem(409, "conflict", "revision_conflict", "学员档案已被修改，请刷新后核实。")
        document = {**(row.document if row else {}), "grade": payload.grade}
        if payload.grade is not None:
            document["textbook_revision"] = "publisher_latest"
        if not student_self_service:
            document.update(payload.model_dump(exclude={"expected_revision"}))
        stamp = datetime.now(timezone.utc)
        if row is None:
            row = StudentLearningProfile(student_id=student_id, tenant_id=student.tenant_id, revision=1, document=document,
                                         updated_by=actor.id, updated_at=stamp)
            session.add(row)
        else:
            row.revision, row.document, row.updated_by, row.updated_at = revision + 1, document, actor.id, stamp
        session.commit()
        if student_self_service:
            scopes = scopes_for_grade(payload.grade)
            return {"student_id": student_id, "grade": payload.grade, "stage": curriculum_stage(payload.grade),
                    "subjects": [scope_payload(scope) for scope in scopes],
                    "notice": "课程版本按国家中小学智慧教育平台最新目录。"}
        return {"student_id": student_id, "revision": row.revision, **row.document, "updated_at": row.updated_at.isoformat()}

    @app.get("/v1/students/{student_id}/knowledge", operation_id="getStudentKnowledge")
    def student_knowledge(student_id: str, actor: ActorDep, session: SessionDep):
        require_student(session, actor, student_id)
        grade = student_grade(session, student_id)
        scopes = {scope.subject: scope for scope in scopes_for_grade(grade)} if grade is not None else {}
        graph = published_graph(session)
        # Until a graph is explicitly reviewed against a current publisher
        # edition, never show pilot nodes as if they belonged to this student.
        usable_graph = graph if scopes and graph_is_latest_reviewed(graph) else None
        nodes = list(session.scalars(select(KnowledgeNode).where(KnowledgeNode.graph_version == usable_graph.version)
                                     .order_by(KnowledgeNode.sort_order))) if usable_graph else []
        if usable_graph:
            nodes = [node for node in nodes if any(node_matches_scope(node, scope) for scope in scopes.values())]
        graph = usable_graph
        by_id = {n.id: n for n in nodes}
        states = {row.knowledge_id: row.document for row in session.scalars(
            select(StudentKnowledgeState).where(StudentKnowledgeState.student_id == student_id))} if graph else {}
        projection = session.get(LearningProjection, student_id)
        expected = fingerprints(session, [student_id]).get(student_id)
        items = []
        for knowledge_id, state in states.items():
            node = by_id.get(knowledge_id)
            if node is None:
                continue
            parent = by_id.get(node.parent_id)
            items.append({**state, "name": node.name, "chapter_id": node.parent_id, "chapter_name": parent.name if parent else None,
                          "sort_order": node.sort_order})
        items.sort(key=lambda s: s["sort_order"])
        entities = session.scalars(select(Entity).where(Entity.homework_id.in_(select(Homework.id).where(
            Homework.student_id == student_id, Homework.processing_blocked.is_(False))), Entity.kind == "case")).all()
        counts = {subject: {"correct_count": 0, "incorrect_count": 0, "partial_count": 0,
                            "scorable_count": 0} for subject in scopes}
        for case in entities:
            decision = case.document.get("current_decision") or {}
            if (case.document.get("lifecycle") != "active"
                    or decision.get("validity") not in {"candidate", "confirmed"}
                    or (decision.get("origin") == "model" and not model_decision_usable(decision))):
                continue
            homework = session.get(Homework, case.homework_id)
            course = homework.curriculum if homework and isinstance(homework.curriculum, dict) else {}
            question = session.get(Entity, case.document.get("question_id"))
            if (question is None or course.get("grade") != grade
                    or course.get("edition") != "publisher_latest"):
                continue
            subject = canonical_subject((question.annotations or {}).get("subject_override")
                                        or question.document.get("subject"))
            scope = scopes.get(subject)
            if (scope is None or (course.get("subject") is not None and canonical_subject(course.get("subject")) != subject)
                    or (course.get("publisher_code") is not None and course.get("publisher_code") != scope.publisher_code)
                    or scope_payload(scope)["catalog_status"] != "available"):
                continue
            verdict = decision.get("verdict")
            if verdict == "correct":
                counts[subject]["correct_count"] += 1
                counts[subject]["scorable_count"] += 1
            elif verdict == "partially_correct":
                counts[subject]["partial_count"] += 1
                counts[subject]["incorrect_count"] += 1
                counts[subject]["scorable_count"] += 1
            elif verdict in {"incorrect", "unanswered"}:
                counts[subject]["incorrect_count"] += 1
                counts[subject]["scorable_count"] += 1
        subjects = []
        for subject, scope in scopes.items():
            item = counts[subject]
            scoped_nodes = [node for node in nodes if node_matches_scope(node, scope)]
            graph_ready = bool(graph_is_latest_reviewed(graph) and scoped_nodes)
            score_points = item["correct_count"] + item["partial_count"] * 0.5
            item.update(scope_payload(scope), graph_ready=graph_ready,
                        status="ready" if graph_ready else "building",
                        score_percent=round(100 * score_points / item["scorable_count"])
                        if item["scorable_count"] else None)
            subjects.append(item)
        graph_version = graph.version if graph else None
        graph_review_status = graph.review_status if graph else "not_ready_latest_edition"
        return {"student_id": student_id, "grade": grade, "graph_version": graph_version, "graph_review_status": graph_review_status,
                "rule_version": RULE_VERSION,
                "projection": {"evidence_revision": projection.evidence_revision if projection else 0,
                               "state_version": projection.state_version if projection else 0,
                               "projected_at": projection.projected_at.isoformat() if projection else None,
                               "pending": bool(expected) and (projection is None or projection.source_fingerprint != expected)},
                "states": items, "chapters": chapter_rollup([_node_dict(n) for n in nodes], states),
                "subjects": subjects,
                "notice": "仅统计当前年级、出版社及最新版教材范围内的有效 AI 判定；参考分来自已拍作业样本，不代表整本教材掌握率。目录未校对的科目只展示作答统计，不映射旧版知识点。"}

    @app.get("/v1/students/{student_id}/knowledge/{knowledge_id}", operation_id="getStudentKnowledgeNode")
    def knowledge_node(student_id: str, knowledge_id: str, actor: ActorDep, session: SessionDep,
                       include_invalidated: bool = False):
        require_student(session, actor, student_id)
        if student_grade(session, student_id) is not None and not graph_is_latest_reviewed(published_graph(session)):
            raise Problem(503, "service", "service_unavailable", "当前年级的最新版教材知识点目录仍在建设中。")
        graph, nodes = graph_nodes(session)
        by_id = {n.id: n for n in nodes}
        node = by_id.get(knowledge_id)
        if node is None:
            raise Problem(404, "not_found", "not_found", "知识点不存在。")
        if include_invalidated:
            _staff(actor)
        query = select(LearningEvidence).where(LearningEvidence.student_id == student_id, LearningEvidence.knowledge_id == knowledge_id)
        if not include_invalidated:
            query = query.where(LearningEvidence.validity == "valid")
        evidence = []
        for row in session.scalars(query.order_by(LearningEvidence.occurred_at, LearningEvidence.id)):
            question = session.get(Entity, row.document["question_id"])
            homework = session.get(Homework, row.document["homework_id"])
            evidence.append({"id": row.id, **{k: row.document[k] for k in (
                "attempt_id", "case_id", "homework_id", "question_id", "decision_id", "verdict", "event_type", "result",
                "assistance_level", "independent", "link_role", "occurred_at")},
                "validity": row.validity, "invalidation_reason": row.invalidation_reason,
                "source_role": row.document.get("source_role", "sitting_teacher"),
                "question_text": question.document["stem_text"] if question else None,
                "homework_title": homework.title if homework else None})
        state = session.get(StudentKnowledgeState, (student_id, knowledge_id))
        edges = list(session.scalars(select(KnowledgeEdge).where(KnowledgeEdge.graph_version == graph.version,
                                                                 KnowledgeEdge.relation_type == "prerequisite_of",
                                                                 (KnowledgeEdge.to_id == knowledge_id) | (KnowledgeEdge.from_id == knowledge_id))))
        states = {row.knowledge_id: row.document["learning_status"] for row in session.scalars(
            select(StudentKnowledgeState).where(StudentKnowledgeState.student_id == student_id))}
        related = lambda ids: [{"id": i, "name": by_id[i].name, "learning_status": states.get(i, "no_judgment")} for i in ids if i in by_id]  # noqa: E731
        return {"node": _node_dict(node), "state": state.document if state else None, "evidence": evidence,
                "prerequisites": related([e.from_id for e in edges if e.to_id == knowledge_id]),
                "leads_to": related([e.to_id for e in edges if e.from_id == knowledge_id]),
                "notice": "前置关系只用于提出诊断问题，不会因为本知识点出错就把前置知识点判为薄弱。"}

    @app.get("/v1/questions/{question_id}/knowledge-links", operation_id="listQuestionKnowledgeLinks")
    def list_links(question_id: str, actor: ActorDep, session: SessionDep):
        question_scope(session, actor, question_id)
        _, nodes = graph_nodes(session)
        names = {n.id: n.name for n in nodes}
        return {"question_id": question_id, "items": [_link_dict(l, names) for l in current_links(session, question_id).values()]}

    @app.put("/v1/questions/{question_id}/subject", operation_id="setUnrecognizedQuestionSubject")
    def set_question_subject(question_id: str, payload: QuestionSubjectInput, actor: ActorDep, session: SessionDep):
        question, homework, student = question_scope(session, actor, question_id)
        # Use the same homework-first lock as correction mutations, then
        # refresh the previously loaded question before checking its revision.
        homework = session.scalar(select(Homework).where(Homework.id == homework.id).with_for_update()
                                  .execution_options(populate_existing=True))
        if homework.processing_blocked:
            raise Problem(409, "conflict", "processing_blocked", "该作业已停止处理。")
        question = session.scalar(select(Entity).where(
            Entity.id == question_id, Entity.kind == "question").with_for_update()
                                  .execution_options(populate_existing=True))
        if question is None:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        if question.revision != payload.expected_question_revision:
            raise Problem(409, "conflict", "revision_conflict", "题目已更新，请刷新后再选择学科。")
        profile = session.get(StudentLearningProfile, student.id)
        grade = profile.document.get("grade") if profile else None
        curriculum = homework.curriculum if isinstance(homework.curriculum, dict) else {}
        if type(grade) is not int or curriculum.get("grade") != grade:
            raise Problem(409, "conflict", "course_unknown", "请先登记学生年级，再选择对应学科。")
        subject = canonical_subject(payload.subject)
        scope = scope_for_subject(grade, subject)
        if scope is None:
            raise Problem(422, "validation", "invalid_request", "所选学科不在当前年级范围内。")
        if canonical_subject((question.annotations or {}).get("subject_override")) == subject:
            return {"question_id": question.id, "subject": subject, "question_revision": question.revision}
        if canonical_subject(question.document.get("subject")) is not None:
            raise Problem(409, "conflict", "subject_already_recognized", "AI 已识别这道题的学科，不能通过手动选择覆盖。")
        annotations = dict(question.annotations or {})
        annotations["subject_override"] = subject
        annotations["subject_override_actor_id"] = actor.id
        annotations["subject_override_at"] = iso()
        # A subject correction does not change the image's diagram/slots.
        if annotations.get("diagram_revision") == question.revision:
            annotations["diagram_revision"] = question.revision + 1
        question.annotations = annotations
        save_entity(session, contracts, homework.id, "question",
                    {**question.document, "revision": question.revision + 1}, row=question)
        affected = set()
        for case in session.scalars(select(Entity).where(Entity.homework_id == homework.id, Entity.kind == "case")):
            if case.document["question_id"] != question.id or case.document.get("lifecycle", "active") != "active":
                continue
            invalidate(session, contracts, case, "question_subject_revision")
            update_case(session, contracts, case, current_question_revision=question.revision,
                        current_decision=None, progress="paused" if case.document["progress"] == "paused" else "pending")
            affected.add(case.id)
        # Running calls are fenced by question_revision in the worker's
        # commit-time check. Do not lock them in the opposite order to finish().
        for task in session.scalars(select(Task).where(Task.homework_id == homework.id,
                                                       Task.status.in_(["queued", "retryable_failed"]))):
            if task.inputs.get("case_id") in affected:
                set_task(session, contracts, task, "superseded")
        session.commit()
        return {"question_id": question.id, "subject": subject, "question_revision": question.revision}

    @app.post("/v1/questions/{question_id}/knowledge-links", status_code=201, operation_id="decideQuestionKnowledgeLink")
    def decide_link(question_id: str, payload: LinkInput, actor: ActorDep, session: SessionDep):
        _staff(actor)
        question, homework, student = question_scope(session, actor, question_id)
        graph, nodes = graph_nodes(session)
        names = {n.id: n.name for n in nodes if n.node_type in ("concept", "skill")}
        if payload.knowledge_id not in names:
            raise Problem(422, "validation", "invalid_request", "只能关联已发布的知识点（不含章节）。")
        session.scalar(select(Entity).where(Entity.id == question_id).with_for_update())
        links = current_links(session, question_id)
        current = links.get(payload.knowledge_id)
        if (current.id if current else None) != payload.expected_link_id:
            raise Problem(409, "conflict", "revision_conflict", "题目知识点已被他人修改，请刷新后核实。")
        if payload.role == "primary" and payload.status == "confirmed" and any(
                l.role == "primary" and l.status == "confirmed" and k != payload.knowledge_id for k, l in links.items()):
            raise Problem(409, "conflict", "revision_conflict", "这道题已有主知识点；请先把原主知识点改为次要或驳回。")
        link = QuestionKnowledgeLink(id=new_id("qkl"), tenant_id=student.tenant_id, student_id=student.id, homework_id=homework.id,
                                     question_id=question_id, question_revision=question.revision, knowledge_id=payload.knowledge_id,
                                     graph_version=graph.version, role=payload.role, source="staff", status=payload.status,
                                     rationale=payload.rationale, actor_id=actor.id, supersedes_id=current.id if current else None,
                                     created_at=datetime.now(timezone.utc))
        session.add(link)
        session.commit()
        return _link_dict(link, names)

    def condition_dict(row):
        return {"id": row.id, "attempt_id": row.attempt_id, "assistance_level": row.assistance_level, "note": row.note,
                "actor_id": row.actor_id, "created_at": row.created_at.isoformat()}

    def current_condition(session, attempt_id):
        return session.scalar(select(AttemptCondition).where(AttemptCondition.attempt_id == attempt_id)
                              .order_by(AttemptCondition.created_at.desc(), AttemptCondition.id.desc()))

    @app.get("/v1/attempts/{attempt_id}/condition", operation_id="getAttemptCondition")
    def get_condition(attempt_id: str, actor: ActorDep, session: SessionDep):
        _staff(actor)
        attempt = session.get(Entity, attempt_id)
        if attempt is None or attempt.kind != "attempt":
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        require_student(session, actor, session.get(Homework, attempt.homework_id).student_id)
        row = current_condition(session, attempt_id)
        return {"attempt_id": attempt_id, "current": condition_dict(row) if row else None}

    @app.post("/v1/attempts/{attempt_id}/condition", status_code=201, operation_id="recordAttemptCondition")
    def record_condition(attempt_id: str, payload: ConditionInput, actor: ActorDep, session: SessionDep):
        _staff(actor)
        attempt = session.get(Entity, attempt_id)
        if attempt is None or attempt.kind != "attempt":
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        homework = session.get(Homework, attempt.homework_id)
        student = require_student(session, actor, homework.student_id)
        session.scalar(select(Entity).where(Entity.id == attempt_id).with_for_update())
        current = current_condition(session, attempt_id)
        if (current.id if current else None) != payload.expected_condition_id:
            raise Problem(409, "conflict", "revision_conflict", "作答帮助条件已被他人修改，请刷新后核实。")
        hints = [e.document for e in session.scalars(select(Entity).where(Entity.homework_id == attempt.homework_id, Entity.kind == "hint"))
                 if e.document["case_id"] == attempt.document["case_id"]]
        if payload.assistance_level == "independent" and (hints_seen_before(attempt.document, hints)
                                                          or attempt.document["parent_attempt_id"] is not None):
            raise Problem(409, "conflict", "help_recorded", "这次作答前已看过提示，或属于订正，不能标为独立完成。")
        row = AttemptCondition(id=new_id("cond"), tenant_id=student.tenant_id, student_id=student.id, attempt_id=attempt_id,
                               assistance_level=payload.assistance_level, note=payload.note, actor_id=actor.id,
                               created_at=datetime.now(timezone.utc))
        session.add(row)
        session.commit()
        return condition_dict(row)

    @app.post("/v1/students/{student_id}/knowledge/{knowledge_id}/diagnoses", status_code=201, operation_id="addKnowledgeDiagnosis")
    def add_diagnosis(student_id: str, knowledge_id: str, payload: DiagnosisInput, actor: ActorDep, session: SessionDep,
                      idempotency_key: OptionalKey = None):
        _staff(actor)
        student = require_student(session, actor, student_id)
        operation = f"POST /v1/students/{student_id}/knowledge/{knowledge_id}/diagnoses"
        if (replay := replay_idempotent(session, actor, student_id, operation, idempotency_key, payload.model_dump(mode="json"))) is not None:
            return replay
        _, nodes = graph_nodes(session)
        if knowledge_id not in {n.id for n in nodes}:
            raise Problem(404, "not_found", "not_found", "知识点不存在。")
        valid = set(session.scalars(select(LearningEvidence.id).where(
            LearningEvidence.student_id == student_id, LearningEvidence.knowledge_id == knowledge_id,
            LearningEvidence.validity == "valid")))
        if not set(payload.evidence_ids) <= valid:
            raise Problem(422, "validation", "invalid_request", "诊断只能引用该知识点当前有效的证据。")
        if payload.kind == "verified_weak_scope" and len(payload.evidence_ids) < 2:
            raise Problem(422, "validation", "invalid_request", "已核实薄弱范围至少需要两条相关证据，并写明具体子范围。")
        row = KnowledgeDiagnosis(id=new_id("diag"), tenant_id=student.tenant_id, student_id=student_id, knowledge_id=knowledge_id,
                                 kind=payload.kind, scope_text=payload.scope_text.strip(), evidence_ids=payload.evidence_ids,
                                 status="active", actor_id=actor.id, created_at=datetime.now(timezone.utc))
        session.add(row)
        session.flush()
        result = remember_idempotent(session, actor, student_id, operation, idempotency_key, payload.model_dump(mode="json"),
                                     diagnosis_dict(row))
        session.commit()
        return result

    @app.get("/v1/students/{student_id}/knowledge/{knowledge_id}/diagnoses", operation_id="listKnowledgeDiagnoses")
    def list_diagnoses(student_id: str, knowledge_id: str, actor: ActorDep, session: SessionDep):
        _staff(actor)
        require_student(session, actor, student_id)
        rows = session.scalars(select(KnowledgeDiagnosis).where(KnowledgeDiagnosis.student_id == student_id,
                                                                KnowledgeDiagnosis.knowledge_id == knowledge_id,
                                                                KnowledgeDiagnosis.supersedes_id.is_(None))
                               .order_by(KnowledgeDiagnosis.created_at.desc(), KnowledgeDiagnosis.id))
        return {"student_id": student_id, "knowledge_id": knowledge_id, "items": [diagnosis_dict(r) for r in rows]}

    @app.post("/v1/diagnoses/{diagnosis_id}/retract", operation_id="retractKnowledgeDiagnosis")
    def retract_diagnosis(diagnosis_id: str, actor: ActorDep, session: SessionDep):
        _staff(actor)
        row = session.scalar(select(KnowledgeDiagnosis).where(KnowledgeDiagnosis.id == diagnosis_id).with_for_update())
        if row is None:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        require_student(session, actor, row.student_id)
        if row.status != "active":
            raise Problem(409, "conflict", "revision_conflict", "该诊断已撤回。")
        retraction = KnowledgeDiagnosis(id=new_id("diag"), tenant_id=row.tenant_id, student_id=row.student_id,
                                        knowledge_id=row.knowledge_id, kind=row.kind, scope_text=row.scope_text,
                                        evidence_ids=row.evidence_ids, status="retracted", actor_id=actor.id,
                                        supersedes_id=row.id, created_at=datetime.now(timezone.utc))
        row.status = "retracted"
        session.add(retraction)
        session.commit()
        return {"id": diagnosis_id, "status": "retracted", "retraction_id": retraction.id}

    @app.get("/v1/knowledge-link-queue", operation_id="listKnowledgeLinkQueue")
    def link_queue(actor: ActorDep, session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 30):
        _staff(actor)
        from .app import accessible_students
        students = accessible_students(actor).with_only_columns(Student.id)
        _, nodes = graph_nodes(session)
        names = {n.id: n.name for n in nodes}
        query = (select(Entity, Homework).join(Homework, Homework.id == Entity.homework_id)
                 .where(Entity.kind == "case", Homework.student_id.in_(students), Homework.processing_blocked.is_(False))
                 .order_by(Entity.id).limit(2000))
        items = []
        for case, homework in session.execute(query):
            document = case.document
            decision = document.get("current_decision")
            if document.get("lifecycle", "active") != "active" or not decision or decision.get("origin") != "staff":
                continue
            links = current_links(session, document["question_id"])
            if any(l.status == "confirmed" for l in links.values()):
                continue
            question = session.get(Entity, document["question_id"])
            items.append({"case_id": case.id, "question_id": document["question_id"], "student_id": homework.student_id,
                          "homework_title": homework.title, "question_text": question.document["stem_text"] if question else None,
                          "verdict": decision["verdict"],
                          "candidates": [_link_dict(l, names) for l in links.values() if l.status == "candidate"]})
            if len(items) == limit:
                break
        return {"items": items}
