"""One voluntary child-owned variant, with an answer-blind independent solve.

These records are optional practice, never mastery evidence. Model failures are
terminal for a queue execution; only an explicit student POST can retry them.
"""
from dataclasses import asdict, is_dataclass, replace
from datetime import timedelta
from difflib import SequenceMatcher
import hashlib
import json
import os
import re
import time
from typing import Annotated, Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func

from .auth import Actor
from .correction_models import Entity
from .curriculum_catalog import canonical_subject
from .errors import Problem
from .learning_models import (CurriculumGraph, KnowledgeNode, LearningOutbox, PracticeItem, PracticeSession, PracticeResponse, PracticeAssessment,
                              StudentLearningProfile, VariantGeneration)
from .learning_projection import enqueue, new_id, published_graph, register_job_handler
from .models import Homework, Student
from .providers import ArkClient, ProviderError
from .providers.bounded_chat import BoundedChatClient
from .providers.correction_pipeline import _contains_credential, _validate
from .providers.prompts import Role
from .providers.user_original.ark import ArkClient as UserOriginalArkClient
from sqlalchemy.orm import Session

TYPES = ("single_choice", "multiple_choice", "fill_blank", "calculation", "application", "proof")
CALL_SECONDS = 35
MAX_JOB_SECONDS = 240  # less than the learning worker's five-minute lease
SOURCE_BINDING_VERSION = "judgment-evidence-v2"
# General record revision and assistance are audited separately. They do not
# change the original image, transcription or judgment that authorized practice.
ATTEMPT_EVIDENCE_FIELDS = (
    "id", "case_id", "question_id", "captured_question_revision", "parent_attempt_id", "root_attempt_id",
    "asset_ids", "recognition_revision", "answer_text", "answer_regions", "answer_status",
    "recognition_uncertainties", "recognition_status",
)
GENERATE_PROMPT = """为小学或初中学生生成恰好一道与 source_question 同题型、相近难度的练习。
按 student_grade 和 source_curriculum 的年级、教材及 source_subject，只练原题和 knowledge 涉及的知识；信息缺失时不猜年级或引入新知识。
题干、提示和解答用学生熟悉的词和短句，像同年级课本练习。不加装饰性故事、陌生术语或复杂背景。
数学题优先只换数字，保留学生熟悉的句式、运算关系和提问方式，不强行改故事，包括“已知…求…”及简短应用计算题。新数字须合理、可解，难度不增加。
其他学科不能只换数字、人名或同义词来照抄原题；保持原知识点，用简短清楚的新内容。
不得原样重复原题、previous_questions_to_avoid 或 rejected_questions，不得包含学生信息。此前生成过的数学练习也允许更换数字，但不能完全重复。
题干条件完整，选项独立列出并与新题匹配。仅调换选项顺序不算新题。
选择题支持单选/多选，answer_values 写完整正确选项集合；填空每空用独立稳定id，field_answers逐空给答案；
解答题写完整参考解答及合理等价形式；纯数值终答可用answer_kind=number，其他终答用text，两者都必须在solution_text写完整必要步骤。非填空的answer_fields/field_answers为空。
填空时answer_values为空；其他题至少一个answer_values。solution_text用同年级教材的方法，逐步写出简短、完整的必要步骤和正确答案，不能只给终答。
题干不泄露答案；hint_text只给方向。答案和步骤单独放在答案字段及solution_text，供学生展开查看。
遵守requested_question_type/question_kind；只返回schema字段。不需要图片、外部材料或无法展示的图形。"""
VERIFY_PROMPT = """独立解答这道给小学或初中学生的练习。只看新题、目标类型和年级教材信息，不会看到出题者答案，不猜其结论。
按 student_grade 和 source_curriculum 的年级、教材及 source_subject、knowledge_target 解题；信息缺失时不猜年级或引入新知识。
你的solution_text会作为参考解答保存给学生。用学生熟悉的词和短句，按同年级教材的方法逐步写出简短、完整的必要步骤和正确答案，不能只给终答。
不加装饰性故事、陌生术语或复杂背景。数学只换数字的练习是允许的，包括“已知…求…”及简短应用计算题。
检查条件是否齐全、答案是否确定、所有选项和每一空。按要求题型、知识目标和年级教材范围判断same_type，超出范围则为false。
unique_answer对开放证明指结论明确且评分要求确定，允许等价合法解法。无法确定则标false。
选择题返回完整正确选项；填空逐空返回；解答题给独立结论与完整必要步骤。
解答题的数值终答可以标number，其他表述标text；这只是答案表示，不能改变题型或省略solution_text。
不得输出学生掌握或教师批准。
只返回schema字段，不依据不存在的图片或外部文章。"""
EQUIVALENCE_PROMPT = """核对两份针对同一道题的答案/解法是否数学及语义等价、每空完整且条件一致。
允许分数小数、代数等价式、不同正确解法；不因文字相似就相等。任一不完整或无法确定则equivalent=false。
answer_kind的number/text差异本身不代表数学差异。
这是答案一致性核验，不是独立盲解题，也不是教师审核。"""
SOLUTION_REVIEW_PROMPT = EQUIVALENCE_PROMPT + """
对于解答题，必须逐一核对两份solution_text中的运算、推导、所用条件和结论；分别报告answer_a_steps_valid和answer_b_steps_valid。
不能仅因answer_values终答相同就判通过：错误中间步骤、无依据跳步、循环论证或遗漏题目要求时，对应steps_valid=false。
允许不同但完整正确的必要解法，不要求题目没有要求的额外步骤。证据不足不能将steps_valid设true。
equivalent只在两份完整解答均有效、终答与自身步骤一致且两份结论等价时为true。"""
GRADE_PROMPT = """核对这道自愿练习的学生当前作答。允许等价表达、合理不同解法；检查每一空及题目实际要求的步骤。
不要求未被题目要求的额外步骤。任何明确错误则incorrect；证据/服务不足则unscorable，不能猜错。
不得依据参考答案替学生补内容。只给简短具体反馈，不评价能力或掌握程度。"""


def _text(n=2000):
    return {"type": "string", "minLength": 1, "maxLength": n}


def _obj(properties):
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


def _array(items, minimum=0, maximum=12):
    return {"type": "array", "minItems": minimum, "maxItems": maximum, "items": items}


