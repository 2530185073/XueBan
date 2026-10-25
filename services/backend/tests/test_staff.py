"""Campus teacher accounts, campus-wide access and the verification queue."""
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from xueban_backend.account_auth import grant_campus, set_password_account
from xueban_backend.account_models import PrincipalCampusGrant
from xueban_backend.app import create_app
from xueban_backend.models import Campus, Principal, PrincipalRole

from test_correction_flow import flow  # noqa: F401  (pytest fixture reuse)

ADMIN_PASSWORD = "synthetic-admin-password-1"
STAFF_PASSWORD = "synthetic-teacher-password-1"
CHANGED_PASSWORD = STAFF_PASSWORD + "-new"


def keyed(value):
    return {"Idempotency-Key": value}


def staff_setup(flow, tmp_path):
    from cryptography.hazmat.primitives import serialization
    key_path = tmp_path / "session-signing.pem"
    key_path.write_bytes(flow.signing.private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    key_path.chmod(0o600)
    cfg = replace(flow.cfg, signing_private_key_file=str(key_path))
    with Session(flow.database.engine) as session:
        session.add(Principal(id="admin_a", tenant_id="tenant_a", issuer=cfg.oidc_issuer, subject="admin-a", active=True))
        session.flush()
        session.add(PrincipalRole(principal_id="admin_a", role="campus_admin"))
        session.flush()
        grant_campus(session, "admin_a", "campus_a")
        set_password_account(session, cfg, principal_id="admin_a", username_value="campus.admin", password_value=ADMIN_PASSWORD, must_change=False)
        session.commit()
    client = TestClient(create_app(cfg))
    login = client.post("/v1/auth/login", json={"username": "campus.admin", "password": ADMIN_PASSWORD}, headers=keyed("login-admin-1"))
    assert login.status_code == 200, login.text
    return cfg, client, {"Authorization": "Bearer " + login.json()["access_token"]}


def test_admin_creates_campus_teacher_who_sees_queue(flow, tmp_path):
    case = flow.ready()
    flow.pipeline.verdict = "unable_to_judge"
    flow.start_check(case)
    assert flow.worker.run_once()
    cfg, client, admin = staff_setup(flow, tmp_path)

    bad = client.post("/v1/admin/staff-accounts", json={"username": "teacher.x", "display_name": "合成老师", "campus_ids": ["campus_b"], "roles": ["sitting_teacher"], "initial_password": STAFF_PASSWORD}, headers={**admin, **keyed("staff-create-0")})
    assert bad.status_code == 403
    created = client.post("/v1/admin/staff-accounts", json={"username": "teacher.a", "display_name": "合成老师", "campus_ids": ["campus_a"], "roles": ["sitting_teacher"], "initial_password": STAFF_PASSWORD}, headers={**admin, **keyed("staff-create-1")})
    assert created.status_code == 201, created.text
    assert created.json()["roles"] == ["sitting_teacher"] and created.json()["must_change_password"]
    listed = client.get("/v1/admin/staff-accounts", headers=admin).json()
    assert [item["username"] for item in listed["items"]] == ["teacher.a"]

    login = client.post("/v1/auth/login", json={"username": "teacher.a", "password": STAFF_PASSWORD}, headers=keyed("login-teacher-1")).json()
    token = {"Authorization": "Bearer " + login["access_token"]}
    assert client.get("/v1/review-queue", headers=token).status_code == 403
    changed = client.post("/v1/auth/change-password", json={"current_password": STAFF_PASSWORD, "new_password": CHANGED_PASSWORD}, headers={**token, **keyed("teacher-change-1")})
    assert changed.status_code == 200, changed.text
    token = {"Authorization": "Bearer " + changed.json()["access_token"]}

    # Campus grant reaches the learner without a per-learner grant.
    queue = client.get("/v1/review-queue", headers=token).json()
    assert [item["case_id"] for item in queue["items"]] == [case["id"]]
    assert queue["items"][0]["reason"] == "unable_to_judge"
    assert queue["items"][0]["student_name"] == "合成学生甲"
    assert client.get(f"/v1/cases/{case['id']}", headers=token).status_code == 200
    assert client.get("/v1/students/student_b/homeworks", headers=token).status_code == 404

    # Revoking the campus grant removes access at once.
    with Session(flow.database.engine) as session:
        grant = session.get(PrincipalCampusGrant, (created.json()["principal_id"], "campus_a"))
        grant.active = False
        session.commit()
    assert client.get(f"/v1/cases/{case['id']}", headers=token).status_code == 404
    client.close()


def test_admin_changes_teacher_campuses_roles_and_deactivates(flow, tmp_path):
    case = flow.ready()
    flow.start_check(case)
    assert flow.worker.run_once()
    cfg, client, admin = staff_setup(flow, tmp_path)
    with Session(flow.database.engine) as session:
        session.add(Campus(id="campus_c", tenant_id="tenant_a"))
        session.flush()
        grant_campus(session, "admin_a", "campus_c")
        session.add(Principal(id="admin_other", tenant_id="tenant_a", issuer=cfg.oidc_issuer, subject="admin-other", active=True))
        session.flush()
        session.add(PrincipalRole(principal_id="admin_other", role="campus_admin"))
        grant_campus(session, "admin_other", "campus_a")
        set_password_account(session, cfg, principal_id="admin_other", username_value="other.admin", password_value=ADMIN_PASSWORD, must_change=False)
        session.commit()
    created = client.post("/v1/admin/staff-accounts", json={"username": "teacher.b", "display_name": "合成老师乙", "campus_ids": ["campus_a"], "roles": ["sitting_teacher"], "initial_password": STAFF_PASSWORD}, headers={**admin, **keyed("staff-create-b")}).json()
    teacher = created["principal_id"]
    login = client.post("/v1/auth/login", json={"username": "teacher.b", "password": STAFF_PASSWORD}, headers=keyed("login-teacher-b")).json()
    changed = client.post("/v1/auth/change-password", json={"current_password": STAFF_PASSWORD, "new_password": CHANGED_PASSWORD}, headers={"Authorization": "Bearer " + login["access_token"], **keyed("teacher-b-change")}).json()
    token = {"Authorization": "Bearer " + changed["access_token"]}
    path = f"/v1/admin/staff-accounts/{teacher}/access"

    # Widening access keeps the teacher signed in; the same key replays, a different body conflicts.
    wider = {"campus_ids": ["campus_c", "campus_a"], "roles": ["sitting_teacher", "data_steward"], "active": True}
    response = client.post(path, json=wider, headers={**admin, **keyed("access-b-1")})
    assert response.status_code == 200, response.text
    assert response.json()["campus_ids"] == ["campus_a", "campus_c"] and response.json()["roles"] == ["data_steward", "sitting_teacher"]
    assert client.post(path, json=wider, headers={**admin, **keyed("access-b-1")}).json() == response.json()
    assert client.post(path, json={**wider, "active": False}, headers={**admin, **keyed("access-b-1")}).status_code == 409
    assert client.get("/v1/review-queue", headers=token).status_code == 200
    assert client.post(path, json={**wider, "campus_ids": ["campus_b"]}, headers={**admin, **keyed("access-b-2")}).status_code == 403
    assert client.post(path, json={**wider, "roles": ["campus_admin", "sitting_teacher"]}, headers={**admin, **keyed("access-b-3")}).status_code == 422

    # Narrowing ends the teacher's sessions and access to the removed campus.
    narrowed = client.post(path, json={"campus_ids": ["campus_c"], "roles": ["sitting_teacher"], "active": True}, headers={**admin, **keyed("access-b-4")})
    assert narrowed.status_code == 200 and narrowed.json()["campus_ids"] == ["campus_c"]
    assert client.get("/v1/review-queue", headers=token).status_code == 401
    relogin = client.post("/v1/auth/login", json={"username": "teacher.b", "password": CHANGED_PASSWORD}, headers=keyed("login-teacher-b-2")).json()
    assert client.get(f"/v1/cases/{case['id']}", headers={"Authorization": "Bearer " + relogin["access_token"]}).status_code == 404

    # Deactivation blocks sign-in and stays listed so it can be reversed.
    off = client.post(path, json={"campus_ids": ["campus_c"], "roles": ["sitting_teacher"], "active": False}, headers={**admin, **keyed("access-b-5")})
    assert off.status_code == 200 and off.json()["active"] is False
    assert client.post("/v1/auth/login", json={"username": "teacher.b", "password": CHANGED_PASSWORD}, headers=keyed("login-teacher-b-3")).status_code != 200
    assert any(item["principal_id"] == teacher and not item["active"] for item in client.get("/v1/admin/staff-accounts", headers=admin).json()["items"])
    on = client.post(path, json={"campus_ids": ["campus_c"], "roles": ["sitting_teacher"], "active": True}, headers={**admin, **keyed("access-b-6")})
    assert on.json()["active"] is True
    assert client.post("/v1/auth/login", json={"username": "teacher.b", "password": CHANGED_PASSWORD}, headers=keyed("login-teacher-b-4")).status_code == 200

    # Another administrator's account is not a teacher account to reshape here.
    other = client.get("/v1/admin/staff-accounts", headers=admin).json()["items"]
    other_id = next(item["principal_id"] for item in other if item["username"] == "other.admin")
    refused = client.post(f"/v1/admin/staff-accounts/{other_id}/access", json={"campus_ids": ["campus_a"], "roles": ["sitting_teacher"], "active": False}, headers={**admin, **keyed("access-other-1")})
    assert refused.status_code == 409 and refused.json()["code"] == "not_staff_account"
    client.close()


def test_student_reads_own_help_and_review_records(flow):
    case = flow.ready()
    response = flow.post(f"/v1/cases/{case['id']}/help-requests", {"expected_revision": case["revision"], "attempt_id": case["current_attempt_id"], "kind": "appeal", "note": "我觉得这题判错了"})
    assert response.status_code == 201, response.text
    help_id = response.json()["id"]
    read = flow.client.get(f"/v1/help-requests/{help_id}")
    assert read.status_code == 200 and read.json()["kind"] == "appeal" and read.json()["status"] == "waiting"
    assert flow.client.get("/v1/reviews/review_" + "0" * 32).status_code == 404
