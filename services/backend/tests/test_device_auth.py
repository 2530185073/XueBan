from dataclasses import replace
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from xueban_backend.admin import enroll
from xueban_backend.app import create_app
from xueban_backend.device_auth import digest, install_device_auth, issue_pairing, normalize_code
from xueban_backend.device_models import DevicePairing, DeviceSession
from xueban_backend.models import Principal, StudentGrant


@pytest.fixture
def paired(settings, signing, tmp_path, database):
    private = tmp_path / "private.pem"
    private.write_bytes(signing.private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    private.chmod(0o600)
    cfg = replace(settings, signing_private_key_file=str(private))
    app = create_app(cfg)
    if not any(route.path == "/v1/device-sessions" for route in app.routes):
        install_device_auth(app, database.engine, cfg)
    with Session(database.engine) as session, session.begin():
        code = issue_pairing(session, cfg, "actor_a")
    with TestClient(app) as client:
        yield client, cfg, code, private


def exchange(client, code, key="pairing-request-0001"):
    return client.post("/v1/device-sessions", json={"pairing_code": code}, headers={"Idempotency-Key": key})


def test_pairing_authenticates_real_authorized_subject_and_retry(paired, database):
    client, cfg, code, _ = paired
    response = exchange(client, code)
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    identity = client.get("/v1/me", headers={"Authorization": "Bearer " + token})
    assert identity.status_code == 200
    assert [s["student_id"] for s in identity.json()["authorized_students"]] == ["student_a"]
    assert exchange(client, code).json() == response.json()
    assert exchange(client, code, "different-request").status_code == 401
    with Session(database.engine) as session:
        row = session.get(DevicePairing, digest(normalize_code(code)))
        assert row.code_hash != code and row.redemption_key_hash
        assert len(session.scalars(select(DeviceSession)).all()) == 1


def test_device_revocation_invalidates_bearer_and_retry(paired, database):
    client, _, code, _ = paired
    result = exchange(client, code).json()
    with Session(database.engine) as session, session.begin():
        device = session.scalar(select(DeviceSession))
        device.revoked = True
    assert client.get("/v1/me", headers={"Authorization": "Bearer " + result["access_token"]}).status_code == 401
    assert exchange(client, code).status_code == 401


def test_pairing_expired_unknown_or_ungranted_rejected(paired, database):
    client, _, code, _ = paired
    assert exchange(client, "AAAA-AAAA-AAAA-AAAA").status_code == 401
    with Session(database.engine) as session, session.begin():
        session.get(DevicePairing, digest(normalize_code(code))).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert exchange(client, code).status_code == 401


def test_lost_response_retry_after_pairing_expiry_recovers_same_session(paired, database):
    client, _, code, _ = paired
    initial = exchange(client, code).json()
    with Session(database.engine) as session, session.begin():
        session.get(DevicePairing, digest(normalize_code(code))).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert exchange(client, code).json() == initial
    assert exchange(client, code, "different-client-key").status_code == 401
    with Session(database.engine) as session, session.begin():
        session.scalar(select(DeviceSession)).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert exchange(client, code).status_code == 401


def test_revoked_grant_cannot_pair_or_access(paired, database):
    client, _, code, _ = paired
    token = exchange(client, code).json()["access_token"]
    with Session(database.engine) as session, session.begin():
        session.get(StudentGrant, ("actor_a", "student_a")).active = False
    assert exchange(client, code).status_code == 401
    assert client.get("/v1/me", headers={"Authorization": "Bearer " + token}).json()["authorized_students"] == []


def test_wrong_principal_session_cannot_be_reused(paired, database, signing):
    client, _, code, _ = paired
    result = exchange(client, code).json()
    claims = jwt.decode(result["access_token"], options={"verify_signature": False})
    forged_by_issuer = signing.token(subject="subject-b", overrides={"device_session_id": claims["device_session_id"]})
    assert client.get("/v1/me", headers={"Authorization": "Bearer " + forged_by_issuer}).status_code == 401


@pytest.mark.parametrize("extra", [{"student_id": "student_b"}, {"role": "campus_admin"}])
def test_pairing_cannot_self_assign_scope(paired, extra):
    client, _, code, _ = paired
    response = client.post("/v1/device-sessions", json={"pairing_code": code, **extra}, headers={"Idempotency-Key": "pairing-00001"})
    assert response.status_code == 422


def test_missing_or_insecure_signing_key_does_not_consume_code(paired):
    client, _, code, path = paired
    path.chmod(0o644)
    assert exchange(client, code).status_code == 503
    path.chmod(0o600)
    assert exchange(client, code).status_code == 200


def test_pairing_throttle(paired):
    client, _, _, _ = paired
    for _ in range(10):
        assert exchange(client, "AAAA-AAAA-AAAA-AAAA").status_code == 401
    assert exchange(client, "AAAA-AAAA-AAAA-AAAA").status_code == 429


def test_enrollment_does_not_elevate_or_move_existing_identity(settings, database):
    with Session(database.engine) as session:
        with pytest.raises(ValueError, match="role"):
            enroll(session, settings, tenant_id="tenant_a", campus_id="campus_a", student_id="student_a",
                   student_name="合成学生甲", principal_id="actor_a", subject="subject-a", role="campus_admin")
        session.rollback()
        with pytest.raises(ValueError, match="Campus"):
            enroll(session, settings, tenant_id="tenant_b", campus_id="campus_a", student_id="student_b",
                   student_name="合成学生乙", principal_id="actor_b", subject="subject-b", role="student")
