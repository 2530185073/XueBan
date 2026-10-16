"""Phase-1 correction HTTP commands. No model execution occurs in requests."""
from copy import deepcopy
import asyncio
from datetime import timedelta, timezone
import hashlib
import secrets
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Header, Query, Request, Response
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from .asset_store import LocalAssetStore, MAX_ASSET_BYTES, MAX_IMAGE_PIXELS
from .auth import Actor
from .contracts import Contracts
from .correction_core import (assets_for, conflict, create_task, event, expect, get_entity, input_versions,
    invalidate, iso, new_id, now, reserve_idempotency, save_entity, update_case)
from .correction_models import Asset, Entity, EntitySnapshot, Task
from .errors import Problem
from .models import Homework, Principal, PrincipalRole, Student


def install_correction(app, engine, cfg, database_session, current_actor):
    from .app import accessible_students, parse_time, require_student

    contracts = Contracts(cfg.contracts_dir)
    store = LocalAssetStore(cfg.asset_storage_dir)
    ActorDep = Annotated[Actor, Depends(current_actor)]
    SessionDep = Annotated[Session, Depends(database_session)]
    KeyDep = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]

    def checked(name, value):
        contracts.validate(name, value)
        return value

    def authorize(session, actor, homework_id, *, lock=False):
        query = select(Homework).where(Homework.id == homework_id, Homework.tenant_id == actor.tenant_id)
        if lock:
            query = query.with_for_update()
        homework = session.scalar(query.execution_options(populate_existing=True))
        if homework is None:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        require_student(session, actor, homework.student_id)
        if homework.processing_blocked:
            raise Problem(409, "conflict", "processing_blocked", "该作业已停止处理。")
        return homework

    def locate(session, actor, identifier, resource, *, lock=False):
        if resource == "homework":
            homework = authorize(session, actor, identifier, lock=lock)
            return homework, homework
        model = Asset if resource == "asset" else Task if resource == "job" else Entity
        row = session.get(model, identifier)
        if row is None or (model is Entity and row.kind != resource):
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        homework = authorize(session, actor, row.homework_id, lock=lock)
        if lock:
            session.refresh(row)
        return row, homework

    def teacher(actor):
        if not set(actor.roles) & {"sitting_teacher", "campus_admin"}:
            raise Problem(403, "authorization", "forbidden", "此操作需要已获该学生授权的坐班老师。")

    def mutate(path, operation, resource, request_schema, response_schema, action, status=200, staff=False):
        def endpoint(request: Request, payload: dict, actor: ActorDep, session: SessionDep, idempotency_key: KeyDep):
            if staff:
                teacher(actor)
            contracts.validate(request_schema, payload, kind="requests")
            identifier = next(iter(request.path_params.values()))
            row, homework = locate(session, actor, identifier, resource, lock=True)
            record, replay = reserve_idempotency(session, actor, homework.student_id, "POST " + request.url.path, idempotency_key, payload)
            if replay is not None:
                return checked(response_schema, replay)
            expect(row.revision, payload["expected_revision"])
            result = checked(response_schema, action(session, actor, homework, row, payload))
            record.response_json = result
            session.commit()
            return result
        app.add_api_route(path, endpoint, methods=["POST"], status_code=status, operation_id=operation)

    def read(path, operation, resource, schema, wrapper=False):
        def endpoint(request: Request, actor: ActorDep, session: SessionDep, revision: Annotated[int | None, Query(ge=1)] = None):
            identifier = next(iter(request.path_params.values()))
            row, _ = locate(session, actor, identifier, resource)
            document = row.document
            if revision is not None and wrapper:
                snapshot = session.get(EntitySnapshot, (row.id, revision))
                if snapshot is None:
                    raise Problem(404, "not_found", "not_found", "该历史版本不存在。")
                document = snapshot.document
            if wrapper:
                effective = document.get("revision") == row.revision and row.document.get("lifecycle", "active") == "active" and row.document.get("validity") not in {"stale", "retracted"}
                if resource in {"attempt", "decision"}:
                    case = get_entity(session, document["case_id"], "case")
                    effective = effective and case.document["lifecycle"] == "active"
                    if resource == "attempt":
                        effective = effective and case.document["current_attempt_id"] == row.id
                    else:
                        effective = effective and (case.document["current_decision"] or {}).get("id") == row.id
                document = {"record": document, "current_revision": row.revision, "currently_effective": effective}
            return checked(schema, document)
        app.add_api_route(path, endpoint, methods=["GET"], operation_id=operation)

    def base_url():
        parts = urlsplit(cfg.public_base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.path.rstrip("/") or parts.query or parts.fragment or parts.username or parts.password or "\\" in cfg.public_base_url:
            raise Problem(503, "service", "storage_unconfigured", "服务公共地址尚未正确配置。")
        if cfg.asset_storage_dir is None:
            raise Problem(503, "service", "storage_unconfigured", "图片存储尚未配置。")
        return cfg.public_base_url.rstrip("/")

    def issue_ticket(asset, actor):
        token = secrets.token_urlsafe(32)
        expiry = now() + timedelta(seconds=max(30, min(cfg.upload_ticket_ttl_seconds, 1800)))
        asset.upload_token_hash = hashlib.sha256(token.encode()).hexdigest()
        asset.upload_actor_id = actor.id
        asset.upload_expires_at = expiry
        return {"asset": asset.document, "put_url": base_url() + f"/v1/assets/{asset.id}/content", "expires_at": iso(expiry), "required_headers": {"X-Upload-Token": token, "Content-Type": asset.document["mime_type"]}}

    def upload(session, actor, homework, _, payload):
        base_url()
        if payload["byte_size"] > MAX_ASSET_BYTES or payload["width"] * payload["height"] > MAX_IMAGE_PIXELS or max(payload["width"], payload["height"]) > 30000:
            raise Problem(422, "validation", "image_too_large", "单张图片最多 4 MiB、2400 万像素。")
        identifier = new_id("asset")
        doc = {key: payload[key] for key in ("page_index", "sha256", "byte_size", "mime_type", "width", "height")}
        doc.update(id=identifier, revision=1, homework_id=homework.id, status="awaiting_upload", source_asset_id=None, source_region=None, created_at=iso())
        asset = Asset(id=identifier, homework_id=homework.id, revision=1, document=doc)
        session.add(asset)
        homework.revision += 1
        event(session, homework.id, identifier, 1, "asset.created")
        return issue_ticket(asset, actor)

    def renew(session, actor, homework, asset, payload):
        if asset.document["status"] != "awaiting_upload":
            conflict(asset.revision)
        asset.revision += 1
        asset.document = {**asset.document, "revision": asset.revision}
        return issue_ticket(asset, actor)

    def ticket_actor(session, asset, token, purpose):
        digest = getattr(asset, purpose + "_token_hash")
        expires = getattr(asset, purpose + "_expires_at")
        actor_id = getattr(asset, purpose + "_actor_id")
        if not token or not digest or not secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(), digest) or expires is None or expires.replace(tzinfo=expires.tzinfo or timezone.utc) <= now():
            raise Problem(401, "authorization", "invalid_asset_ticket", "图片票据无效或已过期。")
        principal = session.get(Principal, actor_id)
        roles = tuple(session.scalars(select(PrincipalRole.role).where(PrincipalRole.principal_id == actor_id)))
        if principal is None or not principal.active or not set(roles) & {"student", "sitting_teacher", "campus_admin"}:
            raise Problem(401, "authorization", "invalid_asset_ticket", "图片票据已失效。")
        from .account_models import PasswordAccount
        account = session.scalar(select(PasswordAccount).where(PasswordAccount.principal_id == principal.id))
        if account is not None and (not account.active or account.must_change_password):
            raise Problem(401, "authorization", "invalid_asset_ticket", "账号状态变化，图片票据已失效。")
        actor = Actor(principal.id, principal.tenant_id, roles, account.student_id if account else None)
        authorize(session, actor, asset.homework_id)
        return actor

    @app.put("/v1/assets/{asset_id}/content", operation_id="putPrivateAssetBytes", status_code=204)
    async def put_content(asset_id: str, request: Request, token: Annotated[str | None, Header(alias="X-Upload-Token")] = None):
        def authorized_size():
            with Session(engine) as session:
                asset = session.get(Asset, asset_id)
                if asset is None:
                    raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
                ticket_actor(session, asset, token, "upload")
                if asset.document["status"] != "awaiting_upload":
                    conflict(asset.revision)
                return min(asset.document["byte_size"], MAX_ASSET_BYTES)
        # No transaction or row lock spans a network await. Synchronous DB/file
        # work runs in the threadpool, never blocks the ASGI event loop.
        byte_limit = await run_in_threadpool(authorized_size)
        content = bytearray()
        try:
            async with asyncio.timeout(30):
                async for chunk in request.stream():
                    content.extend(chunk)
                    if len(content) > byte_limit:
                        raise Problem(422, "validation", "asset_integrity_mismatch", "图片大小超过上传声明。")
        except TimeoutError:
            raise Problem(422, "validation", "upload_timeout", "图片上传超时，请使用原票据重试。") from None
        def commit_bytes():
            with Session(engine) as session:
                asset = session.get(Asset, asset_id)
                if asset is None:
                    raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
                actor = ticket_actor(session, asset, token, "upload")
                authorize(session, actor, asset.homework_id, lock=True)
                session.refresh(asset)
                ticket_actor(session, asset, token, "upload")
                if asset.document["status"] != "awaiting_upload":
                    conflict(asset.revision)
                store.put(asset.id, bytes(content), asset.document)
                session.commit()
        await run_in_threadpool(commit_bytes)
        return Response(status_code=204)

    def complete(session, actor, homework, asset, payload):
        if asset.document["status"] != "awaiting_upload":
            conflict(asset.revision)
        if payload["sha256"] != asset.document["sha256"] or payload["byte_size"] != asset.document["byte_size"]:
            raise Problem(422, "validation", "asset_integrity_mismatch", "完整性确认与原上传声明不一致。")
        store.read(asset.id, asset.document)
        asset.revision += 1
        asset.document = {**asset.document, "revision": asset.revision, "status": "uploaded"}
        asset.upload_token_hash = None
        homework.revision += 1
        event(session, homework.id, asset.id, asset.revision, "asset.completed")
        return asset.document

    @app.get("/v1/assets/{asset_id}/access", operation_id="getAssetAccess")
    def access(asset_id: str, actor: ActorDep, session: SessionDep):
        asset, _ = locate(session, actor, asset_id, "asset", lock=True)
        if asset.document["status"] != "uploaded":
            conflict(asset.revision)
        token = secrets.token_urlsafe(32)
        expiry = now() + timedelta(seconds=120)
        asset.read_token_hash = hashlib.sha256(token.encode()).hexdigest()
        asset.read_actor_id = actor.id
        asset.read_expires_at = expiry
        result = checked("AssetAccess", {"asset_id": asset.id, "read_url": base_url() + f"/v1/assets/{asset.id}/content?ticket={token}", "expires_at": iso(expiry)})
        session.commit()
        return result

    @app.get("/v1/assets/{asset_id}/content", operation_id="getPrivateAssetBytes")
    def get_content(asset_id: str, session: SessionDep, ticket: Annotated[str, Query(min_length=20, max_length=100)]):
        asset = session.get(Asset, asset_id)
        if asset is None:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        ticket_actor(session, asset, ticket, "read")
        if asset.document["status"] != "uploaded":
            conflict(asset.revision)
        return Response(store.read(asset.id, asset.document), media_type=asset.document["mime_type"], headers={"X-Content-Type-Options": "nosniff"})

    def recognize(session, actor, homework, _, payload):
        assets_for(session, homework.id, payload["asset_ids"])
        existing = session.scalars(select(Task).where(Task.homework_id == homework.id, Task.kind == "recognize_homework", Task.status.not_in(["failed", "cancelled", "superseded"]))).all()
        if any(set(t.inputs["asset_ids"]) & set(payload["asset_ids"]) for t in existing):
            conflict(homework.revision, "recognition_already_started")
        _, accepted = create_task(session, contracts, homework, "recognize_homework", {"asset_ids": payload["asset_ids"]})
        homework.revision += 1
        return accepted

    def active_case(case):
        if case.document["lifecycle"] != "active":
            conflict(case.revision)

    def context(session, homework, case, payload):
        active_case(case)
        question = get_entity(session, case.document["question_id"], "question")
        attempt = get_entity(session, payload["attempt_id"], "attempt")
        if attempt.homework_id != homework.id or attempt.document["case_id"] != case.id:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        if attempt.id != case.document["current_attempt_id"]:
            conflict(case.revision)
        expect(question.revision, payload["question_revision"])
        expect(attempt.document["recognition_revision"], payload["attempt_recognition_revision"])
        if attempt.document["recognition_status"] in {"pending", "failed"}:
            conflict(case.revision, "recognition_not_ready")
        ids = list(dict.fromkeys([r["asset_id"] for r in question.document["regions"]] + attempt.document["asset_ids"]))
        assets = assets_for(session, homework.id, ids)
        return question, attempt, assets, input_versions(case, question, attempt, assets)

    def check(session, actor, homework, case, payload):
        question, attempt, assets, versions = context(session, homework, case, payload)
        for task in session.scalars(select(Task).where(Task.homework_id == homework.id, Task.kind == "check", Task.status.in_(["queued", "running", "retryable_failed"]))):
            if task.inputs.get("case_id") == case.id and task.inputs.get("input_versions", {}).get("fingerprint") == versions["fingerprint"]:
                conflict(case.revision, "check_already_started")
        identifier = new_id("check")
        task, accepted = create_task(session, contracts, homework, "check", {"case_id": case.id, "check_id": identifier, "input_versions": versions, "asset_ids": [a.id for a in assets], "decision_id_at_submission": (case.document["current_decision"] or {}).get("id")}, {"resource_type": "check", "resource_id": identifier})
        save_entity(session, contracts, homework.id, "check", {"id": identifier, "revision": 1, "job_id": task.id, "case_id": case.id, "input_versions": versions, "model_version": None, "rule_version": None, "prompt_version": None, "decision_ids": [], "created_at": iso()})
        update_case(session, contracts, case, check_ids=case.document["check_ids"] + [identifier], progress="rechecking")
        return accepted

    def hint(session, actor, homework, case, payload):
        active_case(case)
        source = case.document["current_decision"]
        if source is None or source["id"] != payload["source_decision_id"] or source["revision"] != payload["source_decision_revision"] or source["validity"] not in {"candidate", "needs_review", "confirmed"} or payload["attempt_id"] != case.document["current_attempt_id"]:
            conflict(case.revision, "hint_source_stale")
        question = get_entity(session, case.document["question_id"], "question")
        attempt = get_entity(session, payload["attempt_id"], "attempt")
        ids = list(dict.fromkeys([r["asset_id"] for r in question.document["regions"]] + attempt.document["asset_ids"]))
        assets = assets_for(session, homework.id, ids)
        versions = input_versions(case, question, attempt, assets)
        tasks = session.scalars(select(Task).where(Task.homework_id == homework.id, Task.kind == "hint", Task.status.in_(["queued", "running", "retryable_failed", "succeeded"])))
        for task in tasks:
            inputs = task.inputs
            if inputs.get("case_id") == case.id and inputs.get("source_decision_id") == source["id"] and inputs.get("source_decision_revision") == source["revision"] and inputs.get("level") == payload["level"] and inputs.get("input_versions", {}).get("fingerprint") == versions["fingerprint"]:
                return {"job": task.document, "created_resource": task.document["result"]}
        _, accepted = create_task(session, contracts, homework, "hint", {"case_id": case.id, "input_versions": versions, "asset_ids": ids, "source_decision_id": source["id"], "source_decision_revision": source["revision"], "level": payload["level"]})
        update_case(session, contracts, case)
        return accepted

    def correction(session, actor, homework, case, payload):
        active_case(case)
        if payload["parent_attempt_id"] != case.document["current_attempt_id"]:
            conflict(case.revision)
        question = get_entity(session, case.document["question_id"], "question")
        expect(question.revision, payload["question_revision"])
        assets_for(session, homework.id, payload["asset_ids"])
        identifier = new_id("attempt")
        parent = get_entity(session, payload["parent_attempt_id"], "attempt")
        help_order = {"unknown": 0, "none_reported": 1, "hint_used": 2, "staff_help": 3, "answer_shown": 4}
        help_condition = max((parent.document["help_condition"], payload["reported_help"]), key=lambda value: help_order[value])
        doc = {"id": identifier, "revision": 1, "case_id": case.id, "question_id": question.id, "captured_question_revision": question.revision, "parent_attempt_id": payload["parent_attempt_id"], "root_attempt_id": case.document["root_attempt_id"], "asset_ids": payload["asset_ids"], "recognition_revision": 1, "answer_text": None, "answer_regions": [], "recognition_status": "pending", "help_condition": help_condition, "occurred_at": iso(parse_time(payload["occurred_at"])), "created_at": iso()}
        save_entity(session, contracts, homework.id, "attempt", doc)
        invalidate(session, contracts, case, "new_attempt")
        update_case(session, contracts, case, current_attempt_id=identifier, current_decision=None, attempt_ids=case.document["attempt_ids"] + [identifier], progress="editing")
        _, accepted = create_task(session, contracts, homework, "recognize_attempt", {"case_id": case.id, "attempt_id": identifier, "question_id": question.id, "question_revision": question.revision, "asset_ids": payload["asset_ids"]}, {"resource_type": "attempt", "resource_id": identifier})
        return accepted

    def show_hint(session, actor, homework, hint_row, payload):
        if hint_row.document["validity"] != "active":
            conflict(hint_row.revision)
        case = get_entity(session, hint_row.document["case_id"], "case")
        if case.document["current_attempt_id"] != hint_row.document["attempt_id"]:
            conflict(case.revision)
        doc = {**hint_row.document, "revision": hint_row.revision + 1, "first_shown_at": hint_row.document["first_shown_at"] or iso(parse_time(payload["shown_at"]))}
        save_entity(session, contracts, homework.id, "hint", doc, row=hint_row)
        attempt = get_entity(session, doc["attempt_id"], "attempt")
        if attempt.document["help_condition"] not in {"staff_help", "answer_shown"}:
            adoc = {**attempt.document, "revision": attempt.revision + 1, "help_condition": "answer_shown" if doc["exposes_answer"] else "hint_used"}
            save_entity(session, contracts, homework.id, "attempt", adoc, row=attempt)
        return doc

    def review(session, actor, homework, case, payload):
        question, attempt, assets, versions = context(session, homework, case, payload)
        current = case.document["current_decision"]
        if payload["action"] == "manual_initial":
            if current is not None:
                conflict(case.revision)
        elif current is None or (current["id"], current["revision"]) != (payload["target_decision_id"], payload["target_decision_revision"]):
            conflict(case.revision)
        if payload["action"] == "confirm" and current["verdict"] != payload["verdict"]:
            conflict(case.revision, "confirmation_verdict_mismatch")
        validate_regions(session, homework, [region for basis in payload["basis"] for region in basis["regions"]])
        invalidate(session, contracts, case, "staff_review")
        new_decision = None
        if payload["action"] in {"confirm", "replace", "manual_initial"}:
            new_decision = {"id": new_id("decision"), "revision": 1, "case_id": case.id, "check_id": None, "input_versions": versions, "origin": "staff", "validity": "confirmed", "verdict": payload["verdict"], "reason_code": None, "basis": payload["basis"], "first_error_region": None, "confirmation_source": "staff_review", "confirmed_by": actor.id, "confirmed_at": iso(), "replaces_decision_id": current["id"] if current else None, "invalidated_by_event_id": None, "created_at": iso()}
            save_entity(session, contracts, homework.id, "decision", new_decision)
        doc = {"id": new_id("review"), "case_id": case.id, "target_decision_id": payload["target_decision_id"], "target_decision_revision": payload["target_decision_revision"], "action": payload["action"], "new_decision_id": new_decision["id"] if new_decision else None, "reason": payload["reason"], "reviewer_id": actor.id, "created_at": iso()}
        save_entity(session, contracts, homework.id, "review", doc)
        update_case(session, contracts, case, current_decision=new_decision, review_ids=case.document["review_ids"] + [doc["id"]], progress="verified_resolved" if new_decision and new_decision["verdict"] == "correct" else "needs_correction")
        return doc

    def validate_regions(session, homework, regions):
        for region in regions:
            asset = session.get(Asset, region["asset_id"])
            if asset is None or asset.homework_id != homework.id or asset.document["status"] != "uploaded" or region["x"] + region["width"] > asset.document["width"] or region["y"] + region["height"] > asset.document["height"]:
                raise Problem(422, "validation", "invalid_region", "题区必须位于本作业已上传的原图内。")

    def revise_question(session, actor, homework, question, payload):
        if question.document["lifecycle"] != "active":
            conflict(question.revision)
        validate_regions(session, homework, payload["regions"])
        doc = {**question.document, "revision": question.revision + 1, "stem_text": payload["stem_text"], "regions": payload["regions"], "source": "staff_revision", "source_actor_id": actor.id, "created_at": iso()}
        save_entity(session, contracts, homework.id, "question", doc, row=question)
        for case in session.scalars(select(Entity).where(Entity.homework_id == homework.id, Entity.kind == "case")):
            if case.document["question_id"] == question.id:
                invalidate(session, contracts, case, "question_revision")
                update_case(session, contracts, case, current_question_revision=question.revision, current_decision=None, progress="pending")
        return doc

    def revise_answer(session, actor, homework, attempt, payload):
        validate_regions(session, homework, payload["answer_regions"])
        if any(r["asset_id"] not in attempt.document["asset_ids"] for r in payload["answer_regions"]):
            raise Problem(422, "validation", "invalid_region", "答案区域必须属于该次作答图片。")
        doc = {**attempt.document, "revision": attempt.revision + 1, "recognition_revision": attempt.document["recognition_revision"] + 1, "answer_text": payload["answer_text"], "answer_regions": payload["answer_regions"], "recognition_status": "ready"}
        save_entity(session, contracts, homework.id, "attempt", doc, row=attempt)
        attempt.annotations = {"answer_status": "present" if payload["answer_text"] else "unclear"}
        case = get_entity(session, attempt.document["case_id"], "case")
        if case.document["current_attempt_id"] == attempt.id:
            invalidate(session, contracts, case, "answer_recognition_revision")
            update_case(session, contracts, case, current_decision=None, progress="pending")
        return doc

    def progress(session, actor, homework, case, payload):
        active_case(case)
        if case.document["progress"] == "verified_resolved":
            conflict(case.revision)
        return update_case(session, contracts, case, progress="paused" if payload["action"] == "pause" else "pending")

    def create_help(session, actor, homework, case, payload):
        active_case(case)
        if payload["attempt_id"] != case.document["current_attempt_id"]:
            conflict(case.revision)
        doc = {"id": new_id("help"), "revision": 1, "case_id": case.id, "attempt_id": payload["attempt_id"], "kind": payload["kind"], "note": payload["note"], "status": "waiting", "claimed_by": None, "created_at": iso(), "claimed_at": None, "completed_at": None, "resolution_note": None}
        save_entity(session, contracts, homework.id, "help", doc)
        update_case(session, contracts, case, help_request_ids=case.document["help_request_ids"] + [doc["id"]])
        return doc

    def claim_help(session, actor, homework, row, payload):
        if row.document["status"] != "waiting":
            conflict(row.revision)
        doc = {**row.document, "revision": row.revision + 1, "status": "claimed", "claimed_by": actor.id, "claimed_at": iso()}
        save_entity(session, contracts, homework.id, "help", doc, row=row)
        return doc

    def complete_help(session, actor, homework, row, payload):
        if row.document["status"] != "claimed" or row.document["claimed_by"] != actor.id:
            conflict(row.revision)
        doc = {**row.document, "revision": row.revision + 1, "status": payload["outcome"], "resolution_note": payload["note"], "completed_at": iso()}
        save_entity(session, contracts, homework.id, "help", doc, row=row)
        attempt = get_entity(session, row.document["attempt_id"], "attempt")
        if attempt.document["help_condition"] != "answer_shown":
            save_entity(session, contracts, homework.id, "attempt", {**attempt.document, "revision": attempt.revision + 1, "help_condition": "staff_help"}, row=attempt)
        # A help completion is not a correctness confirmation. Review is explicit.
        return doc

    @app.get("/v1/help-requests", operation_id="listHelpRequests")
    def list_help(actor: ActorDep, session: SessionDep, cursor: Annotated[str | None, Query(min_length=1, max_length=500)] = None, limit: Annotated[int, Query(ge=1, le=100)] = 30):
        teacher(actor)
        students = accessible_students(actor).with_only_columns(Student.id)
        query = select(Entity).join(Homework, Homework.id == Entity.homework_id).where(Entity.kind == "help", Homework.student_id.in_(students), Homework.processing_blocked.is_(False), Entity.document["status"].as_string().in_(["waiting", "claimed"]))
        if cursor:
            if not cursor.startswith("help_") or not cursor[5:].isalnum() or len(cursor) > 64:
                raise Problem(422, "validation", "invalid_request", "待办游标无效。")
            query = query.where(Entity.id > cursor)
        rows = list(session.scalars(query.order_by(Entity.id).limit(limit + 1)))
        return checked("HelpPage", {"items": [row.document for row in rows[:limit]], "next_cursor": rows[limit - 1].id if len(rows) > limit else None})

    def repair_layout(session, actor, homework, _, payload):
        from .layout_repair import accept_repair
        return accept_repair(session, contracts, actor, homework, payload)

    mutate("/v1/homeworks/{homework_id}/uploads", "createUpload", "homework", "CreateUpload", "UploadTicket", upload, 201)
    mutate("/v1/homeworks/{homework_id}/layout-revisions", "repairQuestionLayout", "homework", "RepairLayout", "Accepted", repair_layout, 202, True)
    mutate("/v1/assets/{asset_id}/upload-ticket", "renewUploadTicket", "asset", "RenewUpload", "UploadTicket", renew)
    mutate("/v1/assets/{asset_id}/complete", "completeUpload", "asset", "CompleteUpload", "ImageAsset", complete)
    mutate("/v1/homeworks/{homework_id}/recognitions", "startRecognition", "homework", "StartRecognition", "Accepted", recognize, 202)
    mutate("/v1/cases/{case_id}/checks", "startCheck", "case", "StartCheck", "Accepted", check, 202)
    mutate("/v1/cases/{case_id}/hints", "requestHint", "case", "RequestHint", "Accepted", hint, 202)
    mutate("/v1/cases/{case_id}/attempts", "createCorrection", "case", "CreateCorrection", "Accepted", correction, 202)
    mutate("/v1/hints/{hint_id}/views", "recordHintView", "hint", "ShowHint", "Hint", show_hint)
    mutate("/v1/cases/{case_id}/reviews", "submitReview", "case", "SubmitReview", "Review", review, 201, True)
    mutate("/v1/questions/{question_id}/revisions", "reviseQuestion", "question", "ReviseQuestion", "CapturedQuestionRevision", revise_question, 201, True)
    mutate("/v1/attempts/{attempt_id}/recognition-revisions", "reviseAnswer", "attempt", "ReviseAnswer", "Attempt", revise_answer, 201, True)
    mutate("/v1/cases/{case_id}/progress", "setCorrectionProgress", "case", "SetProgress", "CorrectionCase", progress)
    mutate("/v1/cases/{case_id}/help-requests", "requestHelp", "case", "CreateHelp", "HelpRequest", create_help, 201)
    mutate("/v1/help-requests/{help_id}/claim", "claimHelp", "help", "ClaimHelp", "HelpRequest", claim_help, staff=True)
    mutate("/v1/help-requests/{help_id}/complete", "completeHelp", "help", "CompleteHelp", "HelpRequest", complete_help, staff=True)
    for args in [
        ("/v1/assets/{asset_id}", "getImageAsset", "asset", "ImageAsset", False),
        ("/v1/jobs/{job_id}", "getJob", "job", "Job", False),
        ("/v1/questions/{question_id}", "getQuestion", "question", "QuestionRead", True),
        ("/v1/attempts/{attempt_id}", "getAttempt", "attempt", "AttemptRead", True),
        ("/v1/cases/{case_id}", "getCorrectionCase", "case", "CorrectionCase", False),
        ("/v1/checks/{check_id}", "getCheck", "check", "CheckRun", False),
        ("/v1/decisions/{decision_id}", "getDecision", "decision", "DecisionRead", True),
        ("/v1/hints/{hint_id}", "getHint", "hint", "Hint", False),
    ]:
        read(*args)
