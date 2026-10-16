"""Durable correction aggregates; public documents are validated by phase1 schemas."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class Asset(Base):
    __tablename__ = "correction_assets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    homework_id: Mapped[str] = mapped_column(ForeignKey("homeworks.id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    upload_token_hash: Mapped[str | None] = mapped_column(String(64))
    upload_actor_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    upload_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_token_hash: Mapped[str | None] = mapped_column(String(64))
    read_actor_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    read_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Entity(Base):
    __tablename__ = "correction_entities"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    homework_id: Mapped[str] = mapped_column(ForeignKey("homeworks.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    annotations: Mapped[dict | None] = mapped_column(JSON)
    __table_args__ = (Index("ix_correction_entity_homework_kind", "homework_id", "kind"),)


class EntitySnapshot(Base):
    __tablename__ = "correction_snapshots"
    entity_id: Mapped[str] = mapped_column(ForeignKey("correction_entities.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(primary_key=True)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)


class Task(Base):
    __tablename__ = "correction_tasks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    homework_id: Mapped[str] = mapped_column(ForeignKey("homeworks.id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, nullable=False)
    audit: Mapped[dict | None] = mapped_column(JSON)
    tries: Mapped[int] = mapped_column(default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_correction_task_claim", "status", "available_at", "lease_expires_at"),)


class CorrectionEvent(Base):
    __tablename__ = "correction_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    homework_id: Mapped[str] = mapped_column(ForeignKey("homeworks.id"), nullable=False, index=True)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_revision: Mapped[int] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("aggregate_id", "aggregate_revision", "kind", name="uq_correction_event_revision"),)
