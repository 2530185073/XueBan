"""Staff operations: campus teacher accounts and the verification queue.

Teacher accounts reuse the long-lived password account machinery. A teacher is
granted whole campuses, so learners created later are visible without per-learner
grants. The review queue lists active cases that still need a person: model
candidates awaiting verification during calibration, items the model could not
judge, and recognitions that need revision.
"""
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, Header, Query
from pydantic import Field, SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .account_auth import KEY, ID_PATTERN, Strict, mac_key, operation, password, record_operation, revoke_sessions, username
from .account_models import PasswordAccount, PrincipalCampusGrant
from .auth import Actor, TokenVerifier
from .correction_models import Entity
from .device_auth import signing_key
from .errors import Problem
from .models import Homework, Principal, PrincipalRole, Student
from .passwords import hash_password

STAFF_ROLES = ("sitting_teacher", "data_steward")


class CreateStaffAccount(Strict):
    username: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=80)
    campus_ids: list[Annotated[str, Field(pattern=ID_PATTERN)]] = Field(min_length=1, max_length=20)
    roles: list[str] = Field(min_length=1, max_length=2)
    initial_password: SecretStr


class ResetPassword(Strict):
    new_password: SecretStr


class StaffAccess(Strict):
    campus_ids: list[Annotated[str, Field(pattern=ID_PATTERN)]] = Field(min_length=1, max_length=20)
    roles: list[str] = Field(min_length=1, max_length=2)
    active: bool


def needs_staff(case: dict, attempt: dict | None, question: dict | None = None) -> str | None:
    if case["lifecycle"] != "active" or case["progress"] == "verified_resolved":
        return None
    decision = case["current_decision"]
    if decision is not None:
        if decision["validity"] == "needs_review":
            return "unable_to_judge"
        if decision["validity"] == "candidate":
            return "verify_candidate"
        return None
    if attempt is not None and attempt["recognition_status"] in {"needs_revision", "failed"}:
        return "recognition_revision"
    # A staff revision or layout repair cleared the judgment; nobody else will start the next check.
    revised = case["check_ids"] or (question is not None and question.get("source") in {"staff_revision", "staff_layout"})
    if attempt is not None and attempt["recognition_status"] == "ready" and revised:
        return "awaiting_check"
    return None


