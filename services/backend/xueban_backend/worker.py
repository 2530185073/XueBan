"""Single-host durable worker. PostgreSQL supports multiple independent workers.

Claim/finish transactions are short. External inference runs outside a transaction.
Lease tokens fence stale workers; recovery may repeat a billed call, never silently
marks it free, and never duplicates the corresponding business records.
"""
import argparse
import base64
import re
import sys
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
import time
import unicodedata

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from .asset_store import LocalAssetStore
from .config import Settings
from .contracts import Contracts
from .correction_core import (assets_for, create_task, event, get_entity, input_versions, invalidate, iso,
    new_id, now, save_entity, set_task, update_case)
from .correction_models import Asset, Entity, Task
from .db import create_database_engine
from .errors import Problem
from .layout_repair import apply_repair, recognize_replacements
from .models import Homework, Student
from .providers import ProviderError
from .providers.correction_pipeline import CorrectionPipeline, PipelineImage, normalize_field_for_comparison


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
            ledger["executions"].append({"number": task.tries, "started_at": iso(timestamp), "charge_status": "unknown", "outcome": "started"})
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
                question_input = deepcopy(question.document)
                if question.annotations and question.annotations.get("label"):
                    question_input["label"] = question.annotations["label"]
                context.update(question=question_input, attempt=attempt_input, input_versions=deepcopy(versions))
                if task.kind == "hint":
                    context.update(decision=deepcopy(case.document["current_decision"]), level=task.inputs["level"])
            return task.kind, context

    def _current(self, session, task, case, question, attempt):
        versions = task.inputs["input_versions"]
        if case.document["lifecycle"] != "active" or case.document["current_attempt_id"] != attempt.id or question.revision != versions["question_revision"] or attempt.document["recognition_revision"] != versions["attempt_recognition_revision"]:
            return False
        assets = assets_for(session, task.homework_id, task.inputs["asset_ids"])
        if input_versions(case, question, attempt, assets)["fingerprint"] != versions["fingerprint"]:
            return False
        current = case.document["current_decision"]
        if task.kind == "hint":
            return current is not None and current["id"] == task.inputs["source_decision_id"] and current["revision"] == task.inputs["source_decision_revision"] and current["validity"] in {"candidate", "needs_review", "confirmed"}
        return (current or {}).get("id") == task.inputs.get("decision_id_at_submission") and case.document["check_ids"][-1] == task.inputs["check_id"]

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
            if kind in {"recognize_homework", "recognize_attempt"}:
                result = self.pipeline.recognize(**context)
            elif kind == "check":
                method = self.pipeline.recheck if context["attempt"]["parent_attempt_id"] is not None else self.pipeline.check
                result = method(**context)
            elif kind == "hint":
                result = self.pipeline.hint(**context)
            elif kind == "repair_layout":
                result = recognize_replacements(self.pipeline, self.store, context["assets"], context["replacements"])
            else:
                raise ProviderError("invalid_input")
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

    def finish(self, identifier, token, *, result=None, error=None):
        with Session(self.engine) as session:
            task = session.scalar(select(Task).where(Task.id == identifier).with_for_update())
            if task is None or task.status != "running" or task.lease_token != token:
                return False
            # Expired executions cannot commit even if another worker has not yet claimed.
            from datetime import timezone
            if task.lease_expires_at.replace(tzinfo=task.lease_expires_at.tzinfo or timezone.utc) <= now():
                return False
            homework = session.scalar(select(Homework).where(Homework.id == task.homework_id).with_for_update())
            student = session.get(Student, homework.student_id)
            if homework.processing_blocked or student is None or not student.active:
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
                retryable = code in {"upstream_rate_limited", "upstream_unavailable", "concurrency_limit", "storage_error"} and task.tries < 3
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
            task.audit = ledger
            task.lease_token = None
            task.lease_expires_at = None
            session.commit()
            return True

    def _apply(self, session, task, homework, result):
        if task.kind == "repair_layout":
            return apply_repair(session, self.contracts, task, homework, result)
        if task.kind == "recognize_homework":
            # Unknown-located items remain in private audit, never get invented boxes.
            located = [item for item in result.data["items"] if item["regions"] and item.get("location_status", "located") == "located"]
            for item in located:
                self._new_question(session, homework, task, item)
            coverage = deepcopy(result.data["coverage"])
            if len(located) != len(result.data["items"]) or not located:
                coverage["collection_scope"] = "unknown"
                coverage["note"] = "部分内容无法可靠定位或读取，请查看原图并重新拍摄或请坐班老师核实。"
            self.contracts.validate("Coverage", coverage)
            homework.coverage_json = coverage
            homework.revision += 1
            return {"resource_type": "homework", "resource_id": homework.id}
        if task.kind == "recognize_attempt":
            attempt = get_entity(session, task.inputs["attempt_id"], "attempt")
            case = get_entity(session, task.inputs["case_id"], "case")
            if case.document["lifecycle"] != "active" or case.document["current_attempt_id"] != attempt.id or case.document["current_question_revision"] != task.inputs["question_revision"] or attempt.document["recognition_status"] != "pending":
                return None
            # A new correction photo must isolate its target. Multiple items cannot
            # silently be attached to an unrelated parent attempt.
            items = result.data["items"]
            item = items[0] if len(items) == 1 else None
            question = get_entity(session, task.inputs["question_id"], "question")
            def normalized_stem(value):
                return normalize_field_for_comparison(value, "stem_text") or ""
            def normalized_label(value):
                return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value or "")).strip()
            target = normalized_stem(question.document["stem_text"])
            original_label = (question.annotations or {}).get("label")
            same_stem_questions = [row for row in session.scalars(select(Entity).where(Entity.homework_id == homework.id, Entity.kind == "question")) if row.document["lifecycle"] == "active" and normalized_stem(row.document["stem_text"]) == target]
            # Labels are free-form model text. A unique complete stem is sufficient;
            # ambiguous shared stems additionally require the same subquestion label.
            same_label = len(same_stem_questions) <= 1 or bool(original_label and item and normalized_label(item["label"]) == normalized_label(original_label))
            same_question = bool(item and target and normalized_stem(item["stem_text"]) == target and same_label)
            ready = same_question and item.get("recognition_status") == "ready"
            # A single visible problem may still be the wrong problem. Keep its
            # candidate in audit, but do not silently bind its answer to this case.
            doc = {**attempt.document, "revision": attempt.revision + 1, "recognition_revision": attempt.document["recognition_revision"] + 1, "answer_text": item["answer_text"] if same_question else None, "answer_regions": item["answer_regions"] if same_question else [], "recognition_status": "ready" if ready else "needs_revision"}
            save_entity(session, self.contracts, homework.id, "attempt", doc, row=attempt)
            attempt.annotations = {"answer_status": item["answer_status"] if same_question else "unclear", "target_binding": "matched_stem" if same_question else "needs_staff"}
            update_case(session, self.contracts, case, progress="paused" if case.document["progress"] == "paused" else "pending")
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
            if attempt.document["recognition_status"] != "ready":
                doc.update(verdict="unable_to_judge", reason_code="recognition_conflict", first_error_region=None, basis=[{"kind": "model_hypothesis", "summary": "本次作答识别或目标题对应尚待核实，不能据此判断对错。", "source_ref": None, "regions": []}])
            doc.update(id=new_id("decision"), revision=1, case_id=case.id, check_id=check.id, input_versions=versions, origin="model", validity="needs_review" if doc["verdict"] == "unable_to_judge" else "candidate", confirmation_source=None, confirmed_by=None, confirmed_at=None, replaces_decision_id=(case.document["current_decision"] or {}).get("id"), invalidated_by_event_id=None, created_at=iso())
            save_entity(session, self.contracts, homework.id, "decision", doc)
            cdoc = {**check.document, "revision": check.revision + 1, "model_version": result.metadata.model or result.metadata.requested_model, "prompt_version": result.prompt_version, "decision_ids": [doc["id"]]}
            save_entity(session, self.contracts, homework.id, "check", cdoc, row=check)
            if case.document["current_decision"] is not None:
                invalidate(session, self.contracts, case, "new_check_decision")
            # Even 'correct' remains pending until an authorized person verifies it.
            next_progress = "paused" if case.document["progress"] == "paused" else ("pending" if doc["verdict"] == "correct" else "needs_correction")
            update_case(session, self.contracts, case, current_decision=doc, progress=next_progress)
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
        question = {"question_id": question_id, "revision": 1, "homework_id": homework.id, "stem_text": item["stem_text"], "regions": item["regions"], "source": "recognizer", "source_actor_id": None, "bank_question_id": None, "knowledge_point_ids": None, "created_at": timestamp, "lifecycle": "active", "replacement_question_ids": []}
        question_row = save_entity(session, self.contracts, homework.id, "question", question)
        question_row.annotations = {"label": item["label"]}
        ids = list(dict.fromkeys([r["asset_id"] for r in item["regions"]] + [r["asset_id"] for r in item["answer_regions"]]))
        attempt = {"id": attempt_id, "revision": 1, "case_id": case_id, "question_id": question_id, "captured_question_revision": 1, "parent_attempt_id": None, "root_attempt_id": attempt_id, "asset_ids": ids, "recognition_revision": 1, "answer_text": item["answer_text"], "answer_regions": item["answer_regions"], "recognition_status": item.get("recognition_status", "needs_revision"), "help_condition": "unknown", "occurred_at": iso(homework.occurred_at), "created_at": timestamp}
        attempt_row = save_entity(session, self.contracts, homework.id, "attempt", attempt)
        attempt_row.annotations = {"answer_status": item["answer_status"]}
        case = {"id": case_id, "revision": 1, "scope": {"tenant_id": homework.tenant_id, "campus_id": homework.campus_id, "student_id": homework.student_id}, "homework_id": homework.id, "question_id": question_id, "current_question_revision": 1, "root_attempt_id": attempt_id, "current_attempt_id": attempt_id, "progress": "pending", "current_decision": None, "attempt_ids": [attempt_id], "check_ids": [], "hint_ids": [], "help_request_ids": [], "review_ids": [], "created_at": timestamp, "updated_at": timestamp, "lifecycle": "active", "replacement_case_ids": []}
        save_entity(session, self.contracts, homework.id, "case", case)


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
