"""Local administrator commands. Requires database access; never exposed over HTTP."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .db import create_database_engine
from .device_auth import issue_pairing, signing_key
from .device_models import DeviceSession
from .models import Campus, Principal, PrincipalRole, Student, StudentGrant, Tenant


def private_write(path: Path, content: bytes):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())


def initialize_issuer(directory: Path):
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    private_write(directory / "issuer-private.pem", key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    private_write(directory / "issuer-public.pem", key.public_key().public_bytes(serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo))


def enroll(session: Session, cfg: Settings, *, tenant_id: str, campus_id: str, student_id: str,
           student_name: str, principal_id: str, subject: str, role: str):
    for value in (tenant_id, campus_id, student_id, principal_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            raise ValueError("Invalid resource identifier")
    if not cfg.auth_configured or not 1 <= len(subject) <= 255 or not 1 <= len(student_name) <= 80:
        raise ValueError("Configured issuer, subject and student name are required")
    if role not in {"student", "sitting_teacher", "campus_admin"}:
        raise ValueError("Invalid role")
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        session.add(Tenant(id=tenant_id))
        session.flush()
    campus = session.get(Campus, campus_id)
    if campus is None:
        session.add(Campus(id=campus_id, tenant_id=tenant_id))
        session.flush()
    elif campus.tenant_id != tenant_id:
        raise ValueError("Campus belongs to a different tenant")
    student = session.get(Student, student_id)
    if student is None:
        session.add(Student(id=student_id, tenant_id=tenant_id, campus_id=campus_id, display_name=student_name, active=True))
        session.flush()
    elif (student.tenant_id, student.campus_id, student.display_name, student.active) != (tenant_id, campus_id, student_name, True):
        raise ValueError("Existing student differs; refusing to change ownership or reactivate")
    principal = session.get(Principal, principal_id)
    if principal is None:
        session.add(Principal(id=principal_id, tenant_id=tenant_id, issuer=cfg.oidc_issuer, subject=subject, active=True))
        session.flush()
    elif (principal.tenant_id, principal.issuer, principal.subject, principal.active) != (tenant_id, cfg.oidc_issuer, subject, True):
        raise ValueError("Existing principal differs; refusing to overwrite")
    roles = set(session.scalars(select(PrincipalRole.role).where(PrincipalRole.principal_id == principal_id)))
    if roles and roles != {role}:
        raise ValueError("Existing role differs; refusing to elevate privileges")
    if not roles:
        session.add(PrincipalRole(principal_id=principal_id, role=role))
    grant = session.get(StudentGrant, (principal_id, student_id))
    if grant is None:
        session.add(StudentGrant(principal_id=principal_id, student_id=student_id, tenant_id=tenant_id, active=True))
    elif not grant.active:
        raise ValueError("Grant was revoked; explicit authorization review required")
    session.flush()


def bootstrap_admin(session, cfg, *, tenant_id, campus_id, principal_id, subject, username, password_file):
    """Explicit local-operator setup; no fake student or inherited student grants."""
    from .account_auth import grant_campus, set_password_account
    from .passwords import read_password_file
    for value in (tenant_id, campus_id, principal_id):
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", value):
            raise ValueError("Invalid resource identifier")
    if not cfg.auth_configured or not 1 <= len(subject) <= 255:
        raise ValueError("Configured issuer and subject are required")
    password = read_password_file(password_file)
    if session.get(Tenant, tenant_id) is None:
        session.add(Tenant(id=tenant_id)); session.flush()
    campus = session.get(Campus, campus_id)
    if campus is None:
        session.add(Campus(id=campus_id, tenant_id=tenant_id)); session.flush()
    elif campus.tenant_id != tenant_id:
        raise ValueError("Campus belongs to a different tenant")
    principal = session.get(Principal, principal_id)
    if principal is None:
        session.add(Principal(id=principal_id, tenant_id=tenant_id, issuer=cfg.oidc_issuer, subject=subject, active=True)); session.flush()
    elif (principal.tenant_id, principal.issuer, principal.subject, principal.active) != (tenant_id, cfg.oidc_issuer, subject, True):
        raise ValueError("Existing principal differs; refusing to overwrite")
    roles = set(session.scalars(select(PrincipalRole.role).where(PrincipalRole.principal_id == principal_id)))
    if roles and roles != {"campus_admin"}:
        raise ValueError("Existing role differs; refusing to elevate privileges")
    if not roles:
        session.add(PrincipalRole(principal_id=principal_id, role="campus_admin")); session.flush()
    # Re-running bootstrap must never reset an already established account.
    from .account_models import PasswordAccount
    if session.scalar(select(PasswordAccount).where(PasswordAccount.principal_id == principal_id)) is not None:
        raise ValueError("Account already exists; use explicit password account administration")
    grant_campus(session, principal_id, campus_id)
    return set_password_account(session, cfg, principal_id=principal_id, username_value=username,
                                password_value=password, must_change=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init-issuer")
    initialize.add_argument("--directory", type=Path, required=True)
    register = commands.add_parser("enroll")
    for field in ("tenant-id", "campus-id", "student-id", "student-name", "principal-id", "subject"):
        register.add_argument("--" + field, required=True)
    register.add_argument("--role", required=True, choices=["student", "sitting_teacher", "campus_admin"])
    pair = commands.add_parser("pair")
    pair.add_argument("--principal-id", required=True)
    pair.add_argument("--output", type=Path, required=True)
    pair.add_argument("--ttl", type=int, default=900)
    revoke = commands.add_parser("revoke-session")
    revoke.add_argument("--session-id", required=True)
    account = commands.add_parser("set-password-account")
    account.add_argument("--principal-id", required=True)
    account.add_argument("--student-id")
    account.add_argument("--username", required=True)
    account.add_argument("--password-file", type=Path, required=True)
    account.add_argument("--no-must-change", action="store_true", help="Explicit operator override for a permanent password")
    grant = commands.add_parser("grant-campus")
    grant.add_argument("--principal-id", required=True)
    grant.add_argument("--campus-id", required=True)
    bootstrap = commands.add_parser("bootstrap-admin")
    for field in ("tenant-id", "campus-id", "principal-id", "subject", "username"):
        bootstrap.add_argument("--" + field, required=True)
    bootstrap.add_argument("--password-file", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "init-issuer":
        initialize_issuer(args.directory)
        print("Issuer key files created; private key contents are not displayed.")
        return
    cfg = Settings.from_environment()
    engine = create_database_engine(cfg.database_url, allow_sqlite=cfg.allow_sqlite_for_tests)
    try:
        if args.command == "pair":
            signing_key(cfg)
            # Check exclusive report creation before issuing a live credential.
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as file, Session(engine) as session, session.begin():
                code = issue_pairing(session, cfg, args.principal_id, lifetime_seconds=args.ttl)
                json.dump({"pairing_code": code, "issued_at": datetime.now(timezone.utc).isoformat(), "expires_in_seconds": args.ttl}, file)
                file.flush()
                os.fsync(file.fileno())
            print("Pairing credential saved to the specified private file; it is single use with idempotent retry.")
        else:
            with Session(engine) as session, session.begin():
                if args.command == "enroll":
                    values = vars(args).copy()
                    values.pop("command")
                    enroll(session, cfg, **values)
                elif args.command == "set-password-account":
                    from .account_auth import set_password_account
                    from .passwords import read_password_file
                    set_password_account(session, cfg, principal_id=args.principal_id, student_id=args.student_id,
                                         username_value=args.username, password_value=read_password_file(args.password_file),
                                         must_change=not args.no_must_change)
                elif args.command == "grant-campus":
                    from .account_auth import grant_campus
                    grant_campus(session, args.principal_id, args.campus_id)
                elif args.command == "bootstrap-admin":
                    values = vars(args).copy(); values.pop("command")
                    bootstrap_admin(session, cfg, **values)
                else:
                    device = session.get(DeviceSession, args.session_id)
                    if device is None:
                        raise ValueError("Unknown device session")
                    device.revoked = True
            print("Administrator operation committed.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
