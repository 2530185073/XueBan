"""Student-first entry locks for short correction/learning write transactions.

Locate ownership with unlocked reads, then serialize the student before task,
homework or correction-entity locks. Callers recheck ownership and eligibility
after acquiring their child locks; the lookup is never authorization evidence.
"""
from sqlalchemy import select

from .correction_models import Task
from .models import Homework, Student


def lock_student(session, student_id, *, tenant_id=None):
    query = select(Student).where(Student.id == student_id)
    if tenant_id is not None:
        query = query.where(Student.tenant_id == tenant_id)
    return session.scalar(query.with_for_update().execution_options(populate_existing=True))


def lock_task_student(session, task_id):
    """Return a locked ownership scope without locking the task first.

    Claim and lease heartbeats touch only the task, so they do not use this
    helper. Completion/dispatch must refresh and fence that task afterward.
    """
    scope = session.execute(select(Task.homework_id, Homework.student_id, Homework.tenant_id).join(
        Homework, Homework.id == Task.homework_id).where(Task.id == task_id)).mappings().first()
    if scope is None:
        return None
    student = lock_student(session, scope["student_id"], tenant_id=scope["tenant_id"])
    return {**scope, "student": student}


def task_scope_current(scope, homework):
    return bool(scope and homework is not None and scope["student"] is not None
                and scope["homework_id"] == homework.id and scope["student_id"] == homework.student_id
                and scope["tenant_id"] == homework.tenant_id)