ANSWER_SCHEMA = {
    "answer_kind": {"enum": ["number", "choice", "text"]},
    "answer_values": _array(_text(1000)),
    "field_answers": _array(_obj({"field_id": _text(40), "answer_kind": {"enum": ["number", "text"]},
                                  "answer_values": _array(_text(1000), 1)})),
    "solution_text": _text(3000),
}
CANDIDATE_SCHEMA = _obj({
    "question_type": {"enum": list(TYPES)}, "question_kind": {"enum": ["choice", "fill_blank", "solution"]},
    "stem_text": _text(3000), "options": _array(_obj({"label": _text(8), "text": _text(800)}), maximum=10),
    "answer_fields": _array(_obj({"id": _text(40), "label": _text(80)})),
    **ANSWER_SCHEMA, "hint_text": _text(400),
})
VERIFY_SCHEMA = _obj({"self_contained": {"type": "boolean"}, "unique_answer": {"type": "boolean"},
                      "same_type": {"type": "boolean"}, **ANSWER_SCHEMA, "reason": _text(600)})
EQUIVALENCE_SCHEMA = _obj({"equivalent": {"type": "boolean"}, "reason": _text(600)})
SOLUTION_REVIEW_SCHEMA = _obj({"equivalent": {"type": "boolean"},
    "answer_a_steps_valid": {"type": "boolean"}, "answer_b_steps_valid": {"type": "boolean"}, "reason": _text(600)})
GRADE_SCHEMA = _obj({"result": {"enum": ["correct", "incorrect", "unscorable"]}, "feedback": _text(600)})


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _now():
    from .practice import now
    return now()


def _problem(code, message, status=409):
    return Problem(status, "not_found" if status == 404 else "conflict", code, message)


def _help_context(attempt):
    return {"attempt_id": attempt.id, "record_revision": attempt.revision,
            "help_condition": attempt.document.get("help_condition")}


def _source_fingerprint(snapshot):
    return _digest({key: value for key, value in snapshot.items() if key != "source_help"})


def _current_help_context(session, row):
    attempt = session.get(Entity, row.snapshot["attempt_id"], populate_existing=True)
    return _help_context(attempt) if attempt and attempt.homework_id == row.snapshot["homework_id"] else None


def source_snapshot(session, tenant_id, student_id, case_id, knowledge_id=None):
    """Fresh version/ownership checks; no old-label fallback across textbook scope."""
    from .practice import _source_matches_graph
    from .learning_mapping import _current_links
    student = session.get(Student, student_id, populate_existing=True)
    case = session.get(Entity, case_id, populate_existing=True)
    homework = session.get(Homework, case.homework_id, populate_existing=True) if case else None
    if (not student or not student.active or student.tenant_id != tenant_id or not case or case.kind != "case"
            or not homework or homework.student_id != student_id or homework.tenant_id != tenant_id):
        raise _problem("not_found", "来源错题不存在或不可访问。", 404)
    doc = case.document
    question = session.get(Entity, doc.get("question_id"), populate_existing=True)
    attempt = session.get(Entity, doc.get("current_attempt_id"), populate_existing=True)
    decision = doc.get("current_decision") or {}
    versions = decision.get("input_versions") or {}
    decision_row = session.get(Entity, decision.get("id"), populate_existing=True) if decision.get("id") else None
    if (homework.processing_blocked or doc.get("lifecycle", "active") != "active"
            or not question or question.kind != "question" or question.homework_id != homework.id
            or not attempt or attempt.kind != "attempt" or attempt.homework_id != homework.id
            or not decision_row or decision_row.kind != "decision" or decision_row.homework_id != homework.id
            or decision_row.document != decision or decision.get("revision") != decision_row.revision
            or question.document.get("question_id") != question.id or question.document.get("revision") != question.revision
            or attempt.document.get("question_id") != question.id or attempt.document.get("case_id") != case.id
            or decision.get("case_id") != case.id or doc.get("current_question_revision") != question.revision
            or versions.get("question_id") != question.id
            or attempt.document.get("recognition_status") != "ready" or attempt.document.get("answer_status") != "present"
            or decision.get("validity") not in ("candidate", "confirmed")
            or decision.get("verdict") not in ("incorrect", "partially_correct")
            or versions.get("question_revision") != question.revision
            or versions.get("attempt_id") != attempt.id
            or versions.get("attempt_recognition_revision") != attempt.document.get("recognition_revision")
            or not (question.document.get("stem_text") or "").strip()):
        raise _problem("source_not_eligible", "这道题当前没有清楚且有效的错题依据，请先完成原题核对。")
    q = question.document
    # A displayed provisional wrong answer may offer independent practice
    # even when its original judgment needs review. This does not promote the
    # original decision or make it eligible for knowledge/mastery evidence.
    kind = q.get("question_type")
    if kind not in TYPES:
        # Structural inference only, never invent a proof/choice type from prose.
        kind = "single_choice" if q.get("options") else "fill_blank" if q.get("blank_count") else None
    if kind is None:
        raise _problem("unsupported_source_type", "原题题型尚未确定，暂不能可靠生成同题型练习。")
    family = "choice" if kind in ("single_choice", "multiple_choice") else "fill_blank" if kind == "fill_blank" else "solution"
    profile = session.get(StudentLearningProfile, student_id, populate_existing=True)
    graph = published_graph(session, student_id)
    if graph and not _source_matches_graph(session, student_id, homework, question, graph):
        raise _problem("curriculum_mismatch", "这道历史错题与当前教材范围不一致，请先核对教材。")
    links = list(_current_links(session, question.id).values()) if graph else []
    ids = {link.knowledge_id for link in links if link.status == "confirmed" and link.graph_version == graph.version}
    if knowledge_id and knowledge_id not in ids:
        raise _problem("source_not_eligible", "所选知识点尚未与这道原题可靠关联。")
    selected = knowledge_id or (next(iter(ids)) if len(ids) == 1 else None)
    node = session.get(KnowledgeNode, (graph.version, selected)) if graph and selected else None
    return {"binding_version": SOURCE_BINDING_VERSION,
            "source_help": _help_context(attempt),
            "tenant_id": tenant_id, "student_id": student_id, "source_case_id": case_id,
            "homework_id": homework.id, "question_id": question.id, "question_revision": question.revision,
            "question_digest": _digest(q), "attempt_id": attempt.id,
            "attempt_revision": attempt.document.get("recognition_revision"),
            "attempt_digest": _digest({key: attempt.document.get(key) for key in ATTEMPT_EVIDENCE_FIELDS}),
            "decision_id": decision.get("id"), "decision_revision": decision.get("revision"), "decision_digest": _digest(decision),
            "graph_version": graph.version if graph else None, "profile_revision": profile.revision if profile else None,
            "source_curriculum": homework.curriculum, "knowledge_id": selected,
            "knowledge": {"id": node.id, "name": node.name, "objective": node.observable_objective} if node else None,
            "question_type": kind, "question_kind": family,
            "source_question": {"stem_text": q["stem_text"], "options": q.get("options") or [],
                                "blank_count": q.get("blank_count"), "question_type": kind}}


