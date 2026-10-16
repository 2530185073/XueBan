import base64
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
from jwt.exceptions import PyJWKClientConnectionError
import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from xueban_backend.app import create_app
from xueban_backend.contracts import Contracts
from xueban_backend.models import Homework, IdempotencyRecord, Principal, PrincipalRole, Student, StudentGrant
from conftest import BACKEND, ISSUER, migrate


def auth(signing, **kwargs):
    return {"Authorization": "Bearer " + signing.token(**kwargs)}


def create(client, signing, payload, key="operation-0001", student="student_a", **kwargs):
    return client.post(f"/v1/students/{student}/homeworks", json=payload, headers={**auth(signing, **kwargs), "Idempotency-Key": key})


def assert_problem(response, status, code, settings):
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert response.headers["content-type"].startswith("application/problem+json")
    Contracts(settings.contracts_dir).validate("Problem", response.json())
    assert "eyJ" not in response.text  # No encoded token is echoed.


def test_real_identity_and_ownership_ignore_untrusted_claims(settings, signing, payload):
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/me", headers=auth(signing, overrides={"roles": ["campus_admin"], "student_id": "student_b", "tenant_id": "tenant_b"}))
        assert response.status_code == 200
        assert response.json() == {"actor_id": "actor_a", "roles": ["student"], "authorized_students": [{"student_id": "student_a", "display_name": "合成学生甲", "campus_id": "campus_a"}]}
        created = create(client, signing, payload)
        assert created.status_code == 201, created.text
        assert created.json()["scope"] == {"tenant_id": "tenant_a", "campus_id": "campus_a", "student_id": "student_a"}
        assert created.json()["curriculum"]["grade"] is None
        assert created.json()["asset_ids"] == []
        assert created.json()["occurred_at"] == "2026-10-16T07:30:00Z"
        assert client.get("/v1/homeworks/" + created.json()["id"], headers=auth(signing)).json() == created.json()
        Contracts(settings.contracts_dir).validate("HomeworkSession", created.json())


@pytest.mark.parametrize("variant", ["missing", "expired", "wrong_issuer", "wrong_audience", "wrong_signature", "missing_exp", "future_iat", "hs256"])
def test_invalid_credentials_fail_closed(settings, signing, variant):
    options = {}
    if variant == "expired": options["overrides"] = {"exp": datetime.now(timezone.utc) - timedelta(seconds=5)}
    if variant == "wrong_issuer": options["overrides"] = {"iss": "https://attacker.example.invalid"}
    if variant == "wrong_audience": options["overrides"] = {"aud": "different-api"}
    if variant == "wrong_signature": options["key"] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    if variant == "missing_exp": options["omit"] = ("exp",)
    if variant == "future_iat": options["overrides"] = {"iat": datetime.now(timezone.utc) + timedelta(hours=1)}
    if variant == "hs256": options.update(key=os.urandom(32), algorithm="HS256")
    headers = {"X-Demo-Actor": "teacher"} if variant == "missing" else auth(signing, **options)
    with TestClient(create_app(settings)) as client:
        assert_problem(client.get("/v1/me", headers=headers), 401, "authentication_required", settings)


def test_unconfigured_auth_refuses_access(settings, signing):
    with TestClient(create_app(replace(settings, oidc_issuer="", jwt_public_key_file=""))) as client:
        assert_problem(client.get("/v1/me", headers=auth(signing)), 503, "service_unavailable", settings)


def test_unknown_or_deactivated_principal_is_not_provisioned_from_jwt(settings, signing, database):
    with TestClient(create_app(settings)) as client:
        assert_problem(client.get("/v1/me", headers=auth(signing, subject="unknown-subject")), 403, "access_denied", settings)
        with Session(database.engine) as session:
            session.get(Principal, "actor_a").active = False
            session.commit()
        assert_problem(client.get("/v1/me", headers=auth(signing)), 403, "access_denied", settings)


def test_cross_student_and_cross_tenant_access_is_hidden(settings, signing, payload):
    with TestClient(create_app(settings)) as client:
        foreign = create(client, signing, payload, student="student_b", subject="subject-b")
        assert foreign.status_code == 201
        for student in ("student_ungranted", "student_b"):
            assert_problem(create(client, signing, payload, student=student), 404, "not_found", settings)
            assert_problem(client.get(f"/v1/students/{student}/homeworks", headers=auth(signing)), 404, "not_found", settings)
        assert_problem(client.get("/v1/homeworks/" + foreign.json()["id"], headers=auth(signing)), 404, "not_found", settings)


