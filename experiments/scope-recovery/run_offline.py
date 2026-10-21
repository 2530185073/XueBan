#!/usr/bin/env python3
"""Reproduce the frozen Mock-only experiment in a new, isolated checkout.

Uses only the standard library; never installs dependencies or loads runtime
configuration. PostgreSQL is opt-in and uses the existing isolated-schema fixture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import quote, unquote, urlsplit

BASE = "03aa3a2cd58089d49fb30acadf7e0dc6005e0595"
MODULES = (
    "providers/scope_recovery_contract.py", "providers/scope_recovery_materializer.py",
    "scope_recovery.py", "scope_recovery_dispatch.py", "scope_recovery_apply.py",
)
TESTS = tuple("tests/test_scope_recovery_" + name + ".py" for name in
              ("contract", "materializer", "durable", "dispatch", "apply")) + (
    "tests/test_recognition_recheck.py",
)
ADDED = tuple("services/backend/xueban_backend/" + name for name in MODULES) + tuple(
    "services/backend/" + name for name in TESTS[:-1]
)
MODIFIED = ("services/backend/xueban_backend/recognition_recheck.py",
            "contracts/phase1/data.schema.json")
EXPECTED_PATHS = frozenset(ADDED + MODIFIED)
PACKAGE_ARTIFACTS = frozenset(("README.md", "run_offline.py", "test_run_offline.py"))
ENV_ALLOWLIST = frozenset(("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TMP", "TEMP",
                           "SYSTEMROOT", "WINDIR"))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_environment(source: dict[str, str], *, require_postgres: bool = False) -> dict[str, str]:
    env = {key: value for key, value in source.items() if key in ENV_ALLOWLIST}
    env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull)
    if require_postgres:
        url = source.get("BACKEND_TEST_DATABASE_URL", "")
        if not url or urlsplit(url).scheme.split("+", 1)[0] not in ("postgresql", "postgres"):
            raise ValueError("--require-postgres needs an explicit PostgreSQL BACKEND_TEST_DATABASE_URL")
        env["BACKEND_TEST_DATABASE_URL"] = url
    return env


def redact(text: str, database_url: str = "") -> str:
    if database_url:
        values = {database_url, unquote(database_url)}
        password = urlsplit(database_url).password
        if password:
            values.update((password, unquote(password), quote(unquote(password), safe="")))
        for value in sorted(values, key=len, reverse=True):
            if value:
                text = text.replace(value, "[REDACTED_DATABASE_CONNECTION]")
    return re.sub(r"postgres(?:ql)?(?:\+[\w]+)?://[^\s\"'<>]+", "[REDACTED_DATABASE_CONNECTION]", text)


def check_package(package: Path) -> dict:
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("base_commit") != BASE or manifest.get("tests") != list(TESTS):
        raise ValueError("Manifest base or test scope mismatch")
    files = manifest.get("files", {})
    if set(files) != EXPECTED_PATHS:
        raise ValueError("Manifest contains an unexpected patch file set")
    patch = (package / "candidate.patch").read_bytes()
    if sha(patch) != manifest.get("patch_sha256"):
        raise ValueError("Patch checksum mismatch")
    headers = re.findall(rb"^diff --git a/(\S+) b/(\S+)$", patch, re.M)
    paths = [left.decode("utf-8") for left, right in headers if left == right]
    if len(headers) != len(EXPECTED_PATHS) or len(paths) != len(set(paths)) or set(paths) != EXPECTED_PATHS:
        raise ValueError("Patch headers exceed the frozen file scope")
    if re.search(rb"^(?:rename (?:from|to)|copy (?:from|to)|deleted file mode|GIT binary patch)", patch, re.M):
        raise ValueError("Only the frozen text additions and modifications are allowed")
    artifacts = manifest.get("package_artifacts", {})
    if set(artifacts) != PACKAGE_ARTIFACTS:
        raise ValueError("Package artifact set mismatch")
    for path, expected in artifacts.items():
        if sha((package / path).read_bytes()) != expected:
            raise ValueError("Package artifact checksum mismatch: " + path)
    for path, entry in files.items():
        if entry.get("status") != ("added" if path in ADDED else "modified"):
            raise ValueError("Unexpected file operation: " + path)
        for field in ("sha256",) + (() if path in ADDED else ("base_sha256",)):
            if not re.fullmatch(r"[0-9a-f]{64}", entry.get(field, "")):
                raise ValueError("Invalid manifest checksum: " + path)
    return manifest


def new_destination(path: Path) -> None:
    if os.path.lexists(path):
        raise ValueError("Destination already exists; nothing was overwritten")
    if not path.parent.is_dir():
        raise ValueError("Destination parent must already exist")
    physical_parent = path.parent.resolve()
    if any(os.path.lexists(parent / ".git") for parent in (physical_parent, *physical_parent.parents)):
        raise ValueError("Destination must be outside every existing Git worktree")
    path.mkdir(mode=0o700, exist_ok=False)


def extract_regular_archive(archive, destination: Path) -> None:
    seen = set()
    with tarfile.open(fileobj=archive, mode="r:") as tar:
        members = tar.getmembers()
        for member in members:
            if hasattr(tarfile, "data_filter"):
                tarfile.data_filter(member, str(destination))
            name = member.name.rstrip("/")
            path = PurePosixPath(name)
            if (not name or path.is_absolute() or ".." in path.parts or "\\" in name
                    or path.as_posix() != name or name in seen
                    or not (member.isfile() or member.isdir())):
                raise ValueError("Archive contains an unsafe or nonregular path")
            seen.add(name)
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = tar.extractfile(member)
                if stream is None:
                    raise ValueError("Archive file is unreadable")
                with stream, target.open("xb") as output:
                    shutil.copyfileobj(stream, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)


def run_git(args: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode:
        # Avoid copying arbitrary local Git configuration/error text into reports.
        raise ValueError("Git operation failed: " + args[0])
    return result.stdout.strip()


def check_files(destination: Path, manifest: dict, *, applied: bool) -> None:
    for name, entry in manifest["files"].items():
        path = destination / name
        if not applied and entry["status"] == "added":
            if os.path.lexists(path):
                raise ValueError("An added file already exists in the base: " + name)
            continue
        expected = entry["sha256" if applied else "base_sha256"]
        if path.is_symlink() or not path.is_file() or sha(path.read_bytes()) != expected:
            raise ValueError("Source checksum mismatch: " + name)


def prepare(package: Path, repository: Path, destination: Path, env: dict[str, str]) -> dict:
    manifest = check_package(package)
    actual = run_git(["rev-parse", "--verify", BASE + "^{commit}"], cwd=repository, env=env)
    if actual != BASE:
        raise ValueError("Required base commit is not available locally")
    new_destination(destination)
    with tempfile.TemporaryFile() as archive:
        result = subprocess.run(["git", "archive", "--format=tar", BASE], cwd=repository,
                                env=env, stdout=archive, stderr=subprocess.PIPE)
        if result.returncode:
            raise ValueError("Git archive failed; the new directory was retained")
        archive.seek(0)
        extract_regular_archive(archive, destination)
    check_files(destination, manifest, applied=False)
    patch_path = str(package / "candidate.patch")
    run_git(["apply", "--check", patch_path], cwd=destination, env=env)
    run_git(["apply", patch_path], cwd=destination, env=env)
    check_files(destination, manifest, applied=True)
    return manifest


def interpreter(value: str) -> str:
    path = shutil.which(value) if os.sep not in value else os.path.abspath(os.path.expanduser(value))
    if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
        raise ValueError("--python must identify an existing executable with test dependencies")
    # Do not resolve the virtualenv Python symlink: that would lose pyvenv.cfg.
    return os.path.abspath(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, help="A new directory whose parent exists")
    parser.add_argument("--python", required=True, help="Existing Python with backend test dependencies")
    parser.add_argument("--repository", help="Local Git repository containing the fixed base (default: this repository)")
    parser.add_argument("--prepare-only", action="store_true", help="Verify and apply; do not execute tests")
    parser.add_argument("--require-postgres", action="store_true", help="Also require the existing isolated PostgreSQL fixture")
    args = parser.parse_args(argv)
    package = Path(__file__).resolve().parent
    destination = Path(os.path.abspath(os.path.expanduser(args.destination)))
    repository = Path(args.repository).expanduser().absolute() if args.repository else package.parents[1]
    database_url = os.environ.get("BACKEND_TEST_DATABASE_URL", "") if args.require_postgres else ""
    try:
        executable = interpreter(args.python)
        env = clean_environment(dict(os.environ), require_postgres=args.require_postgres)
        manifest = prepare(package, repository, destination, env)
        report = {"base_commit": BASE, "patch_sha256": manifest["patch_sha256"],
                  "source_hashes_verified": True, "mode": "mock_only",
                  "postgresql_requested": args.require_postgres, "tests": list(TESTS),
                  "tests_executed": False, "model_requests": 0}
        if args.prepare_only:
            report["status"] = "prepared_only"
            (destination / "scope-recovery-offline-result.json").write_text(json.dumps(report, indent=2) + "\n")
            print("Prepared and verified the isolated source. Tests were not run.")
            return 0
        backend = destination / "services/backend"
        env["PYTHONPATH"] = os.pathsep.join((str(backend), str(backend / "tests")))
        command = [executable, "-m", "pytest", "-q"]
        if args.require_postgres:
            command.append("--require-postgres")
        command.extend(TESTS)
        result = subprocess.run(command, cwd=backend, env=env, capture_output=True, text=True)
        output = redact(result.stdout + result.stderr, database_url)
        (destination / "scope-recovery-offline-pytest.log").write_text(output, encoding="utf-8")
        report.update(tests_executed=True, returncode=result.returncode,
                      status="passed" if result.returncode == 0 else "failed",
                      summary_lines=[line for line in output.splitlines()
                                     if re.search(r"\d+ (?:passed|failed|error|skipped)", line)][-3:])
        (destination / "scope-recovery-offline-result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(output, end="" if output.endswith("\n") else "\n")
        return result.returncode
    except Exception as error:
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        print("Offline experiment stopped: " + redact(detail, database_url), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
