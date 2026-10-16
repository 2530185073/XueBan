"""Opt-in live model smoke test of the Mac stack, using only self-authored images."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from uuid import uuid4

import httpx
from PIL import Image
from sqlalchemy.orm import Session

from mac_local import BACKEND, REPO, load, write_private

sys.path.insert(0, str(BACKEND))
from xueban_backend.admin import enroll
from xueban_backend.config import Settings
from xueban_backend.db import create_database_engine
from xueban_backend.device_auth import issue_pairing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path.home() / ".local/share/xueban/local-dev")
    parser.add_argument("--run-live", action="store_true", help="Authorize five paid model jobs on synthetic fixtures")
    args = parser.parse_args()
    if not args.run_live:
        parser.error("Real model requests require --run-live")
    runtime = args.runtime.expanduser().resolve()
    config, env = load(runtime)
    os.environ.update(env)
    cfg = Settings.from_environment()
    engine = create_database_engine(cfg.database_url)
    run_id = uuid4().hex[:12]
    student_id, principal_id = "smoke_" + run_id, "smoke_actor_" + run_id
    with Session(engine) as session, session.begin():
        enroll(session, cfg, tenant_id="local_demo", campus_id="local_demo", student_id=student_id,
               student_name="合成联调作业", principal_id=principal_id, subject=principal_id, role="student")
        code = issue_pairing(session, cfg, principal_id)
    engine.dispose()
    report = {"run_id": run_id, "started_at": datetime.now(timezone.utc).isoformat(),
              "fixture": "evals/correction/fixtures/ark-smoke", "synthetic_only": True, "jobs": [], "operations": []}
    report_file = runtime / ("live-smoke-" + run_id + ".json")
    write_private(report_file, "{}")

    def save():
        descriptor, temporary = tempfile.mkstemp(prefix=".smoke-", dir=runtime)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, report_file)
            directory = os.open(runtime, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    save()

    def expect(response):
        if not response.is_success:
            # Do not echo arbitrary response bodies or credentials into logs.
            raise RuntimeError(f"HTTP {response.status_code} at {response.request.url.path}")
        return response.json() if response.content else None

    with httpx.Client(base_url=config["environment"]["PUBLIC_BASE_URL"], timeout=40, follow_redirects=False) as client:
        def post(path, body):
            operation = {"path": path, "key": str(uuid4()), "status": "response_unknown"}
            report["operations"].append(operation)
            save()  # Record intent before a request that might already be billable.
            response = expect(client.post(path, json=body, headers={"Idempotency-Key": operation["key"]}))
            operation["status"] = "accepted"
            if isinstance(response, dict) and isinstance(response.get("job"), dict):
                job = response["job"]
                operation["job_id"] = job["id"]
                report["jobs"].append({"id": job["id"], "kind": job["kind"], "status": job["status"]})
            save()  # Keep accepted job IDs even if polling is interrupted.
            return response

        def get(path):
            return expect(client.get(path))

        def job_wait(job):
            started = time.monotonic()
            while time.monotonic() - started < 360:
                document = get("/v1/jobs/" + job["id"])
                if document["status"] in {"succeeded", "failed", "cancelled", "superseded"}:
                    entry = next(item for item in report["jobs"] if item["id"] == job["id"])
                    entry.update(seconds=round(time.monotonic() - started, 2), status=document["status"], failure=document.get("failure"))
                    save()
                    print("Job", job["kind"], document["status"], flush=True)
                    if document["status"] != "succeeded":
                        raise RuntimeError("Model job did not succeed; inspect private smoke report")
                    return document
                time.sleep(1)
            raise RuntimeError("Bounded job polling deadline exceeded")

        try:
            token = post("/v1/device-sessions", {"pairing_code": code})["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            identity = get("/v1/me")
            report["identity_verified"] = bool(identity)
            homework = post(f"/v1/students/{student_id}/homeworks", {
                "curriculum": {"stage": "junior_middle", "publisher_code": "PEP", "grade": None, "subject": None, "volume": None, "edition": None},
                "title": "Mac 联网闭环验证（合成题）", "occurred_at": datetime.now(timezone.utc).isoformat(), "collection_scope": "partial"})
            homework_id = homework["id"]
            report["homework_id"] = homework_id

            def hw():
                return get("/v1/homeworks/" + homework_id)

            def upload(filename):
                file = REPO / "evals/correction/fixtures/ark-smoke" / filename
                content = file.read_bytes()
                with Image.open(file) as image:
                    width, height = image.size
                current = hw()
                ticket = post(f"/v1/homeworks/{homework_id}/uploads", {
                    "expected_revision": current["revision"], "page_index": len(current["asset_ids"]),
                    "sha256": hashlib.sha256(content).hexdigest(), "byte_size": len(content), "mime_type": "image/png", "width": width, "height": height})
                # Upload capability only, never attach the student's bearer to a signed URL.
                with httpx.Client(timeout=40, follow_redirects=False) as uploader:
                    expect(uploader.put(ticket["put_url"], content=content, headers=ticket["required_headers"]))
                asset = ticket["asset"]
                return post(f"/v1/assets/{asset['id']}/complete", {"expected_revision": asset["revision"], "sha256": asset["sha256"], "byte_size": asset["byte_size"]})

            asset = upload("original.png")
            job_wait(post(f"/v1/homeworks/{homework_id}/recognitions", {"expected_revision": hw()["revision"], "asset_ids": [asset["id"]]})["job"])
            ids = hw()["case_ids"]
            if len(ids) != 1:
                raise RuntimeError("Expected one located question from synthetic fixture")
            case_id = ids[0]

            def case():
                return get("/v1/cases/" + case_id)

            def check(expected):
                current = case()
                attempt = get("/v1/attempts/" + current["current_attempt_id"])["record"]
                job_wait(post(f"/v1/cases/{case_id}/checks", {"expected_revision": current["revision"], "attempt_id": attempt["id"],
                    "question_revision": current["current_question_revision"], "attempt_recognition_revision": attempt["recognition_revision"]})["job"])
                decision = case()["current_decision"]
                record = get("/v1/decisions/" + decision["id"])["record"]
                report.setdefault("decisions", []).append(record)
                save()
                if record["verdict"] != expected or record["validity"] != "candidate":
                    raise RuntimeError("Live result differs from synthetic expectation; inspect private smoke report")

            check("incorrect")
            current = case()
            decision = current["current_decision"]
            result = job_wait(post(f"/v1/cases/{case_id}/hints", {"expected_revision": current["revision"], "attempt_id": current["current_attempt_id"],
                "source_decision_id": decision["id"], "source_decision_revision": decision["revision"], "level": "direction"})["job"])
            hint = get("/v1/hints/" + result["result"]["resource_id"])
            report["hint"] = hint["content"]
            post(f"/v1/hints/{hint['id']}/views", {"expected_revision": hint["revision"], "shown_at": datetime.now(timezone.utc).isoformat()})
            asset = upload("corrected.png")
            current = case()
            job_wait(post(f"/v1/cases/{case_id}/attempts", {"expected_revision": current["revision"], "parent_attempt_id": current["current_attempt_id"],
                "question_revision": current["current_question_revision"], "asset_ids": [asset["id"]], "occurred_at": datetime.now(timezone.utc).isoformat(), "reported_help": "none_reported"})["job"])
            check("correct")
            report["final_case"] = case()
            report["history_verified"] = any(item["id"] == homework_id for item in get(f"/v1/students/{student_id}/homeworks")["items"])
            assert report["history_verified"]
            assert case()["progress"] != "verified_resolved", "Model result must not impersonate staff confirmation"
            report["passed"] = True
            print("Live synthetic correction loop passed. Private report:", report_file)
        finally:
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            save()


if __name__ == "__main__":
    main()