def test_idempotency_same_payload_replays_and_changed_payload_conflicts(settings, signing, payload, database):
    with TestClient(create_app(settings)) as client:
        first = create(client, signing, payload)
        replay = create(client, signing, dict(reversed(list(payload.items()))))
        assert first.status_code == replay.status_code == 201
        assert first.json() == replay.json()
        changed = dict(payload, title="另一次作业")
        assert_problem(create(client, signing, changed), 409, "idempotency_conflict", settings)
        assert create(client, signing, changed, key="operation-0002").status_code == 201
    with Session(database.engine) as session:
        assert session.scalar(select(func.count()).select_from(Homework)) == 2
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 2


def test_revoked_grant_blocks_reads_and_idempotency_replay(settings, signing, payload, database):
    with TestClient(create_app(settings)) as client:
        record = create(client, signing, payload).json()
        with Session(database.engine) as session:
            session.get(StudentGrant, ("actor_a", "student_a")).active = False
            session.commit()
        assert client.get("/v1/me", headers=auth(signing)).json()["authorized_students"] == []
        assert_problem(client.get("/v1/homeworks/" + record["id"], headers=auth(signing)), 404, "not_found", settings)
        assert_problem(create(client, signing, payload), 404, "not_found", settings)


@pytest.mark.parametrize("bad_field", ["student_id", "role", "curriculum", "occurred_at", "title"])
def test_command_schema_rejects_untrusted_or_invalid_fields(settings, signing, payload, bad_field):
    bad = copy.deepcopy(payload)
    if bad_field == "student_id": bad["student_id"] = "student_b"
    if bad_field == "role": bad["role"] = "campus_admin"
    if bad_field == "curriculum": bad["curriculum"]["grade"] = 6
    if bad_field == "occurred_at": bad["occurred_at"] = "2026-10-16T08:00:00"
    if bad_field == "title": bad["title"] = ""
    with TestClient(create_app(settings)) as client:
        assert_problem(create(client, signing, bad), 422, "invalid_request", settings)
        assert client.get("/v1/students/student_a/homeworks", headers=auth(signing)).json()["items"] == []


def test_missing_idempotency_key_is_rejected(settings, signing, payload):
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/students/student_a/homeworks", json=payload, headers=auth(signing))
        assert_problem(response, 422, "invalid_request", settings)


def test_utc_overflow_rejects_command_without_writing(settings, signing, payload):
    with TestClient(create_app(settings)) as client:
        bad = dict(payload, occurred_at="9999-12-31T23:59:59-01:00")
        assert_problem(create(client, signing, bad), 422, "invalid_request", settings)
        assert client.get("/v1/students/student_a/homeworks", headers=auth(signing)).json()["items"] == []


@pytest.mark.parametrize("invalid_time", [None, 123, [], {}])
def test_history_cursor_invalid_timestamp_type_returns_problem(settings, signing, invalid_time):
    cursor = base64.urlsafe_b64encode(json.dumps({"student_id": "student_a", "created_at": invalid_time, "id": "homework_fake"}).encode()).decode()
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/students/student_a/homeworks", params={"cursor": cursor}, headers=auth(signing))
        assert_problem(response, 422, "invalid_request", settings)


def test_history_is_paged_and_cursor_cannot_switch_student(settings, signing, payload):
    with TestClient(create_app(settings)) as client:
        created = [create(client, signing, dict(payload, title=f"合成作业 {i}"), key=f"history-op-{i}").json()["id"] for i in range(3)]
        page1 = client.get("/v1/students/student_a/homeworks?limit=2", headers=auth(signing)).json()
        assert [row["id"] for row in page1["items"]] == list(reversed(created))[0:2]
        page2 = client.get("/v1/students/student_a/homeworks", params={"cursor": page1["next_cursor"], "limit": 2}, headers=auth(signing)).json()
        assert [row["id"] for row in page2["items"]] == created[:1]
        assert page2["next_cursor"] is None
        wrong_scope = client.get("/v1/students/student_b/homeworks", params={"cursor": page1["next_cursor"]}, headers=auth(signing, subject="subject-b"))
        assert_problem(wrong_scope, 422, "invalid_request", settings)
        assert_problem(client.get("/v1/students/student_a/homeworks?cursor=garbage", headers=auth(signing)), 422, "invalid_request", settings)


