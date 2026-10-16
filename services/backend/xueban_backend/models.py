from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, JSON, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)


class Campus(Base):
    __tablename__ = "campuses"
    __table_args__ = (UniqueConstraint("id", "tenant_id"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)


class Principal(Base):
    __tablename__ = "principals"
    __table_args__ = (UniqueConstraint("issuer", "subject"), UniqueConstraint("id", "tenant_id"))
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PrincipalRole(Base):
    __tablename__ = "principal_roles"
    __table_args__ = (CheckConstraint("role IN ('student', 'sitting_teacher', 'campus_admin', 'data_steward')", name="valid_principal_role"),)
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (
        ForeignKeyConstraint(["campus_id", "tenant_id"], ["campuses.id", "campuses.tenant_id"]),
        UniqueConstraint("id", "tenant_id"),
        UniqueConstraint("id", "campus_id", "tenant_id"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    campus_id: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class StudentGrant(Base):
    __tablename__ = "student_grants"
    __table_args__ = (
        ForeignKeyConstraint(["principal_id", "tenant_id"], ["principals.id", "principals.tenant_id"]),
        ForeignKeyConstraint(["student_id", "tenant_id"], ["students.id", "students.tenant_id"]),
    )
    principal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Homework(Base):
    __tablename__ = "homeworks"
    __table_args__ = (
        ForeignKeyConstraint(["student_id", "campus_id", "tenant_id"], ["students.id", "students.campus_id", "students.tenant_id"]),
        CheckConstraint("revision >= 1", name="positive_homework_revision"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    campus_id: Mapped[str] = mapped_column(String(64), nullable=False)
    student_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(default=1, nullable=False)
    curriculum: Mapped[dict] = mapped_column(JSON, nullable=False)
    title: Mapped[str | None] = mapped_column(String(160))
    collection_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processing_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    coverage_json: Mapped[dict | None] = mapped_column(JSON)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("principal_id", "student_id", "operation", "key", name="uq_idempotency_scope"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), nullable=False)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
