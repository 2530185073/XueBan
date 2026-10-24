"""Seed a fully fictional showcase student whose knowledge map is rich, for demos and training.

Honesty rules (also printed on the student's name and in every record's annotations):
* Everything is invented. The student is named "演示学员（虚构数据）" and lives in its own tenant
  ``demo_showcase`` so it can never appear next to a real learner.
* Only raw facts are written (homeworks, questions, attempts, decisions, hints, links, conditions).
  Evidence and knowledge states are computed by the real ``project_student`` rules, never inserted.
* Teacher-verified decisions are written under a clearly named demo teacher, flagged ``demo_fixture``.
* No model is called and no real photo is used; images are generated placeholders.

    seed_showcase.py plan             # print the script of events, change nothing
    seed_showcase.py seed             # create tenant/accounts and write the events (idempotent)
    seed_showcase.py verify           # recompute and print the resulting status distribution
"""
import argparse
import hashlib
import io
import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__:
    from .mac_local import BACKEND, load
else:
    from mac_local import BACKEND, load
sys.path.insert(0, str(BACKEND))

TENANT, CAMPUS = "demo_showcase", "demo_showcase_campus"
GRAPH = "pep-math-g7-up-9787107382468-r3"
USERNAME = "demo.showcase.student"
TEACHER_USERNAME = "demo.showcase.teacher"
STUDENT_NAME = "演示学员（虚构数据）"
FIXTURE = "showcase-20261022"

# Mastery stories, expressed as the evidence the real rules turn into a status.
#  stable        2+ teacher-verified independent successes, on 2+ days, 2+ question families, >=24h apart
#  consolidating one independent success, or a correction made with help
#  needs_help    one or two errors, no later success
#  none          no records at all (honest: "no record" is not "cannot do")
# (kind, days_ago, hours_after_day_start). A correction is always later than the error it fixes.
STORY = {
    "stable": [("ok", 9, 0), ("ok", 5, 0), ("ok", 2, 0)],
    "stable2": [("ok", 7, 0), ("ok", 3, 0)],
    "improving": [("bad", 8, 0), ("fix", 8, 2), ("ok", 4, 0), ("ok", 1, 0)],
    "consolidating": [("ok", 2, 0)],
    "fixed": [("bad", 4, 0), ("fix", 4, 2)],
    "needs_help": [("bad", 3, 0)],
    "struggling": [("bad", 6, 0), ("bad", 2, 0)],
    "none": [],
}


def plan_for(skills):
    """Map every skill id to a story so each chapter has a believable shape."""
    ch = {}
    for s in skills:
        ch.setdefault(s["ch"], []).append(s["id"])
    names = sorted(ch)
    out = {}
    # Each chapter tells a different story, so the map teaches something instead of being uniformly green.
    shapes = [
        # 1 有理数: the foundation is secure
        ["stable", "stable", "stable2", "stable", "improving", "stable2", "stable"],
        # 2 有理数的运算: strong core with a classic weak spot, sign handling in division and powers
        ["stable", "stable2", "improving", "struggling", "needs_help", "stable", "stable2", "fixed", "stable", "consolidating", "needs_help", "stable2", "improving"],
        # 3 代数式: mostly solid
        ["stable2", "stable", "improving", "stable", "needs_help", "consolidating", "stable2", "consolidating", "stable"],
        # 4 整式的加减: bracket removal is shaky, the rest is good
        ["stable", "stable2", "stable", "improving", "consolidating", "struggling", "needs_help", "consolidating"],
        # 5 一元一次方程: the current frontier, many in progress
        ["stable2", "stable", "improving", "consolidating", "needs_help", "consolidating", "fixed", "consolidating", "stable2", "consolidating",
         "consolidating", "improving", "none", "consolidating", "none", "none", "none", "none", "none"],
        # 6 几何图形初步: just started
        ["stable2", "stable", "consolidating", "improving", "consolidating", "fixed", "consolidating", "none", "none", "consolidating",
         "none", "none", "none", "none", "none", "none", "none", "none", "none"],
    ]
    for index, chapter in enumerate(names):
        for pos, skill_id in enumerate(ch[chapter]):
            shape = shapes[index] if index < len(shapes) else ["none"]
            out[skill_id] = shape[pos] if pos < len(shape) else "none"
    return out