def test_committed_data_survives_fresh_process(settings, signing, payload):
    with TestClient(create_app(settings)) as client:
        first = create(client, signing, payload).json()
    script = """import json, os
from fastapi.testclient import TestClient
from xueban_backend.app import create_app
with TestClient(create_app()) as client:
    response = client.get('/v1/homeworks/' + os.environ['TEST_HOMEWORK_ID'], headers={'Authorization': 'Bearer ' + os.environ['TEST_ACCESS_TOKEN']})
    assert response.status_code == 200, response.text
    print(json.dumps(response.json(), ensure_ascii=False))
"""
    environment = dict(os.environ, PYTHONPATH=str(BACKEND), DATABASE_URL=settings.database_url, ALLOW_SQLITE_FOR_TESTS="1", OIDC_ISSUER=settings.oidc_issuer, OIDC_AUDIENCE=settings.oidc_audience, JWT_PUBLIC_KEY_FILE=settings.jwt_public_key_file, OIDC_JWKS_URL="", TEST_ACCESS_TOKEN=signing.token(), TEST_HOMEWORK_ID=first["id"])
    result = subprocess.run([sys.executable, "-c", script], env=environment, capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(result.stdout) == first
    with TestClient(create_app(settings)) as restarted:
        assert create(restarted, signing, payload).json() == first


def test_failed_commit_rolls_back_homework_and_idempotency(settings, signing, payload, database, monkeypatch):
    with TestClient(create_app(settings)) as client:
        with monkeypatch.context() as patch:
            def fail_commit(_): raise SQLAlchemyError("injected pre-commit failure")
            patch.setattr(Session, "commit", fail_commit)
            assert_problem(create(client, signing, payload), 503, "service_unavailable", settings)
        with Session(database.engine) as session:
            assert session.scalar(select(func.count()).select_from(Homework)) == 0
            assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0
        assert create(client, signing, payload).status_code == 201


def test_database_rejects_cross_tenant_grant(database):
    with Session(database.engine) as session:
        session.add(StudentGrant(principal_id="actor_a", student_id="student_b", tenant_id="tenant_a", active=True))
        with pytest.raises(IntegrityError): session.commit()


def test_jwks_verification_and_unavailable_provider(settings, signing, monkeypatch):
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(signing.private.public_key(), as_dict=True)
    jwk.update(kid="runtime-test-key", alg="RS256", use="sig")
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda _: {"keys": [jwk]})
    cfg = replace(settings, jwt_public_key_file="", oidc_jwks_url=ISSUER + "/jwks")
    with TestClient(create_app(cfg)) as client:
        assert client.get("/v1/me", headers=auth(signing)).status_code == 200
    def unavailable(_): raise PyJWKClientConnectionError("provider unavailable")
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", unavailable)
    with TestClient(create_app(cfg)) as client:
        assert_problem(client.get("/v1/me", headers=auth(signing)), 503, "service_unavailable", settings)


def test_migration_downgrade_and_reupgrade(database):
    migrate(database.url)  # Repeating upgrade head must be a no-op.
    database.engine.dispose()
    migrate(database.url, "base", downgrade=True)
    assert "homeworks" not in inspect(database.engine).get_table_names()
    migrate(database.url)
    assert {"homeworks", "idempotency_records", "principals", "student_grants"} <= set(inspect(database.engine).get_table_names())


def test_cors_default_closed_and_exact_opt_in(settings):
    headers = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,idempotency-key,content-type"}
    with TestClient(create_app(settings)) as client:
        assert "access-control-allow-origin" not in client.options("/v1/students/student_a/homeworks", headers=headers).headers
    with TestClient(create_app(replace(settings, cors_allowed_origins=("http://localhost:5173",)))) as client:
        allowed = client.options("/v1/students/student_a/homeworks", headers=headers)
        assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert "access-control-allow-credentials" not in allowed.headers
        denied = client.options("/v1/students/student_a/homeworks", headers=dict(headers, Origin="https://attacker.example.invalid"))
        assert "access-control-allow-origin" not in denied.headers
    with pytest.raises(RuntimeError): create_app(replace(settings, cors_allowed_origins=("*",)))


@pytest.mark.parametrize("different_payload", [False, True])
def test_postgres_concurrent_retry_creates_one_homework(settings, signing, payload, database, different_payload):
    if database.dialect != "postgresql": pytest.skip("Row contention behavior must be verified on PostgreSQL")
    def submit(index):
        with TestClient(create_app(settings)) as client:
            command = dict(payload, title=f"并发作业 {index}") if different_payload else payload
            response = create(client, signing, command, key="concurrent-operation")
            return response.status_code, response.json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    if different_payload:
        assert sorted(status for status, _ in responses) == [201, 409], responses
        assert next(body for status, body in responses if status == 409)["code"] == "idempotency_conflict"
    else:
        assert all(status == 201 for status, _ in responses), responses
        assert responses[0][1] == responses[1][1]
    with Session(database.engine) as session:
        assert session.scalar(select(func.count()).select_from(Homework)) == 1
