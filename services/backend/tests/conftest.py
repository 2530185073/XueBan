from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from xueban_backend.config import Settings
from xueban_backend.db import create_database_engine
from xueban_backend.models import Campus, Principal, PrincipalRole, Student, StudentGrant, Tenant

BACKEND = Path(__file__).resolve().parents[1]
ISSUER = "https://identity.example.invalid/real-provider"
AUDIENCE = "xueban-phase1-api"
PG_URL = os.environ.get("BACKEND_TEST_DATABASE_URL")


def pytest_addoption(parser):
    parser.addoption("--require-postgres", action="store_true", help="Fail instead of silently skipping PostgreSQL verification")


def pytest_configure(config):
    if config.getoption("--require-postgres") and not PG_URL:
        raise pytest.UsageError("--require-postgres needs BACKEND_TEST_DATABASE_URL")
    if PG_URL and make_url(PG_URL).get_backend_name() != "postgresql":
        raise pytest.UsageError("BACKEND_TEST_DATABASE_URL must be PostgreSQL")


def migrate(url, target="head", *, downgrade=False):
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["database_url"] = url
    cfg.attributes["allow_sqlite"] = url.startswith("sqlite")
    (command.downgrade if downgrade else command.upgrade)(cfg, target)


@pytest.fixture(params=["sqlite"] + (["postgresql"] if PG_URL else []))
def database(request, tmp_path):
    admin = None
    schema = None
    if request.param == "postgresql":
        # Each test gets a new private schema; never drop or truncate existing tables.
        schema = "xueban_test_" + uuid4().hex
        admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = make_url(PG_URL).update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
    else:
        url = f"sqlite:///{tmp_path / 'test.db'}"
    migrate(url)
    engine = create_database_engine(url, allow_sqlite=True)
    try:
        with Session(engine) as session:
            session.add_all([Tenant(id="tenant_a"), Tenant(id="tenant_b")])
            session.flush()
            session.add_all([Campus(id="campus_a", tenant_id="tenant_a"), Campus(id="campus_b", tenant_id="tenant_b")])
            session.add_all([
                Principal(id="actor_a", tenant_id="tenant_a", issuer=ISSUER, subject="subject-a", active=True),
                Principal(id="actor_b", tenant_id="tenant_b", issuer=ISSUER, subject="subject-b", active=True),
            ])
            session.flush()
            session.add_all([
                Student(id="student_a", tenant_id="tenant_a", campus_id="campus_a", display_name="合成学生甲", active=True),
                Student(id="student_ungranted", tenant_id="tenant_a", campus_id="campus_a", display_name="合成未授权学生", active=True),
                Student(id="student_b", tenant_id="tenant_b", campus_id="campus_b", display_name="合成学生乙", active=True),
                PrincipalRole(principal_id="actor_a", role="student"),
                PrincipalRole(principal_id="actor_b", role="sitting_teacher"),
            ])
            session.flush()
            session.add_all([
                StudentGrant(principal_id="actor_a", student_id="student_a", tenant_id="tenant_a", active=True),
                StudentGrant(principal_id="actor_b", student_id="student_b", tenant_id="tenant_b", active=True),
            ])
            session.commit()
        yield SimpleNamespace(url=url, engine=engine, dialect=request.param)
    finally:
        engine.dispose()
        if admin is not None:
            with admin.connect() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


@pytest.fixture
def signing(tmp_path):
    # Signing material exists only in the running test, never in the repository.
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_path = tmp_path / "issuer-public.pem"
    public_path.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))

    def token(subject="subject-a", overrides=None, omit=(), key=None, algorithm="RS256"):
        now = datetime.now(timezone.utc)
        claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": subject, "iat": now, "exp": now + timedelta(minutes=5)}
        claims.update(overrides or {})
        for field in omit:
            claims.pop(field, None)
        return jwt.encode(claims, key if key is not None else private, algorithm=algorithm, headers={"kid": "runtime-test-key"})

    return SimpleNamespace(private=private, public_path=public_path, token=token)


@pytest.fixture
def settings(database, signing):
    return Settings(database_url=database.url, oidc_issuer=ISSUER, oidc_audience=AUDIENCE, jwt_public_key_file=str(signing.public_path), allow_sqlite_for_tests=True)


@pytest.fixture
def payload():
    return {"curriculum": {"stage": "junior_middle", "publisher_code": "PEP", "grade": None, "subject": None, "volume": None, "edition": None}, "title": "合成学校作业", "occurred_at": "2026-10-16T15:30:00+08:00", "collection_scope": "partial"}