def load_skills(session=None):
    """The published graph is the single source: skills come from the database, not from a file."""
    from sqlalchemy import select
    from xueban_backend.learning_models import KnowledgeNode
    rows = session.scalars(select(KnowledgeNode).where(KnowledgeNode.graph_version == GRAPH, KnowledgeNode.node_type == "skill")
                           .order_by(KnowledgeNode.sort_order, KnowledgeNode.id)).all()
    return [{"id": r.id, "ch": r.parent_id, "name": r.name} for r in rows]


# ---- fictional question content: a bank per chapter, so a question always fits its topic ----
# (stem, wrong answer a student plausibly writes, right answer)
QUESTION_BANK = {
    "pep-g7-up-ch1": [("把 -3、0、2.5、-1/2 按从小到大排列", "0, -1/2, -3, 2.5", "-3, -1/2, 0, 2.5"), ("|-7| 等于多少？", "-7", "7"),
                      ("-5 的相反数是多少？", "-5", "5"), ("数轴上到原点距离为 4 的点表示的数是？", "4", "4 或 -4")],
    "pep-g7-up-ch2": [("计算：(-18) ÷ 9", "2", "-2"), ("计算：(-3) × (-4)", "-12", "12"), ("计算：-2 - (-5)", "-7", "3"),
                      ("计算：(-2)³", "6", "-8"), ("计算：6 ÷ (-1/3)", "-2", "-18")],
    "pep-g7-up-ch3": [("当 a = -2 时，求 3a + 1 的值", "7", "-5"), ("用字母表示“比 x 的 2 倍少 3 的数”", "2(x - 3)", "2x - 3"),
                      ("a = 3, b = -1，求 a² - b 的值", "8", "10"), ("长为 a 宽为 b 的长方形，周长是？", "ab", "2(a + b)")],
    "pep-g7-up-ch4": [("化简：3x + 2x", "5x²", "5x"), ("化简：2(x - 3)", "2x - 3", "2x - 6"), ("化简：-(a - b)", "-a - b", "-a + b"),
                      ("合并同类项：4a - 7a", "3a", "-3a")],
    "pep-g7-up-ch5": [("解方程：2x + 3 = 11", "x = 7", "x = 4"), ("解方程：3x - 5 = 4", "x = -3", "x = 3"), ("解方程：x/2 = 6", "x = 3", "x = 12"),
                      ("列方程：某数的 3 倍比它大 8，求这个数", "3x + 8 = x", "3x - x = 8，x = 4")],
    "pep-g7-up-ch6": [("一条直线上有 A、B、C 三点，AB = 5，BC = 3，求 AC（C 在 B 右侧）", "2", "8"), ("30° 角的补角是多少度？", "60°", "150°"),
                      ("射线与直线的区别是什么？", "射线更长", "射线有一个端点，直线没有端点"), ("∠1 = 25°，其余角是多少度？", "155°", "65°")],
}


def question_for(skill, k):
    bank = QUESTION_BANK.get(skill["ch"]) or QUESTION_BANK["pep-g7-up-ch2"]
    return bank[k % len(bank)]


def placeholder_png(label, width=1200, height=560):
    """A generated worksheet image. Clearly synthetic: no real handwriting, no student photo."""
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (width, height), "#fbfbf6")
    draw = ImageDraw.Draw(image)
    draw.rectangle((24, 24, width - 24, height - 24), outline="#9fb0d8", width=3)
    draw.text((56, 52), "DEMO · fictional worksheet, not a real student record", fill="#6b7ca8")
    draw.text((56, 140), label, fill="#1d2a52")
    for y in range(230, height - 60, 52):
        draw.line((56, y, width - 56, y), fill="#d5ddf0", width=2)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue(), width, height


def build_events(plan, skills, now):
    """Pure: turn the story plan into dated events. Nothing here touches a database."""
    events = []
    for k, skill in enumerate(skills):
        story = plan.get(skill["id"], "none")
        for step, (kind, days, hours) in enumerate(STORY[story]):
            when = (now - timedelta(days=days)).replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(hours=hours, minutes=k % 50)
            events.append({"skill": skill["id"], "k": k, "step": step, "kind": kind, "when": when, "story": story})
    events.sort(key=lambda e: (e["when"], e["skill"]))
    return events