def lock_source(session, row):
    # Match correction/book-selection lock ordering and refresh before saving.
    session.scalar(select(Student).where(Student.id == row.student_id).with_for_update().execution_options(populate_existing=True))
    session.scalar(select(StudentLearningProfile).where(StudentLearningProfile.student_id == row.student_id)
                   .with_for_update().execution_options(populate_existing=True))
    session.scalar(select(Homework).where(Homework.id == row.snapshot["homework_id"])
                   .with_for_update().execution_options(populate_existing=True))
    ids = [row.source_case_id, *[row.snapshot[k] for k in ("question_id", "attempt_id", "decision_id") if row.snapshot.get(k)]]
    list(session.scalars(select(Entity).where(Entity.id.in_(ids)).order_by(Entity.id)
                         .with_for_update().execution_options(populate_existing=True)))
    if row.graph_version:
        session.scalar(select(CurriculumGraph).where(CurriculumGraph.version == row.graph_version)
                       .with_for_update().execution_options(populate_existing=True))


def snapshot_current(session, row):
    try:
        current = source_snapshot(session, row.tenant_id, row.student_id, row.source_case_id, row.knowledge_id)
        # The token distinguishes explicit new questions under the existing
        # unique source index; it never replaces the evidence binding.
        if "regenerate_token" in row.snapshot:
            current["regenerate_token"] = row.snapshot["regenerate_token"]
        if row.snapshot.get("binding_version") != SOURCE_BINDING_VERSION:
            # Pre-v2 rows contain only an opaque whole-record digest. Keep an
            # unchanged legacy row usable; never guess its old answer after a
            # changed record merely because the current record says hint_used.
            current.pop("binding_version", None)
            current.pop("source_help", None)
            attempt = session.get(Entity, row.snapshot["attempt_id"], populate_existing=True)
            current["attempt_digest"] = _digest(attempt.document)
        return _source_fingerprint(current) == row.source_fingerprint
    except Problem:
        return False


def generation_dict(session, row):
    status, failure = row.status, row.failure_code
    if status in ("ready", "queued", "generating", "verifying") and not snapshot_current(session, row):
        status, failure = "superseded", "stale_source"
    elif status == "queued" and row.outbox_id:
        job = session.get(LearningOutbox, row.outbox_id)
        if job and job.status == "running":
            status = "generating"
        elif job and job.status == "dead":
            status, failure = "failed", "provider_unavailable"
    notices = {"ready": "题目已由 AI 独立解题核验，可自愿练习；不是老师审核或掌握证明。",
               "failed": "本次未生成可用题目，可以稍后重试；不会记错或增加任务。",
               "superseded": "原题、作答或教材已更新，请回到当前错题重新选择。"}
    return {"job_id": row.id, "student_id": row.student_id, "status": status,
            "source_case_id": row.source_case_id, "graph_version": row.graph_version, "knowledge_id": row.knowledge_id,
            "question_type": row.question_type, "item_id": row.item_id if status == "ready" else None,
            "session_id": row.session_id, "failure_code": failure,
            "notice": notices.get(status, "正在生成一道同题型练习并独立核验，完成后由你选择是否开始。")}


def bounded_client(client):
    """Share configured credentials/model, not a 200s correction-call budget."""
    if isinstance(client, (ArkClient, UserOriginalArkClient)):
        return BoundedChatClient(replace(client.settings, timeout_seconds=CALL_SECONDS, total_timeout_seconds=CALL_SECONDS,
                                         max_attempts=1, max_tokens=3072, max_concurrency=1)), True
    return client, False  # deterministic injected clients in tests


def _call(client, stage, data, schema, prompt, calls):
    result = client.generate(role=Role.CHECK, text=json.dumps(data, ensure_ascii=False), output_schema=schema,
                             instructions=prompt, thinking=False, max_tokens=3072)
    _validate(schema, result.data, "invalid_output")
    metadata = asdict(result.metadata) if is_dataclass(result.metadata) else {"model": getattr(result.metadata, "model", None)}
    secret = getattr(client.settings, "api_key", "")
    if secret and (_contains_credential(result.data, secret) or _contains_credential(metadata, secret)):
        raise ProviderError("invalid_output")
    calls.append({"stage": stage, "input": data, "output": result.data, "metadata": metadata})
    return result.data


def _math_source(snapshot, source_subject=None):
    question = snapshot["source_question"]
    subject = source_subject or question.get("subject") or (snapshot.get("source_curriculum") or {}).get("subject")
    if subject:
        return canonical_subject(subject) == "数学"
    # Older snapshots may lack a subject. Require explicit arithmetic cues,
    # never just numbers, a calculation question_type, or a student's grade.
    stem = question["stem_text"]
    return bool(("计算" in stem and re.search(r"\d\s*[+\-−×÷*/]\s*\(?[+\-−]?\d", stem))
                or re.search(r"已知.*(?:商|被除数|除数|因数|绝对值|相反数|倒数)(?:是|为|等于|[=＝]).*求", stem))


def _math_numeric_signature(value):
    from .practice import _normalize, _number

    # Normalize only harmless wording/formatting, not operations, units or
    # conditions. The same signature is used against both source and history.
    lines = []
    for line in value.splitlines():
        line = _normalize(re.sub(r"^\s*\d+[.、](?!\d)\s*", "", line))
        line = line.replace("两个数", "两数")
        line = re.sub(r"(被除数|除数|商|和|差|积)(?:等于|为|是)", r"\1=", line)
        line = line.rstrip("。.?？")
        if line:
            lines.append(line)
    content = "".join(dict.fromkeys(lines))
    # Include unary signs (商是-7/2, (-3), 2/-5), but keep binary
    # operators after a number/variable/closing bracket in the template.
    numbers = r"(?<![a-z0-9.)\]])[+-]?\d+(?:\.\d+)?|\d+(?:\.\d+)?"
    return re.sub(numbers, "#", content), tuple(_number(n) for n in re.findall(numbers, content))


def _numeric_substitution(stem, previous):
    current, original = _math_numeric_signature(stem), _math_numeric_signature(previous)
    return current[0] == original[0] and current[1] != original[1]


