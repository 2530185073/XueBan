"""Single-host durable worker. PostgreSQL supports multiple independent workers.

Claim/finish transactions are short. External inference runs outside a transaction.
Lease tokens fence stale workers; recovery may repeat a billed call, never silently
marks it free, and never duplicates the corresponding business records.
"""
import argparse
import base64
import sys
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
import time
from threading import Event, Thread

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from .asset_store import LocalAssetStore
from .config import Settings
from .contracts import Contracts
from .correction_api import enqueue_check_task
from .correction_core import (assets_for, create_task, event, get_entity, input_versions, invalidate, iso,
    new_id, now, save_entity, set_task, update_case)
from .correction_models import Asset, Entity, Task
from .db import create_database_engine
from .errors import Problem
from .learning_models import CurriculumGraph, KnowledgeNode, StudentLearningProfile
from .curriculum_catalog import (canonical_subject, graph_is_usable, node_matches_scope,
                                 scope_for_profile, scope_payload, curriculum_matches_scope)
from .layout_repair import apply_repair, recognize_replacements
from .models import Homework, Student
from .student_locks import lock_task_student, task_scope_current
from .providers import ProviderError
from . import recognition_recheck
from .providers import question_units
from .providers.correction_pipeline import CorrectionPipeline, PipelineImage, normalize_field_for_comparison


def verification_summary(value):
    """Expose only bounded provenance; raw reads and provider audit stay private."""
    if not isinstance(value, dict):
        return None
    return {
        "mode": value.get("mode") if value.get("mode") in {"single", "multimodel", "two_model"} else "single",
        "status": value.get("status") if value.get("status") in {"matched", "reviewed", "conflict", "degraded"} else "conflict",
        "independent_readback": value.get("independent_readback") is True,
        "reason_codes": [code[:128] for code in value.get("reason_codes", []) if isinstance(code, str)][:20],
    }


