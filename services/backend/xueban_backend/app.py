import base64
import binascii
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
from typing import Annotated, Any
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, object_session

from .auth import Actor, TokenVerifier
from .config import Settings
from .contracts import Contracts
from .db import create_database_engine
from .errors import Problem, problem_response
from .models import Homework, IdempotencyRecord, Student, StudentGrant

ID_PATTERN = r"^[a-z][a-z0-9_-]{2,63}$"
ResourceId = Annotated[str, Path(pattern=ID_PATTERN)]
security = HTTPBearer(auto_error=False)


def utc_string(value: datetime) -> str:
    return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    try:
        if not isinstance(value, str):
            raise TypeError("Timestamp must be a string")
        parsed = datetime.fromisoformat(value.replace("z", "+00:00").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timezone required")
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise Problem(422, "validation", "invalid_request", "时间必须是带时区的 RFC3339 日期时间。") from None


def homework_json(row: Homework) -> dict:
    from .correction_models import Asset, Entity, Task
    session = object_session(row)
    entities = session.scalars(select(Entity).where(Entity.homework_id == row.id).order_by(Entity.id)).all() if session else []
    return {
        "id": row.id,
        "revision": row.revision,
        "scope": {"tenant_id": row.tenant_id, "campus_id": row.campus_id, "student_id": row.student_id},
        "curriculum": row.curriculum,
        "title": row.title,
        "created_at": utc_string(row.created_at),
        "occurred_at": utc_string(row.occurred_at),
        "coverage": row.coverage_json or {"collection_scope": row.collection_scope, "unrecognized_regions": [], "note": None},
        "asset_ids": list(session.scalars(select(Asset.id).where(Asset.homework_id == row.id).order_by(Asset.id))) if session else [],
        "question_ids": [entity.id for entity in entities if entity.kind == "question"],
        "case_ids": [entity.id for entity in entities if entity.kind == "case"],
        "job_ids": list(session.scalars(select(Task.id).where(Task.homework_id == row.id).order_by(Task.id))) if session else [],
        "processing_blocked": row.processing_blocked,
    }


def accessible_students(actor: Actor):
    from .account_models import PrincipalCampusGrant
    granted = select(StudentGrant.student_id).where(StudentGrant.principal_id == actor.id, StudentGrant.active.is_(True), StudentGrant.tenant_id == actor.tenant_id)
    if actor.bound_student_id:
        return select(Student).where(Student.id == actor.bound_student_id, Student.id.in_(granted), Student.tenant_id == actor.tenant_id, Student.active.is_(True))
    conditions = [Student.id.in_(granted)]
    if set(actor.roles) & {"sitting_teacher", "campus_admin"}:
        # Staff serve every learner of a campus they are explicitly granted.
        campuses = select(PrincipalCampusGrant.campus_id).where(PrincipalCampusGrant.principal_id == actor.id, PrincipalCampusGrant.tenant_id == actor.tenant_id, PrincipalCampusGrant.active.is_(True))
        conditions.append(Student.campus_id.in_(campuses))
    return select(Student).where(or_(*conditions), Student.tenant_id == actor.tenant_id, Student.active.is_(True))


def require_student(session: Session, actor: Actor, student_id: str) -> Student:
    student = session.scalar(accessible_students(actor).where(Student.id == student_id))
    if student is None:
        raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
    return student


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings.from_environment()
    engine = create_database_engine(cfg.database_url, allow_sqlite=cfg.allow_sqlite_for_tests)
    contracts = Contracts(cfg.contracts_dir)
    verifier = TokenVerifier(cfg)
    for origin in cfg.cors_allowed_origins:
        parts = urlsplit(origin)
        if "*" in origin or parts.scheme not in {"http", "https"} or not parts.netloc or parts.path or parts.query or parts.fragment or parts.username:
            raise RuntimeError("CORS origins must be exact scheme://host[:port] values")

    @asynccontextmanager
    async def lifespan(_):
        yield
        engine.dispose()

    app = FastAPI(title="XueBan photo correction service", version="0.2.0", lifespan=lifespan)
    app.state.engine = engine
    app.state.settings = cfg
    app.state.verifier = verifier
    if cfg.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cfg.cors_allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Upload-Token"],
            expose_headers=["X-Request-Id"],
        )

    @app.middleware("http")
    async def request_identifier(request: Request, call_next):
        request.state.request_id = "request_" + uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Problem)
    async def handle_problem(request: Request, problem: Problem):
        return problem_response(request, problem)

    @app.exception_handler(RequestValidationError)
    async def handle_invalid_request(request: Request, _):
        return problem_response(request, Problem(422, "validation", "invalid_request", "请求字段、路径或查询参数不合法。"))

    @app.exception_handler(SQLAlchemyError)
    async def handle_database_error(request: Request, _):
        # Do not leak SQL, credentials, tokens, or student data into error bodies.
        return problem_response(request, Problem(503, "service", "service_unavailable", "数据服务暂不可用，请使用原幂等键重试。"))

    def database_session():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    def current_actor(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
        session: Annotated[Session, Depends(database_session)],
    ) -> Actor:
        return verifier.actor(credentials.credentials if credentials else None, session)

    ActorDep = Annotated[Actor, Depends(current_actor)]
    SessionDep = Annotated[Session, Depends(database_session)]

    def checked(name: str, value: dict) -> dict:
        contracts.validate(name, value)
        return value

    @app.get("/v1/me", operation_id="getIdentityContext")
    def me(actor: ActorDep, session: SessionDep):
        students = session.scalars(accessible_students(actor).order_by(Student.id)).all()
        return checked("IdentityContext", {
            "actor_id": actor.id,
            "roles": list(actor.roles),
            "authorized_students": [{"student_id": student.id, "display_name": student.display_name, "campus_id": student.campus_id} for student in students],
        })

    @app.post("/v1/students/{student_id}/homeworks", status_code=201, operation_id="createHomework")
    def create_homework(
        student_id: ResourceId,
        payload: dict[str, Any],
        actor: ActorDep,
        session: SessionDep,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")],
    ):
        student = require_student(session, actor, student_id)
        contracts.validate("CreateHomework", payload, kind="requests")
        occurred_at = parse_time(payload["occurred_at"])
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        operation = "POST /v1/students/{student_id}/homeworks"
        now = datetime.now(timezone.utc)
        record = IdempotencyRecord(
            id="idempotency_" + uuid4().hex,
            principal_id=actor.id,
            student_id=student_id,
            operation=operation,
            key=idempotency_key,
            payload_hash=payload_hash,
            created_at=now,
        )
        try:
            # A competing insert waits on the unique key until its transaction
            # commits. The savepoint keeps our authorization transaction usable.
            with session.begin_nested():
                session.add(record)
                session.flush()
        except IntegrityError:
            existing = session.scalar(select(IdempotencyRecord).where(
                IdempotencyRecord.principal_id == actor.id,
                IdempotencyRecord.student_id == student_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.key == idempotency_key,
            ))
            if existing is None or existing.response_json is None:
                raise Problem(503, "service", "service_unavailable", "提交尚未完成，请使用原幂等键重试。") from None
            if existing.payload_hash != payload_hash:
                raise Problem(409, "conflict", "idempotency_conflict", "此幂等键已用于不同请求，请保留原操作或为新的操作使用新键。")
            return checked("HomeworkSession", existing.response_json)

        homework = Homework(
            id="homework_" + uuid4().hex,
            tenant_id=student.tenant_id,
            campus_id=student.campus_id,
            student_id=student.id,
            revision=1,
            curriculum=payload["curriculum"],
            title=payload["title"],
            collection_scope=payload["collection_scope"],
            created_at=now,
            occurred_at=occurred_at,
            processing_blocked=False,
        )
        session.add(homework)
        session.flush()
        response = checked("HomeworkSession", homework_json(homework))
        record.response_json = response
        session.commit()
        return response

    @app.get("/v1/homeworks/{homework_id}", operation_id="getHomework")
    def get_homework(homework_id: ResourceId, actor: ActorDep, session: SessionDep):
        homework = session.scalar(select(Homework).where(Homework.id == homework_id, Homework.tenant_id == actor.tenant_id))
        if homework is None:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        require_student(session, actor, homework.student_id)
        return checked("HomeworkSession", homework_json(homework))

    @app.get("/v1/students/{student_id}/homeworks", operation_id="listHomeworks")
    def list_homeworks(
        student_id: ResourceId,
        actor: ActorDep,
        session: SessionDep,
        cursor: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ):
        require_student(session, actor, student_id)
        query = select(Homework).where(Homework.student_id == student_id, Homework.tenant_id == actor.tenant_id)
        if cursor:
            try:
                position = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
                if not isinstance(position, dict) or set(position) != {"student_id", "created_at", "id"} or position["student_id"] != student_id:
                    raise ValueError("Cursor scope mismatch")
                timestamp = parse_time(position["created_at"])
                if not isinstance(position["id"], str) or not position["id"].startswith("homework_") or len(position["id"]) > 64:
                    raise ValueError("Invalid cursor ID")
            except (ValueError, TypeError, KeyError, binascii.Error, UnicodeDecodeError):
                raise Problem(422, "validation", "invalid_request", "游标无效或不属于当前学生。") from None
            query = query.where(or_(Homework.created_at < timestamp, and_(Homework.created_at == timestamp, Homework.id < position["id"])))
        rows = session.scalars(query.order_by(Homework.created_at.desc(), Homework.id.desc()).limit(limit + 1)).all()
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = base64.urlsafe_b64encode(json.dumps({"student_id": student_id, "created_at": utc_string(last.created_at), "id": last.id}, separators=(",", ":")).encode()).decode().rstrip("=")
        return checked("HomeworkPage", {"items": [homework_json(row) for row in rows[:limit]], "next_cursor": next_cursor})

    from .device_auth import install_device_auth
    from .correction_api import install_correction
    install_device_auth(app, engine, cfg)
    from .account_auth import install_account_auth
    install_account_auth(app, engine, cfg)
    install_correction(app, engine, cfg, database_session, current_actor)
    from .governance import install_governance
    install_governance(app, engine, cfg, database_session, current_actor)
    from .staff import install_staff
    install_staff(app, engine, cfg, database_session, current_actor)
    from .service_metrics import install_service_metrics
    install_service_metrics(app, engine, cfg)
    from .learning_api import install_learning
    install_learning(app, engine, cfg, database_session, current_actor)
    from .agent_memory import install_agent
    install_agent(app, engine, cfg, database_session, current_actor)
    from .learning_reports import install_reports
    install_reports(app, engine, cfg, database_session, current_actor)
    from .practice import install_practice
    install_practice(app, engine, cfg, database_session, current_actor)
    from .ops import install_ops
    install_ops(app, engine, cfg)
    from .student_web import install_student_web
    install_student_web(app, cfg.student_web_dist)
    from .staff_web import install_staff_web
    install_staff_web(app)
    return app