def _near_copy_stem(stem, previous, *, allow_numeric_changes=False):
    from .practice import _normalize
    current, original = _normalize(stem), _normalize(previous)
    if current == original:
        return True
    if allow_numeric_changes:
        numeric_current, numeric_original = _math_numeric_signature(stem), _math_numeric_signature(previous)
        if numeric_current[0] == numeric_original[0]:
            # Wording alone is not a new question. Changed values are allowed,
            # while equal values (including 3 and 3.0) remain duplicates.
            return numeric_current[1] == numeric_original[1]
    # Without explicit math context, keep the prose-copy safeguards.
    # Arithmetic drills legitimately share notation and short instructions.
    template = lambda text: re.sub(r"\d+(?:\.\d+)?", "#", text)
    words = re.sub(r"[^a-z\u4e00-\u9fff]", "", original)
    for instruction in ("计算", "求值", "等于几", "等于多少", "写出必要步骤", "结果", "填空"):
        words = words.replace(instruction, "")
    if len(words) >= 4 and template(current) == template(original):
        return True
    # OCR can repeat a stem with a new numeric label. Compare its individual
    # lines as well as the whole text, ignoring numbers/punctuation and small
    # wording changes. Short arithmetic instructions remain legitimate drills.
    def prose(value):
        return re.sub(r"[^a-z\u4e00-\u9fff]", "", _normalize(value))
    candidate_words = prose(stem)
    if len(candidate_words) < 12:
        return False
    return any(len(part) >= 12 and SequenceMatcher(None, candidate_words, part, autojunk=False).ratio() >= .82
               for part in [prose(previous), *(prose(line) for line in previous.splitlines())])


def _valid_solution_answer(answer):
    """Accept typed numerical conclusions without treating them as full work.

    Both representations still require the blind solve and full-step review.
    A number field must contain literal rational values, not an expression,
    units, separated digits or an unparseable/nonfinite placeholder.
    """
    from .practice import _number

    values = answer["answer_values"]
    if not values or not all(value.strip() for value in values):
        return False
    if answer["answer_kind"] == "text":
        return True
    if answer["answer_kind"] != "number":
        return False
    decimal = r"[+\-−]?(?:\d+(?:\.\d+)?|\.\d+)"
    return all(re.fullmatch(rf"\s*{decimal}(?:\s*/\s*{decimal})?\s*", value)
               and _number(value) is not None for value in values)


def _valid_candidate(candidate, snapshot, *, source_subject=None):
    from .practice import _normalize
    allow_numeric_changes = _math_source(snapshot, source_subject)
    if (candidate["question_type"] != snapshot["question_type"] or candidate["question_kind"] != snapshot["question_kind"]
            or _near_copy_stem(candidate["stem_text"], snapshot["source_question"]["stem_text"],
                               allow_numeric_changes=allow_numeric_changes)):
        return False
    options = candidate["options"]
    labels = [o["label"] for o in options]
    fields = [f["id"] for f in candidate["answer_fields"]]
    supplied_fields = [f["field_id"] for f in candidate["field_answers"]]
    if len(set(labels)) != len(labels) or len(set(fields)) != len(fields) or len(set(supplied_fields)) != len(supplied_fields):
        return False
    if candidate["question_kind"] == "choice":
        original_options = snapshot["source_question"].get("options") or []
        if (original_options and sorted(_normalize(o["text"]) for o in options) == sorted(
                _normalize(o["text"]) for o in original_options)
                and not (allow_numeric_changes and _numeric_substitution(
                    candidate["stem_text"], snapshot["source_question"]["stem_text"]))):
            return False
        answer = candidate["answer_values"]
        return (candidate["answer_kind"] == "choice" and len(options) >= 2 and not fields and not supplied_fields
                and bool(answer) and len(set(answer)) == len(answer) and set(answer) <= set(labels)
                and (candidate["question_type"] != "single_choice" or len(answer) == 1))
    if options:
        return False
    if candidate["question_kind"] == "fill_blank":
        expected_count = snapshot["source_question"].get("blank_count")
        return (bool(fields) and set(fields) == set(supplied_fields) and not candidate["answer_values"]
                and (not expected_count or len(fields) == expected_count))
    return (_valid_solution_answer(candidate) and not fields and not supplied_fields
            and bool(candidate["solution_text"].strip()))


def _previous_stems(session, row):
    return list(session.scalars(select(PracticeItem.stem_text).join(
        VariantGeneration, VariantGeneration.item_id == PracticeItem.id).where(
            VariantGeneration.tenant_id == row.tenant_id, VariantGeneration.student_id == row.student_id,
            VariantGeneration.source_case_id == row.source_case_id, VariantGeneration.id != row.id,
            PracticeItem.tenant_id == row.tenant_id, PracticeItem.owner_student_id == row.student_id)
        .order_by(VariantGeneration.created_at.desc())))


def _distinct_candidate(session, row, candidate, *, source_subject=None):
    return all(not _near_copy_stem(candidate["stem_text"], previous,
                                  allow_numeric_changes=_math_source(row.snapshot, source_subject))
               for previous in _previous_stems(session, row))


def _valid_reference(candidate, verified):
    if candidate["question_kind"] == "choice":
        values = verified["answer_values"]
        return (verified["answer_kind"] == "choice" and not verified["field_answers"] and bool(values)
                and len(values) == len(set(values)) and set(values) <= {o["label"] for o in candidate["options"]}
                and (candidate["question_type"] != "single_choice" or len(values) == 1))
    if candidate["question_kind"] == "fill_blank":
        fields = [f["field_id"] for f in verified["field_answers"]]
        return (not verified["answer_values"] and len(fields) == len(set(fields))
                and set(fields) == {f["id"] for f in candidate["answer_fields"]})
    return (_valid_solution_answer(verified)
            and not verified["field_answers"] and bool(verified["solution_text"].strip()))


def _answer_agreement(a, b):
    """True/False for numeric/choice; None means semantic comparison required."""
    from .practice import _number, _normalize
    if a["answer_kind"] != b["answer_kind"]:
        return None
    kind = a["answer_kind"]
    av, bv = a["answer_values"], b["answer_values"]
    if not av or not bv:
        return False
    if kind == "choice":
        return set(av) == set(bv)
    if kind == "number":
        aa, bb = [_number(v) for v in av], [_number(v) for v in bv]
        return None if None in aa or None in bb else set(aa) == set(bb)
    return True if {_normalize(v) for v in av} == {_normalize(v) for v in bv} else None


def _agrees(candidate, verified):
    if candidate["question_kind"] == "solution":
        # Identical terminal answers do not establish valid intermediate work.
        # Every solution gets the separate, explicitly non-blind full-work check.
        return None
    if candidate["question_kind"] != "fill_blank":
        return _answer_agreement(candidate, verified)
    a, b = ({x["field_id"]: x for x in row["field_answers"]} for row in (candidate, verified))
    if len(b) != len(verified["field_answers"]) or set(a) != set(b):
        return False
    results = [_answer_agreement(a[k], b[k]) for k in a]
    return False if False in results else None if None in results else True


