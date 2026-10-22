#!/usr/bin/env python3
"""Offline checks for the draft contract, never a running-service test."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from urllib.parse import unquote, urldefrag, urljoin

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent
BASE = "https://schemas.xueban.invalid/phase1/"
SCHEMA_FILES = (
    "states.schema.json",
    "data.schema.json",
    "requests.schema.json",
    "events.schema.json",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def pointer(document: object, fragment: str) -> object:
    node = document
    if not fragment:
        return node
    require(fragment.startswith("/"), f"Unsupported reference anchor: {fragment}")
    for token in unquote(fragment[1:]).split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        node = node[int(token)] if isinstance(node, list) else node[token]
    return node


def walk_refs(value: object):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for child in value.values():
            yield from walk_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_refs(child)


def main() -> None:
    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in ROOT.glob("*.json")
    }
    registry = Registry()
    for filename in SCHEMA_FILES:
        document = documents[filename]
        Draft202012Validator.check_schema(document)
        require(document["$id"] == BASE + filename, f"Unexpected schema ID: {filename}")
        registry = registry.with_resource(document["$id"], Resource.from_contents(document))

    ref_count = 0
    for filename in (*SCHEMA_FILES, "openapi.json"):
        for reference in walk_refs(documents[filename]):
            url, fragment = urldefrag(urljoin(BASE + filename, reference))
            require(url.startswith(BASE), f"Network reference prohibited: {reference}")
            target = url.removeprefix(BASE)
            require(target in documents, f"Missing reference file: {target}")
            pointer(documents[target], fragment)
            ref_count += 1

    def validator(reference: str) -> Draft202012Validator:
        return Draft202012Validator(
            {"$ref": urljoin(BASE, reference)},
            registry=registry,
            format_checker=FormatChecker(),
        )

    api = documents["openapi.json"]
    require(api["openapi"] == "3.1.0", "Use the declared OpenAPI 3.1 dialect")
    require(api["x-status"] == "draft", "The contract must remain explicitly draft")
    require(api["security"] == [{"BearerAuth": []}], "Real bearer identity required")
    security = api["components"]["securitySchemes"]["BearerAuth"]
    require(security["type"] == "http" and security["scheme"] == "bearer", "Invalid auth scheme")
    operation_ids = set()
    request_references = set()
    for path, methods in api["paths"].items():
        for method, operation in methods.items():
            require(method in {"get", "post"}, f"Unexpected draft method: {method}")
            require(operation["operationId"] not in operation_ids, "Duplicate operationId")
            operation_ids.add(operation["operationId"])
            require(operation.get("security", api["security"]), f"Anonymous operation: {path}")
            require(operation["x-allowed-roles"], f"Missing role boundary: {path}")
            parameters = []
            for parameter in operation.get("parameters", []):
                if "$ref" in parameter:
                    parameter = pointer(api, urldefrag(parameter["$ref"])[1])
                parameters.append(parameter)
            actual = {param["name"] for param in parameters if param["in"] == "path"}
            require(actual == set(re.findall(r"{([^}]+)}", path)), f"Path params mismatch: {path}")
            require(all(param.get("required") for param in parameters if param["in"] == "path"), "Optional path parameter")
            if method != "get":
                require(any(param["name"] == "Idempotency-Key" and param.get("required") for param in parameters), f"Missing idempotency: {path}")
                request = operation["requestBody"]
                require(request["required"], f"Optional command body: {path}")
                request_references.add(request["content"]["application/json"]["schema"]["$ref"])
            require(any(code.startswith("2") for code in operation["responses"]), f"No successful response: {path}")
            require({"401", "403", "409", "422", "503"} <= operation["responses"].keys(), f"Missing failure boundary: {path}")

    fixtures = documents["examples.json"]["entries"]
    by_name = {}
    for entry in fixtures:
        require(entry["name"] not in by_name, "Duplicate fixture name")
        validator(entry["schema"]).validate(entry["value"])
        by_name[entry["name"]] = entry
    require(request_references <= {entry["schema"] for entry in fixtures}, "A command has no validated request example")

    negative_count = 0

    def reject(name: str, mutate, message: str) -> None:
        nonlocal negative_count
        entry = by_name[name]
        value = copy.deepcopy(entry["value"])
        mutate(value)
        require(not validator(entry["schema"]).is_valid(value), message)
        negative_count += 1

    reject("model_candidate", lambda value: value.update(validity="confirmed"), "Model output must not confirm itself")
    reject("staff_confirmed", lambda value: value.update(confirmed_by=None), "Confirmation requires an attributable staff member")
    reject("business_unable_to_judge", lambda value: value.update(validity="confirmed"), "Unable-to-judge must not become confirmed")
    reject("technical_failure", lambda value: value.update(result={"resource_type": "check", "resource_id": "check_fake"}), "Failed jobs cannot have successful output")
    reject("successful_check_job", lambda value: value.update(failure={"code": "worker_failure", "message": "failed", "retryable": False}), "Succeeded jobs cannot also fail")
    reject("request_StartCheck", lambda value: value.pop("expected_revision"), "Checks require optimistic concurrency")
    for field in ("expected_revision", "attempt_id", "question_revision", "attempt_recognition_revision"):
        reject("request_StartRecognitionRecheck", lambda value, field=field: value.pop(field),
               "Recognition recovery requires all optimistic concurrency inputs")
    reject("request_StartRecognitionRecheck", lambda value: value.update(remaining=2),
           "Clients cannot supply or reset the recovery budget")
    reject("request_StartRecognitionRecheck", lambda value: value.update(question_revision=0),
           "Recognition recovery requires a real question revision")
    reject("recognition_recovery_available", lambda value: value.update(limit=3), "Recovery limit is fixed at two")
    reject("recognition_recovery_available", lambda value: value.update(used=3), "Used recovery count is bounded")
    reject("recognition_recovery_available", lambda value: value.update(remaining=-1), "Remaining recovery count cannot be negative")
    reject("recognition_recovery_available", lambda value: value.update(used=True), "Boolean is not a recovery counter")
    reject("recognition_recovery_available", lambda value: value.update(last_outcome="correct"),
           "Recovery completion is not a correctness verdict")
    reject("recognition_recovery_available", lambda value: value.update(blocked_reason="unknown"),
           "Recovery blocking reason must be explicit")
    reject("recognition_recovery_available", lambda value: value.pop("active_job_id"),
           "Recovery state explicitly includes nullable active job identity")
    reject("recognition_recovery_available", lambda value: value.update(student_id="student_other"),
           "Recovery response has a fixed ownership-safe shape")
    reject("accepted_recognition_recheck", lambda value: value.pop("recovery"),
           "Accepted recovery must expose its bounded status")
    reject("successful_recognition_recheck_job", lambda value: value.update(kind="recognition_recheck_unbounded"),
           "Only the declared recognition recovery job is supported")
    reject("request_CreateHomework", lambda value: value.update(student_id="student_other"), "Command cannot inject another student's ownership")
    reject("request_SubmitReview", lambda value: value.update(confirmed_by="staff_forged"), "Client cannot choose reviewer identity")
    reject("request_SetProgress", lambda value: value.update(action="complete"), "Clicking done cannot assert correctness")
    reject("request_CreateCorrection", lambda value: value.pop("parent_attempt_id"), "Correction must retain its parent")
    reject("request_CreateCorrection", lambda value: value.update(root_attempt_id="attempt_forged"), "Root attempt is server-derived")
    reject("resolved_case", lambda value: value["current_decision"].update(validity="candidate"), "A candidate cannot resolve a case")
    reject("deletion_waiting_for_device", lambda value: value.update(status="completed", completed_at="2026-10-16T08:00:00Z"), "Pending device cleanup cannot be declared completed")
    reject("event_question.revised", lambda value: value.update(aggregate_type="student"), "Event aggregate type must match its purpose")
    reject("request_RepairLayout", lambda value: value.update(replacement_regions=[]), "Layout repair must provide replacement regions")
    reject("request_RenewUpload", lambda value: value.update(asset_id="asset_other"), "Renewal uses the path asset and cannot create/reassign it")
    reject("request_manual_initial_without_ai", lambda value: value.update(target_decision_id="decision_fake"), "Manual initial review must not fabricate an AI decision")
    reject("request_SubmitReview", lambda value: value.update(target_decision_id=None), "Confirming a candidate needs its target decision")
    reject("model_candidate", lambda value: value.update(check_id=None), "A model decision cannot claim the manual-without-check path")

    # Fixture-level cross-reference checks verify that the demonstration actually
    # exercises cross-revision correction. They do not pretend to implement DB/auth checks.
    original = by_name["original_attempt"]["value"]
    correction = by_name["correction_across_question_revision"]["value"]
    require(correction["parent_attempt_id"] == original["id"], "Broken parent example")
    require(correction["root_attempt_id"] == original["root_attempt_id"], "Broken root example")
    require(correction["question_id"] == original["question_id"], "Examples must retain captured-question identity")
    require(correction["captured_question_revision"] != original["captured_question_revision"], "Cross-revision correction not exercised")
    require(by_name["question_revision_1"]["value"]["bank_question_id"] is None, "No-bank fixture missing")
    require(by_name["question_revision_1"]["value"]["knowledge_point_ids"] is None, "No-knowledge-map fixture missing")

    recovery_post = api["paths"]["/v1/cases/{case_id}/recognition-rechecks"]["post"]
    recovery_get = api["paths"]["/v1/cases/{case_id}/recognition-rechecks/status"]["get"]
    check_post = api["paths"]["/v1/cases/{case_id}/checks"]["post"]
    require(recovery_post["x-allowed-roles"] == recovery_get["x-allowed-roles"] == check_post["x-allowed-roles"],
            "Recognition recovery uses the same actor boundary as checks")
    require(recovery_post["operationId"] == "startRecognitionRecheck"
            and recovery_get["operationId"] == "getRecognitionRecheckStatus", "Stable recovery operations required")
    require(recovery_post["responses"]["202"]["content"]["application/json"]["schema"]["$ref"]
            == "data.schema.json#/$defs/RecognitionRecheckAccepted", "Recovery acceptance must include its state")
    require(recovery_get["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
            == "data.schema.json#/$defs/RecognitionRecoveryStatus", "Recovery state endpoint must use the shared shape")
    accepted = by_name["accepted_recognition_recheck"]["value"]
    require(accepted["job"]["kind"] == "recognition_recheck"
            and accepted["job"]["id"] == accepted["recovery"]["active_job_id"], "Broken recovery task example")
    require(accepted["recovery"]["attempt_id"] == by_name["request_StartRecognitionRecheck"]["value"]["attempt_id"],
            "Recovery request and acceptance must refer to the same attempt")
    for name in ("recognition_recovery_available", "recognition_recovery_running", "recognition_recovery_exhausted"):
        state = by_name[name]["value"]
        require(state["used"] + state["remaining"] == state["limit"] == 2, "Inconsistent synthetic recovery budget")

    print(f"PASS: {len(SCHEMA_FILES)} JSON Schemas, {ref_count} local references, {len(operation_ids)} API operations")
    print(f"PASS: {len(fixtures)} synthetic fixtures and {negative_count} negative contract checks")
    print("Boundary: contract structure only; no server, auth, persistence, OCR accuracy, or device behavior verified")


if __name__ == "__main__":
    main()
