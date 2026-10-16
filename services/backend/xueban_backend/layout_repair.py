"""Staff layout repair: add missed questions, split or merge wrongly cut ones.

The request transaction supersedes the affected questions and cases at once, so
their decisions and hints stop guiding the student. The worker later crops the
staff-selected regions from the private originals, recognizes each crop on its
own and creates replacement questions with their original attempts. A failed job
never revives the superseded records; staff can repair again with an empty
``supersede_questions`` list.
"""
import base64
from copy import deepcopy
import io

from PIL import Image
from sqlalchemy import select

from .correction_core import (assets_for, conflict, create_task, event, expect, get_entity, invalidate, iso,
    new_id, save_entity, set_task, update_case)
from .correction_models import Asset, Entity, Task
from .errors import Problem
from .providers.correction_pipeline import PipelineImage, PipelineResult

MAX_REPLACEMENTS = 12
CROP_PADDING = 16


def validate_regions(session, homework, regions):
    for region in regions:
        asset = session.get(Asset, region["asset_id"])
        if asset is None or asset.homework_id != homework.id or asset.document["status"] != "uploaded" or region["x"] + region["width"] > asset.document["width"] or region["y"] + region["height"] > asset.document["height"]:
            raise Problem(422, "validation", "invalid_region", "题区必须位于本作业已上传的原图内。")


def accept_repair(session, contracts, actor, homework, payload):
    """Runs inside the idempotent, homework-locked request transaction."""
    replacements = payload["replacement_regions"]
    if len(replacements) > MAX_REPLACEMENTS:
        raise Problem(422, "validation", "too_many_replacements", f"一次最多补充或拆分 {MAX_REPLACEMENTS} 道题。")
    for replacement in replacements:
        validate_regions(session, homework, replacement["regions"])
    asset_ids = list(dict.fromkeys(region["asset_id"] for item in replacements for region in item["regions"]))
    assets_for(session, homework.id, asset_ids)
    targets = []
    seen = set()
    for item in payload["supersede_questions"]:
        if item["question_id"] in seen:
            raise Problem(422, "validation", "invalid_request", "同一道题不能重复替换。")
        seen.add(item["question_id"])
        question = get_entity(session, item["question_id"], "question")
        if question.homework_id != homework.id:
            raise Problem(404, "not_found", "not_found", "资源不存在或不可访问。")
        if question.document["lifecycle"] != "active":
            conflict(question.revision)
        expect(question.revision, item["expected_revision"])
        targets.append(question)

    superseded_cases = []
    for question in targets:
        doc = {**question.document, "revision": question.revision + 1, "lifecycle": "superseded"}
        save_entity(session, contracts, homework.id, "question", doc, row=question)
        for case in session.scalars(select(Entity).where(Entity.homework_id == homework.id, Entity.kind == "case")):
            if case.document["question_id"] == question.id and case.document["lifecycle"] == "active":
                invalidate(session, contracts, case, "layout_repair")
                update_case(session, contracts, case, lifecycle="superseded", current_decision=None)
                superseded_cases.append(case.id)
    for task in session.scalars(select(Task).where(Task.homework_id == homework.id, Task.status.in_(["queued", "retryable_failed"]))):
        if task.inputs.get("case_id") in superseded_cases:
            set_task(session, contracts, task, "superseded")

    inputs = {"asset_ids": asset_ids, "replacements": deepcopy(replacements), "superseded_question_ids": [q.id for q in targets], "superseded_case_ids": superseded_cases, "reason": payload["reason"], "actor_id": actor.id}
    _, accepted = create_task(session, contracts, homework, "repair_layout", inputs, {"resource_type": "homework", "resource_id": homework.id})
    homework.revision += 1
    event(session, homework.id, homework.id, homework.revision, "layout.repair_requested", {"superseded_question_ids": inputs["superseded_question_ids"], "replacement_count": len(replacements)})
    return accepted


def _bounds(regions, width, height):
    left = max(0, min(r["x"] for r in regions) - CROP_PADDING)
    top = max(0, min(r["y"] for r in regions) - CROP_PADDING)
    right = min(width, max(r["x"] + r["width"] for r in regions) + CROP_PADDING)
    bottom = min(height, max(r["y"] + r["height"] for r in regions) + CROP_PADDING)
    return left, top, right, bottom