@register_job_handler("generate_student_variant")
def generate_student_variant(session, job, worker):
    row = session.get(VariantGeneration, job.payload.get("generation_id"))
    if row is None or row.tenant_id != job.tenant_id or row.student_id != job.student_id or row.outbox_id != job.id:
        return {"status": "obsolete"}
    if row.status == "ready":
        return {"status": "ready"}
    if job.tries > 1:
        # A process interruption has an unknown billing/outcome boundary. Never
        # turn the generic five-attempt outbox retry into five paid generations.
        row.status, row.failure_code, row.updated_at = "failed", "provider_unavailable", _now()
        row.audit = {**row.audit, "interrupted_execution": True}
        return {"status": row.status, "failure_code": row.failure_code}
    calls, started = [], time.monotonic()
    client, owned = bounded_client(worker.client) if worker.client else (None, False)
    try:
        if not snapshot_current(session, row):
            row.status, row.failure_code = "superseded", "stale_source"
            return {"status": row.status}
        if client is None or not client.settings.enabled:
            row.status, row.failure_code = "failed", "provider_disabled"
            return {"status": row.status}
        max_regens = os.environ.get("VARIANT_MAX_REGENERATIONS", "1")
        if max_regens not in ("0", "1"):
            raise ProviderError("invalid_configuration")
        # Execution-only context: adding these fields to source_snapshot would
        # invalidate fingerprints for existing ready items and sessions.
        profile = session.get(StudentLearningProfile, row.student_id, populate_existing=True)
        question = session.get(Entity, row.snapshot["question_id"], populate_existing=True)
        teaching_context = {"source_curriculum": row.snapshot.get("source_curriculum"),
                            "student_grade": profile.document.get("grade") if profile else None,
                            "source_subject": ((question.annotations or {}).get("subject_override")
                                               or question.document.get("subject")) if question else None}
        rejected_questions = []
        for attempt in range(1 + int(max_regens)):
            if time.monotonic() - started > MAX_JOB_SECONDS - 3 * CALL_SECONDS:
                raise ProviderError("deadline_exceeded")
            if not snapshot_current(session, row):
                row.status, row.failure_code = "superseded", "stale_source"
                break
            row.status = "generating"
            candidate = _call(client, "generate", {"source_question": row.snapshot["source_question"],
                              **teaching_context,
                              "knowledge": row.snapshot["knowledge"], "requested_question_type": row.question_type,
                              "question_kind": row.question_kind,
                              "rejected_questions": rejected_questions,
                              "previous_questions_to_avoid": _previous_stems(session, row)[:5]},
                              CANDIDATE_SCHEMA, GENERATE_PROMPT, calls)
            if (not _valid_candidate(candidate, row.snapshot, source_subject=teaching_context["source_subject"])
                    or not _distinct_candidate(session, row, candidate, source_subject=teaching_context["source_subject"])):
                rejected_questions.append(candidate["stem_text"])
                continue
            row.status = "verifying"
            # Deliberately exclude generated answer, solution, hint and old student answer.
            blind_input = {k: candidate[k] for k in ("stem_text", "options", "answer_fields", "question_type", "question_kind")}
            blind_input.update(teaching_context)
            blind_input["knowledge_target"] = row.snapshot["knowledge"]
            blind_input["requested_question_type"] = row.question_type
            verified = _call(client, "blind_solve", blind_input, VERIFY_SCHEMA, VERIFY_PROMPT, calls)
            reference_valid = _valid_reference(candidate, verified)
            agrees = _agrees(candidate, verified) if reference_valid and all(
                verified[k] for k in ("self_contained", "unique_answer", "same_type")) else False
            if agrees is None:
                is_solution = candidate["question_kind"] == "solution"
                agreement = _call(client, "answer_equivalence", {"question": blind_input, "answer_a": {
                    k: candidate[k] for k in ANSWER_SCHEMA}, "answer_b": {k: verified[k] for k in ANSWER_SCHEMA}},
                    SOLUTION_REVIEW_SCHEMA if is_solution else EQUIVALENCE_SCHEMA,
                    SOLUTION_REVIEW_PROMPT if is_solution else EQUIVALENCE_PROMPT, calls)
                agrees = agreement["equivalent"] and (not is_solution or (
                    agreement["answer_a_steps_valid"] and agreement["answer_b_steps_valid"]))
            if not agrees:
                continue
            lock_source(session, row)
            if not snapshot_current(session, row):
                row.status, row.failure_code = "superseded", "stale_source"
                break
            if not _distinct_candidate(session, row, candidate, source_subject=teaching_context["source_subject"]):
                continue
            # Persist independently solved reference; generated hint is direction only.
            models = [call["metadata"].get("model") for call in calls if call["stage"] in ("generate", "blind_solve")][-2:]
            verification = {"source": "ai", "status": "verified", "generator_model": models[0], "verifier_model": models[-1],
                            "heterogeneous_models": len(set(models)) > 1}
            stamp, item_id = _now(), new_id("item")
            session.add(PracticeItem(id=item_id, tenant_id=row.tenant_id, owner_student_id=row.student_id,
                knowledge_id=row.knowledge_id, graph_version=row.graph_version, item_family_id=new_id("family"),
                stem_text=candidate["stem_text"], question_type=row.question_type, question_kind=row.question_kind,
                options=candidate["options"], answer_fields=candidate["answer_fields"], field_answers=verified["field_answers"],
                answer_kind=verified["answer_kind"], answer_values=verified["answer_values"], hint_text=candidate["hint_text"],
                solution_text=verified["solution_text"], verification=verification, difficulty=1, source="model", status="ai_verified",
                generation={"job_id": row.id, "source_fingerprint": row.source_fingerprint},
                revision=1, created_at=stamp, updated_at=stamp))
            row.item_id, row.status, row.failure_code = item_id, "ready", None
            break
        if row.status not in ("ready", "superseded"):
            row.status, row.failure_code = "failed", "verification_failed"
    except ProviderError as error:
        row.status, row.failure_code = "failed", "provider_unavailable"
        calls.append(_failure_audit(error, client))
    finally:
        if owned:
            client.close()
        row.audit = {**row.audit, "executions": [*row.audit.get("executions", []),
                     {"calls": calls, "status": row.status, "source_help_after": _current_help_context(session, row)}][-5:]}
        row.updated_at = _now()
    return {"status": row.status, "failure_code": row.failure_code}


class GenerationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    willing: Literal[True]
    source_case_id: str = Field(min_length=1, max_length=64)
    knowledge_id: str | None = Field(default=None, max_length=64)
    count: Literal[1] = 1
    regenerate_token: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"\S")


def load_generation(session, actor, generation_id):
    from .app import require_student
    row = session.get(VariantGeneration, generation_id)
    if row is None or row.tenant_id != actor.tenant_id:
        raise _problem("not_found", "任务不存在或不可访问。", 404)
    require_student(session, actor, row.student_id)
    return row


