"""Shared curriculum graph and per-student learning projections.

The curriculum graph holds no student data. Everything else is scoped by
(tenant_id, student_id), is derived from reviewed correction records, and is
registered with the deletion executor.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base

def student_fk():
    return ForeignKeyConstraint(["student_id", "tenant_id"], ["students.id", "students.tenant_id"])


class CurriculumGraph(Base):
    __tablename__ = "curriculum_graphs"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_note: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"
    graph_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_type: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    definition: Mapped[str | None] = mapped_column(Text)
    observable_objective: Mapped[str | None] = mapped_column(Text)
    curriculum_refs: Mapped[list] = mapped_column(JSON, nullable=False)
    coverage_facets: Mapped[list] = mapped_column(JSON, nullable=False)
    misconception_codes: Mapped[list] = mapped_column(JSON, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)


class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edges"
    graph_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    from_id: Mapped[str] = mapped_column(String(64), nullable=False)
    to_id: Mapped[str] = mapped_column(String(64), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)


class StudentLearningProfile(Base):
    __tablename__ = "student_learning_profiles"
    __table_args__ = (student_fk(),)
    student_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuestionKnowledgeLink(Base):
    """Append-only: the newest row per (question_id, knowledge_id) is current."""
    __tablename__ = "question_knowledge_links"
    __table_args__ = (student_fk(), Index("ix_qkl_question", "question_id"), Index("ix_qkl_student", "student_id"))
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    student_id: Mapped[str] = mapped_column(String(64), nullable=False)
    homework_id: Mapped[str] = mapped_column(String(64), nullable=False)
    question_id: Mapped[str] = mapped_column(String(64), nullable=False)
    question_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_id: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_version: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[str | None] = mapped_column(String(64))
    supersedes_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AttemptCondition(Base):
    """Staff-confirmed help condition for one attempt; newest row is current."""
    __tablename__ = "attempt_conditions"
    __table_args__ = (student_fk(), Index("ix_attempt_conditions_attempt", "attempt_id"), Index("ix_attempt_conditions_student", "student_id"))
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    student_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_id: Mapped[str] = mapped_column(String(64), nullable=False)
    assistance_level: Mapped[str] = mapped_column(String(24), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeDiagnosis(Base):
    __tablename__ = "knowledge_diagnoses"
    __table_args__ = (student_fk(), Index("ix_knowledge_diagnoses_student", "student_id", "knowledge_id"))
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    student_id: Mapped[str] = mapped_column(String(64), nullable=False)
    knowledge_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LearningEvidence(Base):
    """Append-only observation; changed inputs invalidate and add, never edit."""
    __tablename__ = "learning_evidence"
    __table_args__ = (student_fk(), Index("ix_learning_evidence_student", "student_id", "validity"),
                      Index("ix_learning_evidence_key", "student_id", "source_key"))
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    campus_id: Mapped[str] = mapped_column(String(64), nullable=False)
    student_id: Mapped[str] = mapped_column(String(64), nullable=False)
    knowledge_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    validity: Mapped[str] = mapped_column(String(16), nullable=False)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidation_reason: Mapped[str | None] = mapped_column(String(48))


class StudentKnowledgeState(Base):
    __tablename__ = "student_knowledge_states"
    __table_args__ = (student_fk(),)
    student_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_version: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LearningProjection(Base):
    __tablename__ = "learning_projections"
    __table_args__ = (student_fk(),)
    student_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    projected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LearningOutbox(Base):
    """Durable at-least-once jobs; consumers are idempotent, so a duplicate is harmless."""
    __tablename__ = "learning_outbox"
    __table_args__ = (Index("ix_learning_outbox_claim", "status", "available_at"), Index("ix_learning_outbox_student", "student_id"),
                      Index("ix_learning_outbox_dedupe", "dedupe_key", "status"))
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    student_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    tries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentMemoryItem(Base):
    """One long-term memory of a learner's personal agent, always traceable to evidence."""
    __tablename__ = "agent_memory_items"
    __table_args__ = (student_fk(), Index("ix_agent_memory_student", "student_id", "status"),
                      Index("ix_agent_memory_rule", "student_id", "rule_key"))
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    student_id: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    knowledge_id: Mapped[str | None] = mapped_column(String(64))
    evidence_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(48))
    rule_key: Mapped[str | None] = mapped_column(String(160))
    proposed_by: Mapped[str | None] = mapped_column(String(64))
    confirmed_by: Mapped[str | None] = mapped_column(String(64))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_id: Mapped[str | None] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentRun(Base):
    """What one agent output read (versions, evidence, memories) and produced."""
    __tablename__ = "agent_runs"
    __table_args__ = (student_fk(), Index("ix_agent_runs_student", "student_id", "purpose"))
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    student_id: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_ref: Mapped[str | None] = mapped_column(String(64))
    context: Mapped[dict] = mapped_column(JSON, nullable=False)
    output: Mapped[dict | None] = mapped_column(JSON)
    model_metadata: Mapped[dict | None] = mapped_column(JSON)
    requested_by: Mapped[str | None] = mapped_column(String(64))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