def crop_images(store, assets, replacement):
    """Return model inputs plus the per-asset offset that maps crop pixels back."""
    by_id = {asset.id: asset for asset in assets}
    images, offsets = [], {}
    for asset_id in dict.fromkeys(r["asset_id"] for r in replacement["regions"]):
        asset = by_id[asset_id]
        regions = [r for r in replacement["regions"] if r["asset_id"] == asset_id]
        box = _bounds(regions, asset.document["width"], asset.document["height"])
        with Image.open(io.BytesIO(store.read(asset.id, asset.document))) as source:
            crop = source.convert("RGB").crop(box)
            stream = io.BytesIO()
            crop.save(stream, format="JPEG", quality=92)
        images.append(PipelineImage(asset.id, "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode(), crop.width, crop.height))
        offsets[asset.id] = (box[0], box[1])
    return tuple(images), offsets


def recognize_replacements(pipeline, store, assets, replacements):
    """One recognition per replacement; returns a single auditable candidate."""
    results, metadata, prompt_version = [], None, None
    for replacement in replacements:
        images, offsets = crop_images(store, assets, replacement)
        result = pipeline.recognize(images=images)
        metadata, prompt_version = result.metadata, result.prompt_version
        items = []
        for item in result.data["items"]:
            moved = deepcopy(item)
            for field in ("regions", "answer_regions"):
                moved[field] = [{**region, "x": region["x"] + offsets[region["asset_id"]][0], "y": region["y"] + offsets[region["asset_id"]][1]} for region in item[field]]
            items.append(moved)
        results.append({"items": items})
    return PipelineResult({"replacements": results}, metadata, prompt_version)


def apply_repair(session, contracts, task, homework, result):
    replacements = task.inputs["replacements"]
    candidates = result.data["replacements"]
    if len(candidates) != len(replacements):
        raise Problem(409, "conflict", "stale_input", "补题结果与请求不一致。")
    assets = {a.id: a for a in assets_for(session, homework.id, task.inputs["asset_ids"])}
    created_questions, created_cases = [], []
    for replacement, candidate in zip(replacements, candidates):
        items = candidate["items"]
        item = items[0] if len(items) == 1 else None
        answer_regions = []
        if item is not None:
            answer_regions = [r for r in item["answer_regions"] if r["asset_id"] in assets and r["x"] + r["width"] <= assets[r["asset_id"]].document["width"] and r["y"] + r["height"] <= assets[r["asset_id"]].document["height"]]
        stem = replacement["stem_text"] or (item["stem_text"] if item else None)
        # Staff-drawn regions are authoritative; the crop recognition only fills
        # the answer. More or fewer than one item means a person must check it.
        ready = bool(item and stem and item.get("recognition_status") == "ready" and len(answer_regions) == len(item["answer_regions"]))
        question_id, case_id, attempt_id = new_id("question"), new_id("case"), new_id("attempt")
        timestamp = iso()
        question = {"question_id": question_id, "revision": 1, "homework_id": homework.id, "stem_text": stem, "regions": replacement["regions"], "source": "staff_layout", "source_actor_id": task.inputs["actor_id"], "bank_question_id": None, "knowledge_point_ids": None, "created_at": timestamp, "lifecycle": "active", "replacement_question_ids": []}
        row = save_entity(session, contracts, homework.id, "question", question)
        row.annotations = {"label": item["label"]} if item else None
        attempt = {"id": attempt_id, "revision": 1, "case_id": case_id, "question_id": question_id, "captured_question_revision": 1, "parent_attempt_id": None, "root_attempt_id": attempt_id, "asset_ids": list(dict.fromkeys(r["asset_id"] for r in replacement["regions"])), "recognition_revision": 1, "answer_text": item["answer_text"] if item else None, "answer_regions": answer_regions, "recognition_status": "ready" if ready else "needs_revision", "help_condition": "unknown", "occurred_at": iso(homework.occurred_at), "created_at": timestamp}
        attempt_row = save_entity(session, contracts, homework.id, "attempt", attempt)
        attempt_row.annotations = {"answer_status": item["answer_status"] if item else "unclear", "target_binding": "staff_layout" if item else "needs_staff"}
        case = {"id": case_id, "revision": 1, "scope": {"tenant_id": homework.tenant_id, "campus_id": homework.campus_id, "student_id": homework.student_id}, "homework_id": homework.id, "question_id": question_id, "current_question_revision": 1, "root_attempt_id": attempt_id, "current_attempt_id": attempt_id, "progress": "pending", "current_decision": None, "attempt_ids": [attempt_id], "check_ids": [], "hint_ids": [], "help_request_ids": [], "review_ids": [], "created_at": timestamp, "updated_at": timestamp, "lifecycle": "active", "replacement_case_ids": []}
        save_entity(session, contracts, homework.id, "case", case)
        created_questions.append(question_id)
        created_cases.append(case_id)
    for question_id in task.inputs["superseded_question_ids"]:
        old = get_entity(session, question_id, "question")
        doc = {**old.document, "revision": old.revision + 1, "replacement_question_ids": sorted(set(old.document["replacement_question_ids"]) | set(created_questions))}
        save_entity(session, contracts, homework.id, "question", doc, row=old)
    for case_id in task.inputs["superseded_case_ids"]:
        old = get_entity(session, case_id, "case")
        update_case(session, contracts, old, replacement_case_ids=sorted(set(old.document["replacement_case_ids"]) | set(created_cases)))
    homework.revision += 1
    return {"resource_type": "homework", "resource_id": homework.id}