def install_variant_generation(app, database_session, current_actor):
    from .app import require_student
    ActorDep = Annotated[Actor, Depends(current_actor)]
    SessionDep = Annotated[Session, Depends(database_session)]

    @app.post("/v1/students/{student_id}/variant-generations", status_code=202, operation_id="generateStudentVariant")
    def create(student_id: str, payload: GenerationInput, actor: ActorDep, session: SessionDep):
        student = require_student(session, actor, student_id)
        if "student" not in actor.roles:
            raise Problem(403, "authorization", "forbidden", "由孩子本人自愿选择生成练习。")
        session.scalar(select(Student).where(Student.id == student_id).with_for_update())
        snapshot = source_snapshot(session, student.tenant_id, student.id, payload.source_case_id, payload.knowledge_id)
        if payload.regenerate_token is not None:
            snapshot["regenerate_token"] = payload.regenerate_token
        fingerprint = _source_fingerprint(snapshot)
        history = list(session.scalars(select(VariantGeneration).where(
            VariantGeneration.tenant_id == student.tenant_id, VariantGeneration.student_id == student_id,
            VariantGeneration.source_case_id == payload.source_case_id)
            .order_by(VariantGeneration.created_at.desc(), VariantGeneration.id.desc())))
        row = None
        if payload.regenerate_token is None:
            # Resume the newest usable generation, including explicit new
            # questions. A failed newer job must not hide a verified item.
            for previous in history:
                if previous.knowledge_id != snapshot["knowledge_id"] or not snapshot_current(session, previous):
                    continue
                status = generation_dict(session, previous)["status"]
                if status in ("queued", "generating", "verifying") or (status == "ready" and _verified_item(session, previous)):
                    row = previous
                    break
        if row is None:
            row = next((previous for previous in history if previous.source_fingerprint == fingerprint), None)
        if row is not None and (row.status == "superseded" or not snapshot_current(session, row)
                                or (row.status == "ready" and not _verified_item(session, row))):
            raise _problem("stale_source", "本次生成题目已失效，请重新选择生成新题。")
        if row is None:
            from .device_auth import utc
            stamp = _now()
            if history:
                stamp = max(stamp, utc(history[0].created_at) + timedelta(microseconds=1))
            row = VariantGeneration(id=new_id("variant"), tenant_id=student.tenant_id, student_id=student.id,
                  source_case_id=payload.source_case_id, source_fingerprint=fingerprint,
                  graph_version=snapshot["graph_version"], knowledge_id=snapshot["knowledge_id"],
                  question_type=snapshot["question_type"], question_kind=snapshot["question_kind"], snapshot=snapshot,
                  status="queued", audit={}, created_at=stamp, updated_at=stamp)
            session.add(row)
        if row.outbox_id and row.status == "queued":
            outbox = session.get(LearningOutbox, row.outbox_id)
            if outbox and outbox.status == "dead":
                row.status = "failed"
        if row.outbox_id is None or row.status == "failed":
            row.status, row.failure_code = "queued", None
            row.outbox_id = enqueue(session, row.tenant_id, row.student_id, "generate_student_variant",
                                    {"generation_id": row.id}, f"student-variant:{row.id}")
            row.updated_at = _now()
        session.flush()
        result = generation_dict(session, row)
        session.commit()
        return result

    @app.get("/v1/variant-generations/{generation_id}", operation_id="getStudentVariantGeneration")
    def get(generation_id: str, actor: ActorDep, session: SessionDep):
        return generation_dict(session, load_generation(session, actor, generation_id))

    @app.get("/v1/students/{student_id}/variant-generations", operation_id="listStudentVariantGenerations")
    def listing(student_id: str, actor: ActorDep, session: SessionDep):
        require_student(session, actor, student_id)
        rows = session.scalars(select(VariantGeneration).where(VariantGeneration.student_id == student_id,
                    VariantGeneration.tenant_id == actor.tenant_id).order_by(VariantGeneration.created_at.desc()).limit(20))
        return {"student_id": student_id, "items": [generation_dict(session, row) for row in rows]}

    @app.get("/v1/practice-assessments/{assessment_id}", operation_id="getPracticeAssessment")
    def assessment(assessment_id: str, actor: ActorDep, session: SessionDep):
        row = session.get(PracticeAssessment, assessment_id)
        if row is None or row.tenant_id != actor.tenant_id:
            raise _problem("not_found", "核验记录不存在或不可访问。", 404)
        require_student(session, actor, row.student_id)
        return assessment_dict(session, row)


def _verified_item(session, row):
    item = session.get(PracticeItem, row.item_id, populate_existing=True) if row.item_id else None
    if (not item or item.tenant_id != row.tenant_id or item.owner_student_id != row.student_id
            or item.graph_version != row.graph_version or item.knowledge_id != row.knowledge_id
            or item.status != "ai_verified" or (item.verification or {}).get("status") != "verified"
            or (item.generation or {}).get("job_id") != row.id
            or (item.generation or {}).get("source_fingerprint") != row.source_fingerprint):
        return None
    return item


def _session_matches_generation(activity, generation):
    return bool(activity and activity.purpose == "variant"
        and activity.tenant_id == generation.tenant_id and activity.student_id == generation.student_id
        and activity.source_case_id == generation.source_case_id and activity.graph_version == generation.graph_version
        and activity.knowledge_ids == ([generation.knowledge_id] if generation.knowledge_id else [])
        and activity.item_ids == [generation.item_id]
        and activity.gate.get("generation_job_id") == generation.id)


def upgrade_generated_session(session, activity):
    """Upgrade only an untouched, still-valid legacy notebook session."""
    if (activity.purpose != "variant" or activity.status not in ("active", "time_up")
            or activity.exit_reason or not activity.gate.get("generation_job_id")
            or activity.gate.get("no_expiry") is True):
        return
    generation = session.get(VariantGeneration, activity.gate["generation_job_id"])
    if (not generation or generation.status != "ready" or generation.session_id != activity.id
            or not _session_matches_generation(activity, generation)):
        return
    lock_source(session, generation)
    session.scalar(select(VariantGeneration).where(VariantGeneration.id == generation.id)
                   .with_for_update().execution_options(populate_existing=True))
    session.scalar(select(PracticeSession).where(PracticeSession.id == activity.id)
                   .with_for_update().execution_options(populate_existing=True))
    # Recheck after the lock: an answer may have committed while we waited.
    if (generation.status != "ready" or generation.session_id != activity.id
            or activity.status not in ("active", "time_up") or activity.exit_reason
            or activity.gate.get("no_expiry") is True or not _session_matches_generation(activity, generation)
            or session.scalar(select(PracticeResponse.id).where(PracticeResponse.session_id == activity.id))
            or not snapshot_current(session, generation) or not _verified_item(session, generation)):
        return
    activity.gate = {**activity.gate, "no_expiry": True}
    activity.status, activity.ended_at = "active", None