class CorrectionWorker:
    def __init__(self, engine, settings, pipeline):
        self.engine = engine
        self.settings = settings
        self.pipeline = pipeline
        self.contracts = Contracts(settings.contracts_dir)
        self.store = LocalAssetStore(settings.asset_storage_dir)

    def claim(self):
        timestamp = now()
        eligible = or_(and_(Task.status.in_(["queued", "retryable_failed"]), Task.available_at <= timestamp), and_(Task.status == "running", Task.lease_expires_at <= timestamp))
        with Session(self.engine, expire_on_commit=False) as session:
            task = session.scalar(select(Task).where(eligible).order_by(Task.available_at, Task.id).with_for_update(skip_locked=True).limit(1))
            if task is None:
                return None
            if task.kind == recognition_recheck.KIND and (task.audit or {}).get("recheck_dispatched"):
                set_task(session, self.contracts, task, "failed", failure={"code": "worker_failure", "message": "上次原图核对执行已中断，调用结果未知；不会自动再次调用。", "retryable": False})
                recognition_recheck.record_outcome(task, "execution_unknown")
                task.lease_token = None
                task.lease_expires_at = None
                session.commit()
                return None
            if task.tries >= 3:
                set_task(session, self.contracts, task, "failed", failure={"code": "worker_failure", "message": "任务中断次数已达到上限，请老师核实后重新提交。", "retryable": False})
                task.lease_token = None
                task.lease_expires_at = None
                session.commit()
                return None
            # The conditional update is also a claim fence in local SQLite tests.
            token = new_id("lease")
            result = session.execute(update(Task).where(Task.id == task.id, Task.revision == task.revision, eligible).values(lease_token=token, lease_expires_at=timestamp + timedelta(seconds=max(120, self.settings.worker_lease_seconds))).execution_options(synchronize_session=False))
            if result.rowcount != 1:
                session.rollback()
                return None
            session.refresh(task)
            task.tries += 1
            set_task(session, self.contracts, task, "running")
            ledger = deepcopy(task.audit or {"executions": []})
            ledger["executions"].append({"number": task.tries, "started_at": iso(timestamp), "charge_status": "unknown", "outcome": "started", **({"lease_digest": recognition_recheck.lease_digest(token)} if task.kind == recognition_recheck.KIND else {})})
            task.audit = ledger
            session.commit()
            return task.id, token

    def _load(self, identifier, token):
        with Session(self.engine, expire_on_commit=False) as session:
            task = session.get(Task, identifier)
            if task is None or task.lease_token != token or task.status != "running":
                return None
            homework = session.get(Homework, task.homework_id)
            student = session.get(Student, homework.student_id)
            if homework.processing_blocked or student is None or not student.active:
                raise Problem(409, "conflict", "processing_blocked", "处理已停止。")
            assets = assets_for(session, homework.id, task.inputs["asset_ids"])
            if task.kind == "repair_layout":
                return task.kind, {"assets": assets, "replacements": task.inputs["replacements"]}
            images = tuple(PipelineImage(a.id, "data:" + a.document["mime_type"] + ";base64," + base64.b64encode(self.store.read(a.id, a.document)).decode(), a.document["width"], a.document["height"]) for a in assets)
            context = {"images": images}
            if task.kind == "recognize_attempt" and getattr(self.pipeline, "supports_target_unit", False):
                original = get_entity(session, task.inputs["question_id"], "question")
                context["target_unit"] = question_units.stored_unit(original.annotations)
            if task.kind == recognition_recheck.KIND:
                case = get_entity(session, task.inputs["case_id"], "case")
                question = get_entity(session, task.inputs["question_id"], "question")
                attempt = get_entity(session, task.inputs["attempt_id"], "attempt")
                if not recognition_recheck.current(session, task, case, question, attempt):
                    raise Problem(409, "conflict", "stale_input", "输入版本已失效。")
                context.update(question=recognition_recheck.target_document(question),
                               attempt=deepcopy(attempt.document), input_versions=deepcopy(task.inputs["input_versions"]))
            if task.kind in {"check", "hint"}:
                case = get_entity(session, task.inputs["case_id"], "case")
                versions = task.inputs["input_versions"]
                question = get_entity(session, versions["question_id"], "question")
                attempt = get_entity(session, versions["attempt_id"], "attempt")
                if not self._current(session, task, case, question, attempt):
                    raise Problem(409, "conflict", "stale_input", "输入版本已失效。")
                attempt_input = deepcopy(attempt.document)
                if attempt.annotations and "answer_status" in attempt.annotations:
                    attempt_input["answer_status"] = attempt.annotations["answer_status"]
                if (attempt.annotations and attempt.annotations.get("slots_recognition_revision") == attempt.document["recognition_revision"]
                        and attempt.annotations.get("slots_question_revision") == question.revision):
                    attempt_input["answer_slots"] = deepcopy(attempt.annotations.get("answer_slots", []))
                question_input = deepcopy(question.document)
                if getattr(self.pipeline, "supports_target_unit", False):
                    annotations = question.annotations or {}
                    question_input["target_unit"] = question_units.stored_unit(annotations)
                    for key in ("target_context", "unit_complete"):
                        if key in annotations:
                            question_input[key] = deepcopy(annotations[key])
                if question.annotations and question.annotations.get("subject_override"):
                    question_input["subject"] = question.annotations["subject_override"]
                if question.annotations and question.annotations.get("label"):
                    question_input["label"] = question.annotations["label"]
                if question.annotations and question.annotations.get("diagram") and question.annotations.get("diagram_revision") == question.revision:
                    question_input["diagram"] = deepcopy(question.annotations["diagram"])
                if question.annotations and question.annotations.get("diagram_revision") == question.revision:
                    question_input["has_diagram"] = bool(question.annotations.get("has_diagram"))
                context.update(question=question_input, attempt=attempt_input, input_versions=deepcopy(versions))
                if task.kind == "check":
                    context.update(self._student_learning_context(session, homework, student, question))
                if task.kind == "hint":
                    context.update(decision=deepcopy(case.document["current_decision"]), level=task.inputs["level"])
            return task.kind, context

    @staticmethod
    def _student_learning_context(session, homework, student, question=None):
        """Return the approved textbook scope even when no reviewed graph can supply tags."""
        profile = session.get(StudentLearningProfile, student.id)
        grade = (profile.document.get("grade") if profile else None)
        if type(grade) is not int or grade not in (5, 6, 7, 8):
            grade = None
        context = {"student_grade": grade, "knowledge_point_candidates": [],
                   "curriculum_context": {"scope": None, "status": "unsupported_scope",
                                          "revision_policy": "publisher_latest"}}
        if grade is None:
            return context
        subject = canonical_subject(((question.annotations or {}).get("subject_override")
                                     or question.document.get("subject")) if question is not None else None)
        if subject is None:
            context["curriculum_context"]["status"] = "unresolved_subject"
            return context
        scoped = scope_for_profile(profile.document if profile else {}, subject)
        if scoped is None:
            return context
        curriculum = homework.curriculum if isinstance(homework.curriculum, dict) else {}
        if ((curriculum.get("subject") is not None
             and canonical_subject(curriculum["subject"]) != subject)
                or (curriculum.get("publisher_code") is not None
                    and curriculum["publisher_code"] != scoped.publisher_code)):
            return context
        scope = scope_payload(scoped)
        context["curriculum_context"].update(scope=scope, status="unverified_edition", revision_policy=scoped.revision_policy)
        if (not curriculum_matches_scope(curriculum, scoped)
                or scope["catalog_status"] != "available"):
            return context
        context["curriculum_context"]["status"] = "available"
        from .learning_projection import published_graph
        graph = published_graph(session, student.id)
        if graph is None or not graph_is_usable(graph):
            return context
        nodes = session.scalars(select(KnowledgeNode).where(
            KnowledgeNode.graph_version == graph.version,
            KnowledgeNode.node_type.in_(("concept", "skill")),
        ).order_by(KnowledgeNode.sort_order))
        context["knowledge_point_candidates"] = [node.name for node in nodes if node_matches_scope(node, scoped)]
        return context

    def _current(self, session, task, case, question, attempt):
        versions = task.inputs["input_versions"]
        if task.inputs.get("recovery_epoch", 0) != recognition_recheck.epoch(case):
            return False
        if case.document["lifecycle"] != "active" or case.document["current_attempt_id"] != attempt.id or question.revision != versions["question_revision"] or attempt.document["recognition_revision"] != versions["attempt_recognition_revision"]:
            return False
        assets = assets_for(session, task.homework_id, task.inputs["asset_ids"])
        if input_versions(case, question, attempt, assets)["fingerprint"] != versions["fingerprint"]:
            return False
        current = case.document["current_decision"]
        if task.kind == "hint":
            return current is not None and current["id"] == task.inputs["source_decision_id"] and current["revision"] == task.inputs["source_decision_revision"] and current["validity"] in {"candidate", "needs_review", "confirmed"}
        return (current or {}).get("id") == task.inputs.get("decision_id_at_submission") and case.document["check_ids"][-1] == task.inputs["check_id"]

    def renew_lease(self, identifier, token):
        """Extend only a live lease owned by this execution, never revive an old one."""
        timestamp = now()
        with Session(self.engine) as session:
            updated = session.execute(update(Task).where(
                Task.id == identifier, Task.status == "running", Task.lease_token == token,
                Task.lease_expires_at > timestamp,
            ).values(lease_expires_at=timestamp + timedelta(seconds=max(120, self.settings.worker_lease_seconds))))
            session.commit()
            return updated.rowcount == 1

    @contextmanager
    def _keep_lease(self, identifier, token):
        # A page may now require multiple bounded provider calls. Renew in a
        # separate short transaction while inference runs, retaining token fencing.
        stopped = Event()
        interval = min(30, max(120, self.settings.worker_lease_seconds) / 3)

        def heartbeat():
            while not stopped.wait(interval):
                try:
                    if not self.renew_lease(identifier, token):
                        return
                except Exception:
                    # A transient DB failure can recover next tick; finish still
                    # refuses an expired lease. Never log connection details.
                    continue

        thread = Thread(target=heartbeat, daemon=True, name="correction-lease")
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join(timeout=1)

    def run_once(self):
        claim = self.claim()
        if claim is None:
            return False
        identifier, token = claim
        result = None
        try:
            loaded = self._load(identifier, token)
            if loaded is None:
                return True
            kind, context = loaded
            with self._keep_lease(identifier, token):
                if kind in {"recognize_homework", "recognize_attempt"}:
                    result = self.pipeline.recognize(**context)
                elif kind == recognition_recheck.KIND:
                    method = getattr(self.pipeline, "rerecognize_original", None)
                    if method is None:
                        raise ProviderError("unsupported_original_recheck")
                    if not self._dispatch_original_recheck(identifier, token):
                        raise Problem(409, "conflict", "stale_input", "原图核对已派发或输入失效。")
                    result = method(**context)
                elif kind == "check":
                    method = self.pipeline.recheck if context["attempt"]["parent_attempt_id"] is not None else self.pipeline.check
                    result = method(**context)
                elif kind == "hint":
                    result = self.pipeline.hint(**context)
                elif kind == "repair_layout":
                    result = recognize_replacements(self.pipeline, self.store, context["assets"], context["replacements"])
                else:
                    raise ProviderError("invalid_input")
            # Stop heartbeat writes before committing the result transaction.
            self.finish(identifier, token, result=result)
        except ProviderError as error:
            self.finish(identifier, token, error=error, result=result)
        except Problem as error:
            failure = ProviderError("invalid_output") if result is not None and error.code == "service_unavailable" else error
            self.finish(identifier, token, error=failure, result=result)
        except Exception:
            # Never log an exception body: upstream errors can include student data.
            self.finish(identifier, token, error=ProviderError("worker_failure"), result=result)
        return True

    def _dispatch_original_recheck(self, identifier, token):
        """Reserve the one external execution durably before making any call."""
        with Session(self.engine) as session:
            scope = lock_task_student(session, identifier)
            task = session.scalar(select(Task).where(Task.id == identifier).with_for_update()
                                  .execution_options(populate_existing=True))
            if task is None or task.kind != recognition_recheck.KIND or task.status != "running" or task.lease_token != token:
                return False
            homework = session.scalar(select(Homework).where(Homework.id == task.homework_id).with_for_update()
                                      .execution_options(populate_existing=True))
            if not task_scope_current(scope, homework) or homework.processing_blocked or not scope["student"].active:
                return False
            case = get_entity(session, task.inputs["case_id"], "case")
            question = get_entity(session, task.inputs["question_id"], "question")
            attempt = get_entity(session, task.inputs["attempt_id"], "attempt")
            if (task.audit or {}).get("recheck_dispatched") or not recognition_recheck.current(session, task, case, question, attempt):
                return False
            ledger = deepcopy(task.audit or {})
            ledger["recheck_dispatched"] = True
            ledger["dispatched_at"] = iso()
            ledger["executions"][-1]["dispatched"] = True
            # Conditional update fences SQLite, whose FOR UPDATE is a no-op.
            changed = session.execute(update(Task).where(Task.id == task.id, Task.revision == task.revision,
                Task.lease_token == token, Task.status == "running", Task.lease_expires_at > now())
                .values(audit=ledger, revision=task.revision + 1,
                        document={**task.document, "revision": task.revision + 1, "updated_at": iso()})
                .execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                return False
            session.commit()
            return True

    def _late_original_recheck(self, session, task, token, result, error):
        """A timed-out owner may report billing evidence, never business records."""
        if task is None or task.kind != recognition_recheck.KIND:
            return False
        ledger = deepcopy(task.audit or {})
        execution = next((row for row in ledger.get("executions", [])
                          if row.get("lease_digest") == recognition_recheck.lease_digest(token)), None)
        if (execution is None or "late_return" in execution or not execution.get("dispatched")
                or task.status in {"succeeded", "superseded", "cancelled"}
                or task.status == "failed" and ledger.get("recovery_outcome") != "execution_unknown"):
            return False
        evidence = {"recorded_at": iso(), "applied": False}
        if result is not None:
            evidence.update(metadata=asdict(result.metadata), prompt_version=result.prompt_version,
                            candidate=deepcopy(result.data))
        elif isinstance(error, ProviderError):
            evidence["attempts"] = [asdict(row) for row in error.attempts]
        execution["late_return"] = evidence
        ledger["recovery_outcome"] = "execution_unknown"
        task.audit = ledger
        if task.status == "running":
            set_task(session, self.contracts, task, "failed", failure={"code": "worker_failure",
                "message": "原图核对已超过执行租期，迟到结果仅保留审计，不覆盖当前记录。", "retryable": False})
            task.lease_token = None
            task.lease_expires_at = None
        session.commit()
        return False

    def finish(self, identifier, token, *, result=None, error=None):
        with Session(self.engine) as session:
            scope = lock_task_student(session, identifier)
            task = session.scalar(select(Task).where(Task.id == identifier).with_for_update()
                                  .execution_options(populate_existing=True))
            if task is None or task.status != "running" or task.lease_token != token:
                return self._late_original_recheck(session, task, token, result, error)
            # Expired executions cannot commit even if another worker has not yet claimed.
            from datetime import timezone
            if task.lease_expires_at.replace(tzinfo=task.lease_expires_at.tzinfo or timezone.utc) <= now():
                return self._late_original_recheck(session, task, token, result, error)
            homework = session.scalar(select(Homework).where(Homework.id == task.homework_id).with_for_update()
                                      .execution_options(populate_existing=True))
            if not task_scope_current(scope, homework) or homework.processing_blocked or not scope["student"].active:
                error = Problem(409, "conflict", "processing_blocked", "处理已停止。")
            ledger = deepcopy(task.audit or {"executions": []})
            execution = ledger["executions"][-1]
            if result is not None:
                if result.status != "candidate":
                    error = ProviderError("invalid_output")
                execution.update(outcome="returned", finished_at=iso(), metadata=asdict(result.metadata), prompt_version=result.prompt_version)
                execution["candidate"] = deepcopy(result.data)
            if error is None:
                reference = self._apply(session, task, homework, result)
                if reference is None:
                    set_task(session, self.contracts, task, "superseded")
                    execution["outcome"] = "superseded"
                else:
                    set_task(session, self.contracts, task, "succeeded", result=reference)
                    execution["outcome"] = "succeeded"
            elif isinstance(error, Problem) and error.status == 409:
                set_task(session, self.contracts, task, "superseded")
                execution.update(outcome="superseded", finished_at=iso())
            else:
                code = getattr(error, "code", "worker_failure")
                # Safe fixed codes only; external exception strings never enter storage.
                if code in {"timeout", "total_timeout", "upstream_timeout", "deadline_exceeded"}:
                    failure_code = "provider_timeout"
                elif code in {"invalid_output", "invalid_input", "invalid_json", "schema_mismatch", "incomplete_output", "response_too_large", "provider_credential_echo"}:
                    failure_code = "invalid_provider_output"
                elif code in {"storage_error", "asset_integrity_mismatch", "invalid_image"}:
                    failure_code = "storage_error"
                elif code == "worker_failure":
                    failure_code = "worker_failure"
                else:
                    failure_code = "provider_unavailable"
                # Inference timeouts can already have been billed. They are surfaced
                # for an explicit retry, not automatically retried indefinitely.
                retryable = task.kind != recognition_recheck.KIND and code in {"upstream_rate_limited", "upstream_unavailable", "concurrency_limit", "storage_error"} and task.tries < 3
                set_task(session, self.contracts, task, "retryable_failed" if retryable else "failed", failure={"code": failure_code, "message": "处理未完成，未产生有效判断；请重试或请坐班老师核实。", "retryable": retryable})
                task.available_at = now() + timedelta(seconds=2 * task.tries)
                execution.update(outcome="failed", finished_at=iso(), failure_code=failure_code)
                if isinstance(error, ProviderError):
                    execution["attempts"] = [asdict(attempt) for attempt in error.attempts]
                if task.kind == "recognize_attempt" and not retryable:
                    attempt = get_entity(session, task.inputs["attempt_id"], "attempt")
                    if attempt.document["recognition_status"] == "pending":
                        doc = {**attempt.document, "revision": attempt.revision + 1, "recognition_status": "failed"}
                        save_entity(session, self.contracts, homework.id, "attempt", doc, row=attempt)
                if task.kind == "check" and not retryable:
                    case = get_entity(session, task.inputs["case_id"], "case")
                    if case.document["check_ids"][-1] == task.inputs["check_id"] and case.document["progress"] == "rechecking":
                        previous = case.document["current_decision"]
                        update_case(session, self.contracts, case, progress="needs_correction" if previous and previous["verdict"] != "correct" else "pending")
            if task.kind == recognition_recheck.KIND:
                ledger.update({key: value for key, value in (task.audit or {}).items() if key != "executions"})
                if task.status == "failed":
                    ledger["recovery_outcome"] = "service_failed"
                elif task.status == "superseded":
                    ledger["recovery_outcome"] = "stale_input"
            task.audit = ledger
            task.lease_token = None
            task.lease_expires_at = None
            session.commit()
            return True

    def _apply(self, session, task, homework, result):
        if task.kind == recognition_recheck.KIND:
            return recognition_recheck.apply_result(self, session, task, homework, result)
        if task.kind == "repair_layout":
            return apply_repair(session, self.contracts, task, homework, result)
        if task.kind == "recognize_homework":
            for index, item in enumerate(result.data["items"]):
                question, attempt, case = self._new_question(session, homework, task, {**item, "reading_order": index})
                self._apply_page_assessment(session, task, homework, result, item, question, attempt, case)
            coverage = deepcopy(result.data["coverage"])
            if not result.data["items"]:
                coverage["collection_scope"] = "unknown"
                coverage["note"] = "这张照片没有读出可核对的题目，可以重新识别、重新拍摄清楚，或请坐班老师核实。"
            self.contracts.validate("Coverage", coverage)
            homework.coverage_json = coverage
            homework.revision += 1
            if self.settings.auto_check_after_recognition:
                # Recognition and the complete per-question check set share
                # this finish transaction. The shared helper is idempotent,
                # so retries and a concurrent client cannot create duplicate
                # current checks.
                cases = session.scalars(select(Entity).where(
                    Entity.homework_id == homework.id,
                    Entity.kind == "case",
                )).all()
                for case in cases:
                    if case.document["lifecycle"] != "active":
                        continue
                    # Whole-page assessment already completed this exact
                    # attempt, including unresolved results. Retrying it must
                    # be explicit, not another billed per-question pass.
                    if case.document.get("current_decision") is not None:
                        continue
                    question = get_entity(session, case.document["question_id"], "question")
                    attempt = get_entity(session, case.document["current_attempt_id"], "attempt")
                    enqueue_check_task(session, self.contracts, homework, case, question, attempt)
            return {"resource_type": "homework", "resource_id": homework.id}
        if task.kind == "recognize_attempt":
            attempt = get_entity(session, task.inputs["attempt_id"], "attempt")
            case = get_entity(session, task.inputs["case_id"], "case")
            if case.document["lifecycle"] != "active" or case.document["current_attempt_id"] != attempt.id or case.document["current_question_revision"] != task.inputs["question_revision"] or attempt.document["recognition_status"] != "pending":
                return None
            items = result.data["items"]
            question = get_entity(session, task.inputs["question_id"], "question")
            if question.revision != task.inputs["question_revision"]:
                return None
            def normalized_stem(value):
                return normalize_field_for_comparison(value, "stem_text") or ""
            def normalized_label(value):
                # Use the same typography-only label normalization as the
                # independent page readers; blank indices remain distinct.
                from .providers.whole_page import canonical_label
                return canonical_label(value or "")
            original_label = (question.annotations or {}).get("label")
            active_questions = [row for row in session.scalars(select(Entity).where(Entity.homework_id == homework.id, Entity.kind == "question")) if row.document["lifecycle"] == "active"]
            def question_item(row):
                return {"stem_text": row.document["stem_text"], "label": (row.annotations or {}).get("label") or "",
                        "question_type": row.document.get("question_type", "unknown"),
                        "options": row.document.get("options", [])}
            # A whole-page correction can contain unrelated questions. Bind only a
            # unique full-stem match; shared stems additionally need the original
            # subquestion label. Neither array position nor a label alone is proof.
            matching = []
            for item in items:
                if not question_units.compatible_saved_target(
                        question.document, question.annotations, item, normalized_label):
                    continue
                if isinstance(item.get("assessment"), dict):
                    # Multimodel readers can include the printed item label in
                    # the stem. Strip only that label's decoration, then require
                    # complete equality; substring overlap is not target proof.
                    from .providers.whole_page import normalize_page_stem
                    normalize = normalize_page_stem
                else:
                    # Preserve the legacy recognizer's stricter matching rules.
                    normalize = lambda value: normalized_stem(value["stem_text"])
                target = normalize(question_item(question))
                if not target or normalize(item) != target:
                    continue
                shared_stem = sum(normalize(question_item(row)) == target for row in active_questions) > 1
                if shared_stem and not (original_label and normalized_label(item["label"]) == normalized_label(original_label)):
                    continue
                matching.append(item)
            if len(matching) > 1:
                matching = [item for item in matching if original_label and normalized_label(item["label"]) == normalized_label(original_label)]
            item = matching[0] if len(matching) == 1 else None
            same_question = item is not None
            ready = same_question and item.get("recognition_status") == "ready"
            # A single visible problem may still be the wrong problem. Keep its
            # candidate in audit, but do not silently bind its answer to this case.
            doc = {**attempt.document, "revision": attempt.revision + 1, "recognition_revision": attempt.document["recognition_revision"] + 1, "answer_text": item["answer_text"] if same_question else None, "answer_regions": item["answer_regions"] if same_question else [], "answer_status": item["answer_status"] if same_question else "unclear", "recognition_uncertainties": list(item.get("uncertainties", [])) if same_question else ["订正照片与目标题目未能可靠对应。"], "recognition_status": "ready" if ready else "needs_revision"}
            save_entity(session, self.contracts, homework.id, "attempt", doc, row=attempt)
            attempt.annotations = {"answer_status": item["answer_status"] if same_question else "unclear", "target_binding": "matched_stem" if same_question else "needs_staff"}
            if same_question:
                attempt.annotations.update(answer_slots=deepcopy(item.get("answer_slots", [])),
                                           slots_recognition_revision=doc["recognition_revision"], slots_question_revision=question.revision)
            update_case(session, self.contracts, case, progress="paused" if case.document["progress"] == "paused" else "pending")
            if same_question:
                self._apply_page_assessment(session, task, homework, result, item, question, attempt, case)
            return {"resource_type": "attempt", "resource_id": attempt.id}
        case = get_entity(session, task.inputs["case_id"], "case")
        versions = task.inputs["input_versions"]
        question = get_entity(session, versions["question_id"], "question")
        attempt = get_entity(session, versions["attempt_id"], "attempt")
        if not self._current(session, task, case, question, attempt):
            return None
        if task.kind == "check":
            check = get_entity(session, task.inputs["check_id"], "check")
            candidate = result.data
            doc = {key: candidate[key] for key in ("verdict", "reason_code", "basis", "first_error_region")}
            doc.update(knowledge_points=list(candidate.get("knowledge_points", [])),
                       correction_method=candidate.get("correction_method"),
                       correct_answer=candidate.get("correct_answer"),
                       uncertainties=list(candidate.get("uncertainties", [])))
            verification = verification_summary(candidate.get("verification"))
            if verification is not None:
                doc["verification"] = verification
            answer_text = attempt.document.get("answer_text")
            recognition_status = attempt.document.get("recognition_status")
            answer_text_present = (
                isinstance(answer_text, str)
                and bool(normalize_field_for_comparison(answer_text, "answer_text"))
            )
            needs_revision_verified = (
                recognition_status == "needs_revision"
                and candidate.get("evidence_verified") is True
                and answer_text_present
                and bool(normalize_field_for_comparison(question.document.get("stem_text"), "stem_text"))
                and (attempt.annotations or {}).get("target_binding") != "needs_staff"
            )
            # A ready attempt keeps the historical behavior, including blank
            # answers. A needs_revision attempt must pass the explicit model
            # evidence review before its verdict can be used.
            usable_answer = recognition_status == "ready" or needs_revision_verified
            if candidate.get("answer_checks") and usable_answer:
                doc["answer_checks"] = deepcopy(candidate["answer_checks"])
            if not usable_answer:
                doc.update(verdict="unable_to_judge", reason_code="recognition_conflict", correct_answer=None, first_error_region=None, basis=[{"kind": "model_hypothesis", "summary": "本次作答识别或目标题对应尚待核实，不能据此判断对错。", "source_ref": None, "regions": []}])
            unresolved = list(dict.fromkeys([
                *attempt.document.get("recognition_uncertainties", []),
                *doc["uncertainties"],
            ]))[:20]
            if unresolved or (verification is not None and (
                    verification["status"] not in {"matched", "reviewed"}
                    or verification["reason_codes"]
                    or not verification["independent_readback"])):
                doc.update(verdict="unable_to_judge",
                           reason_code="verification_conflict" if usable_answer else "recognition_conflict",
                           uncertainties=unresolved or ["独立核对尚未完成，请核对原图后重试。"])
            if doc["verdict"] == "unable_to_judge":
                doc.update(correct_answer=None, correction_method=None, knowledge_points=[])
                doc.pop("answer_checks", None)
            doc.update(id=new_id("decision"), revision=1, case_id=case.id, check_id=check.id, input_versions=versions, origin="model", validity="needs_review" if doc["verdict"] == "unable_to_judge" else "candidate", confirmation_source=None, confirmed_by=None, confirmed_at=None, replaces_decision_id=(case.document["current_decision"] or {}).get("id"), invalidated_by_event_id=None, created_at=iso())
            save_entity(session, self.contracts, homework.id, "decision", doc)
            cdoc = {**check.document, "revision": check.revision + 1, "model_version": result.metadata.model or result.metadata.requested_model, "prompt_version": result.prompt_version, "decision_ids": [doc["id"]]}
            save_entity(session, self.contracts, homework.id, "check", cdoc, row=check)
            if case.document["current_decision"] is not None:
                invalidate(session, self.contracts, case, "new_check_decision")
            # Even 'correct' remains pending until an authorized person verifies it.
            next_progress = "paused" if case.document["progress"] == "paused" else ("pending" if doc["verdict"] in {"correct", "unable_to_judge"} else "needs_correction")
            update_case(session, self.contracts, case, current_decision=doc, progress=next_progress)
            # Usable AI observations retain their model origin when mapped.
            # If the returned knowledge-point names match the published graph,
            # create the model link and rebuild that student's map in this
            # same transaction. A later teacher review can supersede it.
            if doc["verdict"] in ("incorrect", "partially_correct") and doc["knowledge_points"]:
                from .learning_mapping import auto_confirm_model_links
                from .learning_projection import project_student
                if auto_confirm_model_links(session, homework.tenant_id, homework.student_id):
                    project_student(session, homework.tenant_id, homework.student_id)
            return {"resource_type": "check", "resource_id": check.id}
        if task.kind == "hint":
            data = result.data
            doc = {"id": new_id("hint"), "revision": 1, "case_id": case.id, "attempt_id": attempt.id, "source_decision_id": task.inputs["source_decision_id"], "source_decision_revision": task.inputs["source_decision_revision"], "level": data["level"], "content": data["content"], "exposes_answer": data["exposes_answer"], "source_is_provisional": data["source_is_provisional"], "validity": "active", "first_shown_at": None, "created_at": iso()}
            save_entity(session, self.contracts, homework.id, "hint", doc)
            update_case(session, self.contracts, case, hint_ids=case.document["hint_ids"] + [doc["id"]])
            return {"resource_type": "hint", "resource_id": doc["id"]}
        raise ProviderError("invalid_input")

    def _new_question(self, session, homework, task, item):
        question_id, case_id, attempt_id = new_id("question"), new_id("case"), new_id("attempt")
        timestamp = iso()
        question = {"question_id": question_id, "revision": 1, "homework_id": homework.id, "label": item["label"], "stem_text": item["stem_text"], "question_type": item.get("question_type", "unknown"), "subject": canonical_subject(item.get("subject")), "options": list(item.get("options", [])), "blank_count": item.get("blank_count", 0), "recognition_uncertainties": list(item.get("uncertainties", [])), "regions": item["regions"], "source": "recognizer", "source_actor_id": None, "bank_question_id": None, "knowledge_point_ids": None, "created_at": timestamp, "lifecycle": "active", "replacement_question_ids": []}
        question_row = save_entity(session, self.contracts, homework.id, "question", question)
        question_row.annotations = {"label": item["label"], "diagram": item.get("diagram"), "diagram_revision": 1,
                                    "has_diagram": item.get("has_diagram", bool(item.get("diagram"))),
                                    "reading_order": item.get("reading_order"),
                                    **question_units.annotation_fields(item)}
        ids = list(dict.fromkeys([r["asset_id"] for r in item["regions"]] + [r["asset_id"] for r in item["answer_regions"]]))
        attempt = {"id": attempt_id, "revision": 1, "case_id": case_id, "question_id": question_id, "captured_question_revision": 1, "parent_attempt_id": None, "root_attempt_id": attempt_id, "asset_ids": ids, "recognition_revision": 1, "answer_text": item["answer_text"], "answer_regions": item["answer_regions"], "answer_status": item["answer_status"], "recognition_uncertainties": list(item.get("uncertainties", [])), "recognition_status": item.get("recognition_status", "needs_revision"), "help_condition": "unknown", "occurred_at": iso(homework.occurred_at), "created_at": timestamp}
        attempt_row = save_entity(session, self.contracts, homework.id, "attempt", attempt)
        attempt_row.annotations = {"answer_status": item["answer_status"], "answer_slots": deepcopy(item.get("answer_slots", [])),
                                   "slots_recognition_revision": 1, "slots_question_revision": 1}
        case = {"id": case_id, "revision": 1, "scope": {"tenant_id": homework.tenant_id, "campus_id": homework.campus_id, "student_id": homework.student_id}, "homework_id": homework.id, "question_id": question_id, "current_question_revision": 1, "root_attempt_id": attempt_id, "current_attempt_id": attempt_id, "progress": "pending", "current_decision": None, "attempt_ids": [attempt_id], "check_ids": [], "hint_ids": [], "help_request_ids": [], "review_ids": [], "created_at": timestamp, "updated_at": timestamp, "lifecycle": "active", "replacement_case_ids": []}
        case_row = save_entity(session, self.contracts, homework.id, "case", case)
        return question_row, attempt_row, case_row

    def _apply_page_assessment(self, session, task, homework, result, item, question, attempt, case):
        """Persist a page decision without an additional billed per-question job.

        Page inference remains an AI candidate. Private verification details stay
        in annotations and the task execution ledger, never masquerading as a
        staff review or granting learning mastery. Legacy recognizers can omit
        assessment and continue through the explicit check/recheck path.
        """
        assessment = item.get("assessment")
        if assessment is None:
            return
        if not isinstance(assessment, dict):
            raise ProviderError("invalid_output")
        verification = assessment.get("verification") or {}
        verdict = assessment.get("verdict", "unable_to_judge")
        reason = assessment.get("reason_code")
        summary = assessment.get("basis_summary") or "整页模型判断尚待核实。"
        reasons = {"unclear_image", "incomplete_question", "unsupported_type", "recognition_conflict", "verification_conflict", "insufficient_process", "needs_staff", "other"}
        if reason not in reasons:
            reason = None
        if verdict not in {"correct", "incorrect", "partially_correct", "unanswered", "unable_to_judge"}:
            verdict, reason = "unable_to_judge", "verification_conflict"
        if "target_scope_unverified" in verification.get("reason_codes", []):
            verdict, reason = "unable_to_judge", "verification_conflict"
            summary = "本题的作答范围尚未完整核对，原图和已有笔迹已保留，待AI继续核对。"
        elif any(
                code in {"provider_unavailable", "reconciliation_unavailable"}
                for code in verification.get("reason_codes", [])):
            verdict, reason = "unable_to_judge", "verification_conflict"
            summary = "有一路核对服务暂未完成，原图已保留，本题暂不判断对错。"
        elif attempt.document["recognition_status"] != "ready" or item.get("answer_status") == "unclear":
            verdict, reason = "unable_to_judge", "recognition_conflict"
            summary = "本次作答识读仍有疑问，请核对原图后再判断。"
        elif item.get("uncertainties") or assessment.get("uncertainties") or verification.get("reason_codes") or verification.get("status") not in {"matched", "reviewed"} or verification.get("independent_readback") is not True:
            verdict, reason = "unable_to_judge", "verification_conflict"
            summary = "模型对本题的独立核对尚未达成可靠一致，暂不判断对错。"
        if verdict == "unable_to_judge":
            reason = reason or "verification_conflict"
        # Include the original question source and the CURRENT answer assets in
        # the same fingerprint used by subsequent checks and staff reviews.
        ids = list(dict.fromkeys([r["asset_id"] for r in question.document["regions"]] + attempt.document["asset_ids"]))
        assets = assets_for(session, homework.id, ids)
        versions = input_versions(case, question, attempt, assets)
        check_id, decision_id = new_id("check"), new_id("decision")
        timestamp = iso()
        check = {"id": check_id, "revision": 1, "job_id": task.id, "case_id": case.id, "input_versions": versions,
                 "model_version": result.metadata.model or result.metadata.requested_model, "rule_version": None,
                 "prompt_version": result.prompt_version, "decision_ids": [decision_id], "created_at": timestamp}
        check_row = save_entity(session, self.contracts, homework.id, "check", check)
        trace = {"source": "whole_page_assessment", "recognition_job_id": task.id,
                 "recognition_asset_ids": list(task.inputs["asset_ids"]), "label": item["label"],
                 "assessment": deepcopy(assessment)}
        check_row.annotations = deepcopy(trace)
        decision = {"id": decision_id, "revision": 1, "case_id": case.id, "check_id": check_id,
                    "input_versions": versions, "origin": "model", "validity": "needs_review" if verdict == "unable_to_judge" else "candidate",
                    "verdict": verdict, "reason_code": reason,
                    "basis": [{"kind": "model_hypothesis", "summary": summary[:2000], "source_ref": task.id, "regions": []}],
                    "first_error_region": None, "confirmation_source": None, "confirmed_by": None, "confirmed_at": None,
                    "replaces_decision_id": (case.document["current_decision"] or {}).get("id"),
                    "invalidated_by_event_id": None, "created_at": timestamp}
        decision.update(
            verification=verification_summary(verification),
            uncertainties=list(dict.fromkeys([*item.get("uncertainties", []), *assessment.get("uncertainties", [])]))[:20],
            knowledge_points=list(assessment.get("knowledge_points", [])) if verdict != "unable_to_judge" else [],
            correct_answer=assessment.get("correct_answer") if verdict != "unable_to_judge" else None,
            correction_method=assessment.get("correction_method") if verdict != "unable_to_judge" else None,
        )
        if verdict == "unable_to_judge" and not decision["uncertainties"]:
            decision["uncertainties"] = [summary[:600]]
        if verdict != "unable_to_judge" and assessment.get("answer_checks"):
            decision["answer_checks"] = deepcopy(assessment["answer_checks"])
        decision_row = save_entity(session, self.contracts, homework.id, "decision", decision)
        decision_row.annotations = deepcopy(trace)
        if case.document["current_decision"] is not None:
            invalidate(session, self.contracts, case, "new_page_decision")
        progress = "paused" if case.document["progress"] == "paused" else ("pending" if verdict in {"correct", "unable_to_judge"} else "needs_correction")
        update_case(session, self.contracts, case, check_ids=case.document["check_ids"] + [check_id], current_decision=decision, progress=progress)
        if verdict in {"incorrect", "partially_correct"} and decision["knowledge_points"]:
            from .learning_mapping import auto_confirm_model_links
            from .learning_projection import project_student
            if auto_confirm_model_links(session, homework.tenant_id, homework.student_id):
                project_student(session, homework.tenant_id, homework.student_id)


def run_round(*runners) -> bool:
    """Give every executor one turn so a sustained correction backlog cannot starve learning jobs."""
    return any([runner() for runner in runners])


def main():
    parser = argparse.ArgumentParser(description="Run persistent photo correction jobs")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_environment()
    engine = create_database_engine(settings.database_url, allow_sqlite=settings.allow_sqlite_for_tests)
    try:
        from .governance import DeletionExecutor
        from .learning_jobs import LearningWorker
        deletions = DeletionExecutor(engine, settings)
        with CorrectionPipeline.from_env() as pipeline:
            worker = CorrectionWorker(engine, settings, pipeline)
            learning = LearningWorker(engine, client=pipeline.client)
            while True:
                worked = run_round(deletions.run_once, worker.run_once, learning.run_once)
                if args.once:
                    break
                if not worked:
                    time.sleep(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        # A final database failure can itself prevent writing the failure ledger.
        # Do not dump SQL parameters, configuration, or student data to a traceback.
        print("worker_stopped: service or configuration unavailable; leased tasks remain recoverable", file=sys.stderr)
        raise SystemExit(1) from None