def bind_textbook(session, student_id, tpid_hint):
    """Same document the curriculum-books endpoint stores: grade 7 plus the R3 textbook graph."""
    from xueban_backend.learning_models import CurriculumGraph, StudentLearningProfile
    graph = session.get(CurriculumGraph, GRAPH)
    if graph is None or graph.status != "published" or not graph.book_identity:
        raise SystemExit("the R3 textbook graph is not published in this database")
    stamp = datetime.now(timezone.utc)
    binding = {**graph.book_identity, "graph_version": graph.version}
    document = {"grade": 7, "textbook_revision": "isbn:" + binding["isbn"], "curriculum_binding": binding,
                "curriculum_binding_history": [{"previous_graph_version": None, "graph_version": graph.version, "actor_id": tpid_hint, "changed_at": stamp.isoformat()}],
                "current_knowledge_ids": [], "note": None}
    session.add(StudentLearningProfile(student_id=student_id, tenant_id=TENANT, revision=1, document=document, updated_by=tpid_hint, updated_at=stamp))
    session.flush()


def ensure_accounts(session, cfg, passwords):
    """Tenant, campus, a demo teacher and the demo student. Idempotent; passwords are only set on first creation."""
    from xueban_backend.account_auth import set_password_account
    from xueban_backend.account_models import PrincipalCampusGrant
    from xueban_backend.models import Campus, Principal, PrincipalRole, Student, StudentGrant, Tenant
    created = {}
    if session.get(Tenant, TENANT) is None:
        session.add(Tenant(id=TENANT)); session.flush()
    if session.get(Campus, CAMPUS) is None:
        session.add(Campus(id=CAMPUS, tenant_id=TENANT)); session.flush()
    student_id = "student_demo_showcase"
    if session.get(Student, student_id) is None:
        session.add(Student(id=student_id, tenant_id=TENANT, campus_id=CAMPUS, display_name=STUDENT_NAME, active=True)); session.flush()
        spid = "actor_demo_showcase_student"
        session.add(Principal(id=spid, tenant_id=TENANT, issuer=cfg.oidc_issuer, subject=spid, active=True)); session.flush()
        session.add(PrincipalRole(principal_id=spid, role="student"))
        session.add(StudentGrant(principal_id=spid, student_id=student_id, tenant_id=TENANT, active=True)); session.flush()
        set_password_account(session, cfg, principal_id=spid, username_value=USERNAME, password_value=passwords["student"], student_id=student_id, must_change=False)
        created["student"] = USERNAME
        bind_textbook(session, student_id, tpid_hint="actor_demo_showcase_teacher")
    tpid = "actor_demo_showcase_teacher"
    if session.get(Principal, tpid) is None:
        session.add(Principal(id=tpid, tenant_id=TENANT, issuer=cfg.oidc_issuer, subject=tpid, active=True)); session.flush()
        session.add(PrincipalRole(principal_id=tpid, role="sitting_teacher")); session.flush()
        session.add(PrincipalCampusGrant(principal_id=tpid, campus_id=CAMPUS, tenant_id=TENANT, active=True)); session.flush()
        session.add(StudentGrant(principal_id=tpid, student_id=student_id, tenant_id=TENANT, active=True)); session.flush()
        set_password_account(session, cfg, principal_id=tpid, username_value=TEACHER_USERNAME, password_value=passwords["teacher"], must_change=False)
        created["teacher"] = TEACHER_USERNAME
    return student_id, tpid, created