def _new_generated_session(session, actor, row, item, *, previous=None):
    stamp = _now()
    activity = PracticeSession(id=new_id("practice"), tenant_id=row.tenant_id, student_id=row.student_id,
              graph_version=row.graph_version, purpose="variant", knowledge_ids=[row.knowledge_id] if row.knowledge_id else [],
              source_case_id=row.source_case_id, item_ids=[item.id], hints_used=list(previous.hints_used) if previous else [],
              gate={"generation_job_id": row.id, "direct_variant": True, "no_expiry": True,
                    **({"retry_of_session_id": previous.id} if previous else {}),
                    "child_willing": {"value": True, "source": "student", "actor": actor.id}},
              status="active", started_at=stamp, budget_ends_at=stamp + timedelta(minutes=10))
    session.add(activity)
    row.audit = {**row.audit, "source_help_at_start": _current_help_context(session, row)}
    row.session_id = activity.id
    session.flush()
    return activity


def start_generated(session, actor, student, generation_id, *, source_case_id=None, knowledge_id=None):
    from .practice import _expire, session_dict
    row = load_generation(session, actor, generation_id)
    if row.student_id != student.id or row.tenant_id != student.tenant_id:
        raise _problem("not_found", "任务不存在或不可访问。", 404)
    # Validate these even when an existing session can be returned.
    if ((source_case_id is not None and source_case_id != row.source_case_id)
            or (knowledge_id is not None and knowledge_id != row.knowledge_id)):
        raise _problem("stale_source", "练习来源不一致，请刷新。")
    lock_source(session, row)
    if row.status != "ready" or not snapshot_current(session, row):
        raise _problem("stale_source", "原题或教材已更新，或新题尚未通过核验。")
    item = _verified_item(session, row)
    if item is None:
        raise _problem("stale_source", "本次生成题目已失效。")
    if row.session_id:
        previous = session.get(PracticeSession, row.session_id)
        if not _session_matches_generation(previous, row):
            raise _problem("stale_source", "本次练习记录与来源不一致。")
        _expire(previous, _now())
        return session_dict(session, previous)
    activity = _new_generated_session(session, actor, row, item)
    return session_dict(session, activity)


def retry_generated(session, actor, activity):
    from .practice import session_dict
    generation_id = activity.gate.get("generation_job_id") if activity.purpose == "variant" else None
    if not generation_id:
        raise _problem("retry_not_available", "只有已核验的举一反三错题可以重新作答。")
    generation = load_generation(session, actor, generation_id)
    if not _session_matches_generation(activity, generation):
        raise _problem("stale_source", "本次练习记录与来源不一致。")
    lock_source(session, generation)
    if generation.status != "ready" or not snapshot_current(session, generation):
        raise _problem("stale_source", "原题或教材已更新，请重新选择。")
    item = _verified_item(session, generation)
    if item is None:
        raise _problem("stale_source", "本次生成题目已失效。")
    response = session.scalar(select(PracticeResponse).where(PracticeResponse.session_id == activity.id,
        PracticeResponse.item_id == item.id, PracticeResponse.student_id == activity.student_id,
        PracticeResponse.tenant_id == activity.tenant_id))
    if activity.status != "completed" or not response or response.result not in ("incorrect", "unscorable"):
        raise _problem("retry_not_available", "请等待本次核验完成；答错或无法核验的已完成练习可以重做。")
    retry_id = activity.gate.get("retry_session_id")
    if retry_id:
        retried = session.get(PracticeSession, retry_id)
        if not _session_matches_generation(retried, generation) or retried.gate.get("retry_of_session_id") != activity.id:
            raise _problem("stale_source", "重做记录与来源不一致。")
    else:
        retried = _new_generated_session(session, actor, generation, item, previous=activity)
        activity.gate = {**activity.gate, "retry_session_id": retried.id}
    return session_dict(session, retried)


def grade_generated(item, payload, client=None, *, rule_only=False):
    """Rule-grade safe scalar/option values; use AI for semantics, never string inequality."""
    from .practice import _number
    data = payload.model_dump(mode="json")
    text = payload.answer_text or ""
    if item.question_kind == "choice":
        labels = {o["label"] for o in item.options or []}
        given = payload.selected_options
        if given is None:
            given = [c for c in text.strip().upper() if not c.isspace() and c not in ",，、"]
        if not given or len(given) != len(set(given)) or not set(given) <= labels:
            raise Problem(422, "validation", "invalid_request", "请选择题目提供的有效选项。")
        if item.question_type == "single_choice" and len(given) != 1:
            raise Problem(422, "validation", "invalid_request", "这道题只能选一个选项。")
        return {"result": "correct" if set(given) == set(item.answer_values) else "incorrect", "feedback": "已核对本次选择。"}, data
    if item.question_kind == "fill_blank":
        fields = {f["field_id"]: f for f in item.field_answers or []}
        given = {a.field_id: a.answer_text for a in payload.answers or []}
        if not given and len(fields) == 1 and text.strip():
            given = {next(iter(fields)): text}
        if set(given) != set(fields) or (payload.answers and len(given) != len(payload.answers)):
            raise Problem(422, "validation", "invalid_request", "请逐空填写，空位不能遗漏或重复。")
        results = []
        for field_id, answer in given.items():
            expected = fields[field_id]
            number = _number(answer)
            values = [_number(v) for v in expected["answer_values"]]
            results.append(number in values if expected["answer_kind"] == "number" and number is not None and None not in values else None)
        if False in results or None not in results:
            return {"result": "incorrect" if False in results else "correct", "feedback": "已逐空核对本次作答。"}, data
    elif not text.strip():
        raise Problem(422, "validation", "invalid_request", "请填写本次作答。")
    if rule_only:
        return None, data
    owned = False
    calls = []
    try:
        if client is None or not client.settings.enabled:
            raise ProviderError("provider_disabled")
        bounded, new = bounded_client(client)
        if new:
            if owned:
                client.close()
            client, owned = bounded, True
        result = _call(client, "grade_optional_answer", {
            "question": {"stem_text": item.stem_text, "question_type": item.question_type,
                         "options": item.options or [], "answer_fields": item.answer_fields or []},
            "independently_verified_reference": {"answer_values": item.answer_values,
                          "field_answers": item.field_answers or [], "solution_text": item.solution_text},
            "student_submission": data}, GRADE_SCHEMA, GRADE_PROMPT, calls)
        return result, {**data, "verification_calls": calls}
    except ProviderError as error:
        calls.append(_failure_audit(error, client))
        return {"result": "unscorable", "feedback": "本次暂未完成核验，请稍后重试。"}, {**data, "verification_calls": calls}
    finally:
        if owned:
            client.close()


