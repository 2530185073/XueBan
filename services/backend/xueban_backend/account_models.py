from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, ForeignKeyConstraint, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base


class PasswordAccount(Base):
    __tablename__ = 'password_accounts'
    __table_args__ = (
        ForeignKeyConstraint(['principal_id', 'tenant_id'], ['principals.id', 'principals.tenant_id']),
        ForeignKeyConstraint(['student_id', 'tenant_id'], ['students.id', 'students.tenant_id']),
    )
    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    student_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    password_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PrincipalCampusGrant(Base):
    __tablename__ = 'principal_campus_grants'
    __table_args__ = (
        ForeignKeyConstraint(['principal_id', 'tenant_id'], ['principals.id', 'principals.tenant_id']),
        ForeignKeyConstraint(['campus_id', 'tenant_id'], ['campuses.id', 'campuses.tenant_id']),
    )
    principal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campus_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AuthOperation(Base):
    __tablename__ = 'auth_operations'
    __table_args__ = (UniqueConstraint('principal_id', 'operation', 'key_hash', name='uq_auth_operation'),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_id: Mapped[str] = mapped_column(ForeignKey('principals.id'), nullable=False)
    operation: Mapped[str] = mapped_column(String(96), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_salt: Mapped[str] = mapped_column(String(32), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str | None] = mapped_column(ForeignKey('device_sessions.id'))
    previous_session_id: Mapped[str | None] = mapped_column(String(64))
    credential_version: Mapped[int | None] = mapped_column(Integer)
    response_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LoginLimit(Base):
    __tablename__ = 'login_limits'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RegistrationCounter(Base):
    """Global account namespace; deletion must not recycle an issued number."""
    __tablename__ = 'registration_counters'
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_number: Mapped[int] = mapped_column(Integer, nullable=False)


class StudentRegistration(Base):
    """Anonymous-request recovery without retaining a plaintext password."""
    __tablename__ = 'student_registrations'
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_salt: Mapped[str] = mapped_column(String(32), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    username: Mapped[str] = mapped_column(ForeignKey('password_accounts.username', ondelete='CASCADE'), nullable=False, unique=True)
    grade: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