def write_event(session, contracts, store, ctx, event, skill):
    """One school question, fully linked: homework, asset, question, attempt, check, decision (+hint), link, condition."""
    from xueban_backend.correction_core import iso, new_id, save_entity
    from xueban_backend.correction_models import Asset, Task
    from xueban_backend.learning_models import AttemptCondition, QuestionKnowledgeLink
    from xueban_backend.models import Homework
    sid, tpid, at = ctx["student_id"], ctx["teacher_id"], event["when"]
    stem, wrong_answer, right_answer = question_for(skill, event["k"])
    qtype = "calculation"
    wrong = event["kind"] == "bad"
    hid = new_id("homework")
    session.add(Homework(id=hid, tenant_id=TENANT, campus_id=CAMPUS, student_id=sid, revision=1,
                         curriculum={"stage": "junior_middle", "publisher_code": "PEP", "grade": 7, "subject": "数学", "volume": None, "edition": None},
                         title=f"演示作业 · {skill['name']}", collection_scope="partial", created_at=at, occurred_at=at))
    session.flush()
    content, width, height = placeholder_png(f"{stem}   (demo)")
    asset_id = new_id("asset")
    adoc = {"page_index": 0, "sha256": hashlib.sha256(content).hexdigest(), "byte_size": len(content), "mime_type": "image/png", "width": width,
            "height": height, "id": asset_id, "revision": 2, "homework_id": hid, "status": "uploaded", "source_asset_id": None, "source_region": None, "created_at": iso(at)}
    store.put(asset_id, content, adoc)
    session.add(Asset(id=asset_id, homework_id=hid, revision=2, document=adoc)); session.flush()
    region = {"asset_id": asset_id, "x": 0, "y": 0, "width": width, "height": height, "coordinate_space": "original_pixels"}
    qid, cid, aid, chk, job_id = new_id("question"), new_id("case"), new_id("attempt"), new_id("check"), new_id("job")
    answer = wrong_answer if wrong else right_answer
    question = {"question_id": qid, "revision": 1, "homework_id": hid, "label": "1", "stem_text": stem, "question_type": qtype, "subject": "数学",
                "regions": [region], "source": "staff_layout", "source_actor_id": tpid, "bank_question_id": None, "knowledge_point_ids": None,
                "created_at": iso(at), "lifecycle": "active", "replacement_question_ids": []}
    attempt = {"id": aid, "revision": 1, "case_id": cid, "question_id": qid, "captured_question_revision": 1, "parent_attempt_id": None, "root_attempt_id": aid,
               "asset_ids": [asset_id], "recognition_revision": 1, "answer_text": answer, "answer_regions": [region], "answer_status": "present",
               "recognition_uncertainties": [], "recognition_status": "ready", "help_condition": "none_reported", "occurred_at": iso(at), "created_at": iso(at)}
    versions = {"question_id": qid, "question_revision": 1, "attempt_id": aid, "attempt_recognition_revision": 1, "case_revision_at_submission": 1,
                "fingerprint": hashlib.sha256((qid + aid).encode()).hexdigest()}
    verdict = "incorrect" if wrong else "correct"
    did = new_id("decision")
    decision = {"id": did, "revision": 1, "case_id": cid, "check_id": chk, "input_versions": versions, "origin": "staff", "validity": "confirmed",
                "verdict": verdict, "reason_code": None, "basis": [{"kind": "staff_observation", "summary": "演示数据：老师核对原图后确认。", "source_ref": FIXTURE, "regions": []}],
                "first_error_region": None, "confirmation_source": "staff_review", "confirmed_by": tpid, "confirmed_at": iso(at + timedelta(minutes=20)),
                "replaces_decision_id": None, "invalidated_by_event_id": None, "created_at": iso(at + timedelta(minutes=20))}
    check = {"id": chk, "revision": 1, "job_id": job_id, "case_id": cid, "input_versions": versions, "model_version": FIXTURE, "rule_version": None,
             "prompt_version": None, "decision_ids": [did], "created_at": iso(at + timedelta(minutes=19))}
    case = {"id": cid, "revision": 1, "scope": {"tenant_id": TENANT, "campus_id": CAMPUS, "student_id": sid}, "homework_id": hid, "question_id": qid,
            "current_question_revision": 1, "root_attempt_id": aid, "current_attempt_id": aid, "progress": "verified_resolved" if not wrong else "needs_correction",
            "current_decision": decision, "attempt_ids": [aid], "check_ids": [chk], "hint_ids": [], "help_request_ids": [], "review_ids": [],
            "created_at": iso(at), "updated_at": iso(at + timedelta(minutes=20)), "lifecycle": "active", "replacement_case_ids": []}
    done = at + timedelta(minutes=20)
    job = {"id": job_id, "revision": 3, "kind": "check", "status": "succeeded", "created_at": iso(at), "updated_at": iso(done),
           "retry_after_seconds": None, "failure": None, "result": {"resource_type": "check", "resource_id": chk}}
    session.add(Task(id=job_id, homework_id=hid, revision=3, kind="check", status="succeeded", document=job,
                     inputs={"case_id": cid, "check_id": chk, "input_versions": versions}, audit={"demo_fixture": FIXTURE},
                     tries=1, available_at=at))
    session.flush()
    for kind, doc in (("question", question), ("attempt", attempt), ("case", case), ("check", check), ("decision", decision)):
        row = save_entity(session, contracts, hid, kind, doc)
        row.annotations = {"demo_fixture": FIXTURE, "fictional": True}
    session.add(QuestionKnowledgeLink(id=new_id("link"), tenant_id=TENANT, student_id=sid, homework_id=hid, question_id=qid, question_revision=1,
        knowledge_id=skill["id"], graph_version=GRAPH, role="primary", source="staff", status="confirmed", rationale="演示数据：老师确认的知识点关联。",
        actor_id=tpid, created_at=at))
    if event["kind"] == "ok":
        session.add(AttemptCondition(id=new_id("condition"), tenant_id=TENANT, student_id=sid, attempt_id=aid, assistance_level="independent",
                                     note="演示数据：独立完成。", actor_id=tpid, created_at=at + timedelta(minutes=21)))
    return {"homework": hid, "case": cid, "attempt": aid, "decision": did}