def _failure_audit(error, client):
    audit = {"stage": "failure", "code": error.code, "latency_ms": error.latency_ms,
             "attempts": [asdict(attempt) for attempt in error.attempts]}
    secret = getattr(getattr(client, "settings", None), "api_key", "")
    return {"stage": "failure", "code": "invalid_output"} if secret and _contains_credential(audit, secret) else audit


def _item_digest(item):
    return _digest({k: getattr(item, k) for k in ("id", "tenant_id", "owner_student_id", "graph_version", "knowledge_id",
        "revision", "status", "stem_text", "question_type", "question_kind", "options", "answer_fields", "answer_kind",
        "answer_values", "field_answers", "solution_text", "verification")})


def assessment_dict(session, row):
    status = row.status
    if status == "queued" and row.outbox_id:
        job = session.get(LearningOutbox, row.outbox_id)
        if job and job.status == "running":
            status = "grading"
        elif job and job.status == "dead":
            status = "failed"
    item = session.get(PracticeItem, row.item_id)
    result = row.result if status == "completed" else "unscorable" if status in ("failed", "superseded") else None
    return {"assessment_id": row.id, "student_id": row.student_id, "session_id": row.session_id, "item_id": row.item_id,
            "submission": {k: row.submission.get(k) for k in ("answer_text", "selected_options", "answers")},
            "status": status, "result": result, "feedback": row.feedback or (
                "本次暂未完成核验，请稍后重试。" if status == "failed" else "正在核验本次作答。"),
            "solution_text": item.solution_text if item and status == "completed" else None,
            "retryable": status == "failed"}


def queue_assessment(session, activity, item, payload):
    pending = session.scalar(select(PracticeAssessment).where(PracticeAssessment.session_id == activity.id,
              PracticeAssessment.item_id == item.id, PracticeAssessment.status.in_(("queued", "grading")))
              .order_by(PracticeAssessment.created_at.desc()))
    if pending:
        job = session.get(LearningOutbox, pending.outbox_id)
        if job and job.status not in ("dead", "succeeded"):
            if pending.submission != payload.model_dump(mode="json"):
                raise _problem("assessment_pending", "这道题的上次作答正在核验，请等待结果后再操作。")
            return {"item_id": item.id, "result": "pending", "assessment_id": pending.id,
                    "session_status": activity.status, "feedback": "正在核验本次作答。", "solution_text": None}
        pending.status, pending.result = "failed", "unscorable"
    row = PracticeAssessment(id=new_id("assessment"), tenant_id=activity.tenant_id, student_id=activity.student_id,
        session_id=activity.id, item_id=item.id, graph_version=item.graph_version, item_revision=item.revision,
        item_digest=_item_digest(item), submission=payload.model_dump(mode="json"), status="queued", audit={},
        created_at=_now(), updated_at=_now())
    session.add(row)
    row.outbox_id = enqueue(session, row.tenant_id, row.student_id, "assess_student_practice", {"assessment_id": row.id},
                            f"practice-assessment:{row.id}")
    session.flush()
    return {"item_id": item.id, "result": "pending", "assessment_id": row.id,
            "session_status": activity.status, "feedback": "正在核验本次作答。", "solution_text": None}


def _assessment_context(session, row, *, lock=False):
    def load(model, identifier):
        query = select(model).where(model.id == identifier).execution_options(populate_existing=True)
        return session.scalar(query.with_for_update() if lock else query)
    student = load(Student, row.student_id)
    activity = load(PracticeSession, row.session_id)
    item = load(PracticeItem, row.item_id)
    if (not student or not student.active or student.tenant_id != row.tenant_id or not activity or not item
            or activity.tenant_id != row.tenant_id or activity.student_id != row.student_id
            or activity.status != "active" or row.item_id not in activity.item_ids
            or item.tenant_id != row.tenant_id or item.owner_student_id != row.student_id or item.status != "ai_verified"
            or item.graph_version != row.graph_version or item.revision != row.item_revision
            or activity.graph_version != row.graph_version or _item_digest(item) != row.item_digest
            or session.scalar(select(PracticeResponse.id).where(PracticeResponse.session_id == activity.id,
                                                               PracticeResponse.item_id == item.id))):
        return None
    return activity, item


@register_job_handler("assess_student_practice")
def assess_student_practice(session, job, worker):
    from .practice import AnswerInput
    row = session.get(PracticeAssessment, job.payload.get("assessment_id"))
    if not row or row.tenant_id != job.tenant_id or row.student_id != job.student_id or row.outbox_id != job.id:
        return {"status": "obsolete"}
    if row.status == "completed":
        return {"status": "completed"}
    context = _assessment_context(session, row)
    if context is None:
        row.status, row.result, row.feedback = "superseded", "unscorable", "活动或题目已更新，本次核验不计入结果。"
    elif job.tries > 1:
        row.status, row.result, row.feedback = "failed", "unscorable", "本次核验曾中断，请明确重试。"
    else:
        activity, item = context
        grading, details = grade_generated(item, AnswerInput.model_validate(row.submission), worker.client)
        row.audit = {"calls": details.pop("verification_calls", [])}
        context = _assessment_context(session, row, lock=True)
        if context is None:
            row.status, row.result, row.feedback = "superseded", "unscorable", "活动或题目已更新，本次核验不计入结果。"
        elif grading["result"] == "unscorable":
            row.status, row.result, row.feedback = "failed", "unscorable", grading["feedback"]
        else:
            activity, item = context
            submitted = row.submission
            text = submitted.get("answer_text") or "\n".join(
                f"{a['field_id']}: {a['answer_text']}" for a in submitted.get("answers") or [])
            stamp = _now()
            session.add(PracticeResponse(id=new_id("presp"), tenant_id=row.tenant_id, student_id=row.student_id,
                session_id=activity.id, item_id=item.id, knowledge_id=item.knowledge_id, graph_version=row.graph_version,
                event_type="optional_variant", answer_text=text, result=grading["result"], hint_used=item.id in activity.hints_used,
                details={**submitted, "feedback": grading["feedback"], "assessment_id": row.id}, occurred_at=stamp))
            session.flush()
            count = session.scalar(select(func.count()).select_from(PracticeResponse).where(PracticeResponse.session_id == activity.id))
            if count == len(activity.item_ids):
                activity.status, activity.ended_at = "completed", stamp
            row.status, row.result, row.feedback = "completed", grading["result"], grading["feedback"]
    row.updated_at = _now()
    return {"status": row.status, "result": row.result}