def install_staff(app, engine, cfg, database_session, current_actor):
    from .app import accessible_students
    verifier = TokenVerifier(cfg)
    ActorDep = Annotated[Actor, Depends(current_actor)]
    SessionDep = Annotated[Session, Depends(database_session)]

    def admin_scope(session, authorization):
        if not authorization or not authorization.startswith("Bearer "):
            raise Problem(401, "authorization", "authentication_required", "需要有效的 Bearer 访问凭证。")
        actor = verifier.actor(authorization[7:], session)
        if "campus_admin" not in actor.roles:
            raise Problem(403, "authorization", "access_denied", "仅获授权的校区管理员可管理老师账号。")
        session.scalar(select(Principal).where(Principal.id == actor.id).with_for_update())
        campuses = list(session.scalars(select(PrincipalCampusGrant.campus_id).where(PrincipalCampusGrant.principal_id == actor.id, PrincipalCampusGrant.tenant_id == actor.tenant_id, PrincipalCampusGrant.active.is_(True)).order_by(PrincipalCampusGrant.campus_id)))
        return actor, campuses

    def staff_item(session, account):
        roles = sorted(session.scalars(select(PrincipalRole.role).where(PrincipalRole.principal_id == account.principal_id)))
        campuses = sorted(session.scalars(select(PrincipalCampusGrant.campus_id).where(PrincipalCampusGrant.principal_id == account.principal_id, PrincipalCampusGrant.active.is_(True))))
        return {"principal_id": account.principal_id, "username": account.username, "roles": roles, "campus_ids": campuses, "active": account.active, "must_change_password": account.must_change_password}

    def manageable(session, campuses, principal_id):
        owned = set(session.scalars(select(PrincipalCampusGrant.campus_id).where(PrincipalCampusGrant.principal_id == principal_id, PrincipalCampusGrant.active.is_(True))))
        return bool(owned) and owned <= set(campuses)

    @app.get("/v1/admin/staff-accounts", operation_id="listStaffAccounts")
    def list_staff(authorization: str | None = Header(default=None)):
        with Session(engine) as session:
            actor, campuses = admin_scope(session, authorization)
            accounts = session.scalars(select(PasswordAccount).where(PasswordAccount.student_id.is_(None), PasswordAccount.tenant_id == actor.tenant_id, PasswordAccount.principal_id != actor.id).order_by(PasswordAccount.username))
            return {"items": [staff_item(session, a) for a in accounts if manageable(session, campuses, a.principal_id)], "campuses": [{"campus_id": c} for c in campuses]}

    @app.post("/v1/admin/staff-accounts", status_code=201, operation_id="createStaffAccount")
    def create_staff(payload: CreateStaffAccount, authorization: str | None = Header(default=None), idempotency_key: str = KEY):
        secret = mac_key(signing_key(cfg))
        name = username(payload.username)
        initial = password(payload.initial_password)
        roles = sorted(set(payload.roles))
        if "sitting_teacher" not in roles or not set(roles) <= set(STAFF_ROLES):
            raise Problem(422, "validation", "invalid_request", "老师账号必须包含坐班老师角色，可另加数据治理角色。")
        campus_ids = sorted(set(payload.campus_ids))
        body = {"username": name, "display_name": payload.display_name.strip(), "campus_ids": campus_ids, "roles": roles, "initial_password": initial}
        try:
            with Session(engine) as session, session.begin():
                actor, campuses = admin_scope(session, authorization)
                if not set(campus_ids) <= set(campuses):
                    raise Problem(403, "authorization", "access_denied", "所选校区不在当前管理员授权范围。")
                old = operation(session, actor.id, "create-staff", idempotency_key, body, secret)
                if old:
                    account = session.get(PasswordAccount, name)
                    if account is None or old.credential_version != account.password_version:
                        raise Problem(409, "conflict", "stale_input", "账号状态已变化，请刷新后核实。")
                    return staff_item(session, account)
                if session.get(PasswordAccount, name) is not None:
                    raise Problem(409, "conflict", "username_conflict", "该用户名已存在。")
                now = datetime.now(timezone.utc)
                principal_id = "actor_" + uuid4().hex
                session.add(Principal(id=principal_id, tenant_id=actor.tenant_id, issuer=cfg.oidc_issuer, subject="account:" + uuid4().hex, active=True))
                session.flush()
                session.add_all([PrincipalRole(principal_id=principal_id, role=role) for role in roles])
                session.add_all([PrincipalCampusGrant(principal_id=principal_id, campus_id=c, tenant_id=actor.tenant_id, active=True) for c in campus_ids])
                account = PasswordAccount(username=name, principal_id=principal_id, student_id=None, tenant_id=actor.tenant_id, password_hash=hash_password(initial), password_version=1, must_change_password=True, active=True, failed_attempts=0, created_at=now, updated_at=now)
                session.add(account)
                session.flush()
                record_operation(session, actor.id, "create-staff", idempotency_key, body, secret, credential_version=1, response_json={"principal_id": principal_id})
                return staff_item(session, account)
        except IntegrityError:
            raise Problem(409, "conflict", "username_conflict", "用户名已存在或账号绑定冲突，请刷新列表核实。") from None

    @app.post("/v1/admin/staff-accounts/{principal_id}/reset-password", operation_id="resetStaffPassword")
    def reset_staff(principal_id: str, payload: ResetPassword, authorization: str | None = Header(default=None), idempotency_key: str = KEY):
        secret = mac_key(signing_key(cfg))
        new = password(payload.new_password)
        body = {"principal_id": principal_id, "new_password": new}
        with Session(engine) as session, session.begin():
            actor, campuses = admin_scope(session, authorization)
            account = session.scalar(select(PasswordAccount).where(PasswordAccount.principal_id == principal_id, PasswordAccount.student_id.is_(None), PasswordAccount.tenant_id == actor.tenant_id).with_for_update())
            if account is None or principal_id == actor.id or not manageable(session, campuses, principal_id):
                raise Problem(404, "not_found", "not_found", "老师账号不存在或无权管理。")
            old = operation(session, actor.id, "reset-staff:" + principal_id, idempotency_key, body, secret)
            if old:
                return old.response_json
            account.password_hash = hash_password(new)
            account.password_version += 1
            account.must_change_password = True
            account.failed_attempts = 0
            account.locked_until = None
            account.updated_at = datetime.now(timezone.utc)
            revoke_sessions(session, principal_id)
            result = {"principal_id": principal_id, "must_change_password": True}
            record_operation(session, actor.id, "reset-staff:" + principal_id, idempotency_key, body, secret, credential_version=account.password_version, response_json=result)
            return result

    @app.post("/v1/admin/staff-accounts/{principal_id}/access", operation_id="updateStaffAccess")
    def update_staff_access(principal_id: str, payload: StaffAccess, authorization: str | None = Header(default=None), idempotency_key: str = KEY):
        secret = mac_key(signing_key(cfg))
        roles = sorted(set(payload.roles))
        if "sitting_teacher" not in roles or not set(roles) <= set(STAFF_ROLES):
            raise Problem(422, "validation", "invalid_request", "老师账号必须包含坐班老师角色，可另加数据治理角色。")
        campus_ids = sorted(set(payload.campus_ids))
        body = {"principal_id": principal_id, "campus_ids": campus_ids, "roles": roles, "active": payload.active}
        with Session(engine) as session, session.begin():
            actor, campuses = admin_scope(session, authorization)
            account = session.scalar(select(PasswordAccount).where(PasswordAccount.principal_id == principal_id, PasswordAccount.student_id.is_(None), PasswordAccount.tenant_id == actor.tenant_id).with_for_update())
            if account is None or principal_id == actor.id or not manageable(session, campuses, principal_id):
                raise Problem(404, "not_found", "not_found", "老师账号不存在或无权管理。")
            old = operation(session, actor.id, "access-staff:" + principal_id, idempotency_key, body, secret)
            if old:
                return old.response_json
            if not set(campus_ids) <= set(campuses):
                raise Problem(403, "authorization", "access_denied", "所选校区不在当前管理员授权范围。")
            current_roles = set(session.scalars(select(PrincipalRole.role).where(PrincipalRole.principal_id == principal_id)))
            if not current_roles <= set(STAFF_ROLES):
                raise Problem(409, "conflict", "not_staff_account", "该账号不是坐班老师账号，不能在此修改权限。")
            for role in current_roles - set(roles):
                session.delete(session.get(PrincipalRole, (principal_id, role)))
            session.add_all([PrincipalRole(principal_id=principal_id, role=role) for role in set(roles) - current_roles])
            grants = {g.campus_id: g for g in session.scalars(select(PrincipalCampusGrant).where(PrincipalCampusGrant.principal_id == principal_id).with_for_update())}
            for campus_id, grant in grants.items():
                grant.active = campus_id in campus_ids
            session.add_all([PrincipalCampusGrant(principal_id=principal_id, campus_id=c, tenant_id=actor.tenant_id, active=True) for c in campus_ids if c not in grants])
            narrowed = bool(current_roles - set(roles)) or any(g.active is False for g in grants.values()) or (account.active and not payload.active)
            account.active = payload.active
            account.updated_at = datetime.now(timezone.utc)
            if narrowed:
                revoke_sessions(session, principal_id)
            session.flush()
            result = staff_item(session, account)
            record_operation(session, actor.id, "access-staff:" + principal_id, idempotency_key, body, secret, credential_version=account.password_version, response_json=result)
            return result

    @app.get("/v1/review-queue", operation_id="listReviewQueue")
    def review_queue(actor: ActorDep, session: SessionDep, cursor: Annotated[str | None, Query(pattern=r"^case_[a-f0-9]{32}$")] = None, limit: Annotated[int, Query(ge=1, le=100)] = 30):
        if not set(actor.roles) & {"sitting_teacher", "campus_admin"}:
            raise Problem(403, "authorization", "forbidden", "此操作需要已获该学生授权的坐班老师。")
        students = accessible_students(actor).with_only_columns(Student.id, Student.display_name).subquery()
        query = select(Entity, Homework, students.c.display_name).join(Homework, Homework.id == Entity.homework_id).join(students, students.c.id == Homework.student_id).where(Entity.kind == "case", Homework.processing_blocked.is_(False))
        if cursor:
            query = query.where(Entity.id > cursor)
        scan_limit = 2000
        items, scanned, last = [], 0, None
        # Scan in id order; filtering on JSON state stays portable across SQLite and PG.
        for row, homework, display_name in session.execute(query.order_by(Entity.id).limit(scan_limit)):
            scanned += 1
            attempt = session.get(Entity, row.document["current_attempt_id"])
            question = session.get(Entity, row.document["question_id"])
            reason = needs_staff(row.document, attempt.document if attempt else None, question.document if question else None)
            if reason is not None:
                if len(items) == limit:
                    return {"items": items, "next_cursor": last}
                items.append({"case_id": row.id, "homework_id": homework.id, "student_id": homework.student_id, "student_name": display_name, "homework_title": homework.title, "reason": reason, "progress": row.document["progress"], "updated_at": row.document["updated_at"]})
            last = row.id
        return {"items": items, "next_cursor": last if scanned == scan_limit else None}