def run(command, runtime):
    from collections import Counter
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from xueban_backend.asset_store import LocalAssetStore
    from xueban_backend.config import Settings
    from xueban_backend.contracts import Contracts
    from xueban_backend.db import create_database_engine
    from xueban_backend.learning_models import LearningEvidence, StudentKnowledgeState
    from xueban_backend.learning_projection import project_student
    _, env = load(runtime)
    os.environ.update(env)
    cfg = Settings.from_environment()
    engine = create_database_engine(cfg.database_url)
    with Session(engine) as session:
        skills = load_skills(session)
    plan = plan_for(skills)
    events = build_events(plan, skills, datetime.now(timezone.utc))
    by_id = {s["id"]: s for s in skills}
    if command == "plan":
        print(json.dumps({"skills": len(skills), "stories": dict(Counter(plan.values())), "events": len(events)}, ensure_ascii=False))
        return
    if command == "verify":
        with Session(engine) as session:
            states = Counter(r.document["learning_status"] for r in session.scalars(select(StudentKnowledgeState).where(StudentKnowledgeState.student_id == "student_demo_showcase")))
            evidence = session.scalar(select(__import__("sqlalchemy").func.count()).select_from(LearningEvidence).where(LearningEvidence.student_id == "student_demo_showcase", LearningEvidence.validity == "valid"))
        print(json.dumps({"states": dict(states), "valid_evidence": evidence}, ensure_ascii=False))
        return
    contracts, store = Contracts(cfg.contracts_dir), LocalAssetStore(cfg.asset_storage_dir)
    credentials = Path(runtime) / "demo-showcase-accounts.json"
    if credentials.exists():
        passwords = json.loads(credentials.read_text())
    else:
        passwords = {"student": secrets.token_urlsafe(24), "teacher": secrets.token_urlsafe(24)}
    with Session(engine) as session, session.begin():
        student_id, teacher_id, created = ensure_accounts(session, cfg, passwords)
        existing = session.scalar(select(__import__("sqlalchemy").func.count()).select_from(__import__("xueban_backend.models", fromlist=["Homework"]).Homework).where(
            __import__("xueban_backend.models", fromlist=["Homework"]).Homework.student_id == student_id))
        if existing:
            print(json.dumps({"status": "already_seeded", "homeworks": existing}))
            return
        ctx = {"student_id": student_id, "teacher_id": teacher_id}
        for event in events:
            write_event(session, contracts, store, ctx, event, by_id[event["skill"]])
        projected = project_student(session, TENANT, student_id)
    if created:
        fd = os.open(credentials, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump({**passwords, "student_username": USERNAME, "teacher_username": TEACHER_USERNAME}, file)
    print(json.dumps({"status": "seeded", "events": len(events), "projection": projected, "accounts": sorted(created)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("plan", "seed", "verify"))
    parser.add_argument("--runtime", type=Path, default=Path.home() / ".local/share/xueban/local-dev")
    args = parser.parse_args()
    run(args.command, args.runtime)


if __name__ == "__main__":
    main()
