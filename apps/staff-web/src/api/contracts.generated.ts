// Generated from contracts/phase1; do not edit by hand.
// Cross-field constraints remain enforced by the source JSON Schemas.

export type JobStatus = "queued" | "running" | "succeeded" | "retryable_failed" | "failed" | "superseded" | "cancelled"

export type DecisionValidity = "candidate" | "needs_review" | "confirmed" | "retracted" | "stale"

export type Verdict = "correct" | "incorrect" | "partially_correct" | "unanswered" | "unable_to_judge"

export type CorrectionProgress = "pending" | "editing" | "rechecking" | "needs_correction" | "verified_resolved" | "paused"

export type HelpStatus = "waiting" | "claimed" | "resolved" | "unresolved" | "cancelled"

export type AssetStatus = "awaiting_upload" | "uploaded" | "rejected" | "deletion_pending" | "deleted"

export type DeletionStatus = "accepted" | "running" | "waiting_devices" | "completed" | "failed"

export type Role = "student" | "sitting_teacher" | "campus_admin" | "data_steward"

export type RecordLifecycle = "active" | "superseded" | "deletion_pending"

export type Scope = { "tenant_id": string; "campus_id": string; "student_id": string }

export type CurriculumContext = { "stage": "junior_middle"; "publisher_code": "PEP"; "grade": 7 | 8 | 9 | null; "subject": string | null; "volume": "first" | "second" | "full_year" | null; "edition": string | null }

export type Region = { "asset_id": string; "x": number; "y": number; "width": number; "height": number; "coordinate_space": "original_pixels" }

export type Coverage = { "collection_scope": "partial" | "declared_complete" | "unknown"; "unrecognized_regions": Array<Region>; "note": string | null }

export type AuthorizedStudent = { "student_id": string; "display_name": string; "campus_id": string }

export type IdentityContext = { "actor_id": string; "roles": Array<Role>; "authorized_students": Array<AuthorizedStudent> }

export type HomeworkSession = { "id": string; "revision": number; "scope": Scope; "curriculum": CurriculumContext; "title": string | null; "created_at": string; "occurred_at": string; "coverage": Coverage; "asset_ids": Array<string>; "question_ids": Array<string>; "case_ids": Array<string>; "job_ids": Array<string>; "processing_blocked": boolean }

export type ImageAsset = { "id": string; "revision": number; "homework_id": string; "status": AssetStatus; "page_index": number; "sha256": string; "byte_size": number; "mime_type": "image/jpeg" | "image/png" | "image/webp"; "width": number; "height": number; "source_asset_id": string | null; "source_region": Region | null; "created_at": string }

export type UploadTicket = { "asset": ImageAsset; "put_url": string; "expires_at": string; "required_headers": {  } }

export type AssetAccess = { "asset_id": string; "read_url": string; "expires_at": string }

export type CapturedQuestionRevision = { "question_id": string; "revision": number; "homework_id": string; "stem_text": string | null; "question_type"?: "single_choice" | "multiple_choice" | "fill_blank" | "calculation" | "application" | "proof" | "unknown"; "options"?: Array<{ "label": string; "text": string }>; "blank_count"?: number; "recognition_uncertainties"?: Array<string>; "regions": Array<Region>; "source": "recognizer" | "staff_revision" | "staff_layout"; "source_actor_id": string | null; "bank_question_id": string | null; "knowledge_point_ids": Array<string> | null; "created_at": string; "lifecycle": RecordLifecycle; "replacement_question_ids": Array<string> }

export type Attempt = { "id": string; "revision": number; "case_id": string; "question_id": string; "captured_question_revision": number; "parent_attempt_id": string | null; "root_attempt_id": string; "asset_ids": Array<string>; "recognition_revision": number; "answer_text": string | null; "answer_regions": Array<Region>; "answer_status"?: "present" | "blank" | "unclear"; "recognition_uncertainties"?: Array<string>; "recognition_status": "pending" | "ready" | "needs_revision" | "failed"; "help_condition": "unknown" | "none_reported" | "hint_used" | "staff_help" | "answer_shown"; "occurred_at": string; "created_at": string }

export type InputVersions = { "question_id": string; "question_revision": number; "attempt_id": string; "attempt_recognition_revision": number; "case_revision_at_submission": number; "fingerprint": string }

export type Basis = { "kind": "calculation" | "equivalence" | "reviewed_reference" | "model_hypothesis" | "staff_observation"; "summary": string; "source_ref": string | null; "regions": Array<Region> }

export type Decision = { "id": string; "revision": number; "case_id": string; "check_id": string | null; "input_versions": InputVersions; "origin": "model" | "rule" | "staff"; "validity": DecisionValidity; "verdict": Verdict; "reason_code": "unclear_image" | "incomplete_question" | "unsupported_type" | "recognition_conflict" | "verification_conflict" | "insufficient_process" | "needs_staff" | "other" | null; "knowledge_points"?: Array<string>; "correction_method"?: string | null; "uncertainties"?: Array<string>; "basis": Array<Basis>; "first_error_region": Region | null; "confirmation_source": "staff_review" | null; "confirmed_by": string | null; "confirmed_at": string | null; "replaces_decision_id": string | null; "invalidated_by_event_id": string | null; "created_at": string }

export type TechnicalFailure = { "code": "provider_timeout" | "provider_unavailable" | "storage_error" | "invalid_provider_output" | "worker_failure"; "message": string; "retryable": boolean }

export type ResultRef = { "resource_type": "homework" | "attempt" | "check" | "hint" | "deletion"; "resource_id": string }

export type Job = { "id": string; "revision": number; "kind": "recognize_homework" | "recognize_attempt" | "check" | "hint" | "delete" | "repair_layout"; "status": JobStatus; "created_at": string; "updated_at": string; "retry_after_seconds": number | null; "failure": TechnicalFailure | null; "result": ResultRef | null }

export type Accepted = { "job": Job; "created_resource": ResultRef | null }

export type CheckRun = { "id": string; "revision": number; "job_id": string; "case_id": string; "input_versions": InputVersions; "model_version": string | null; "rule_version": string | null; "prompt_version": string | null; "decision_ids": Array<string>; "created_at": string }

export type Hint = { "id": string; "revision": number; "case_id": string; "attempt_id": string; "source_decision_id": string; "source_decision_revision": number; "level": "direction" | "key_step" | "staff_handoff"; "content": string; "exposes_answer": boolean; "source_is_provisional": boolean; "validity": "active" | "stale" | "retracted"; "first_shown_at": string | null; "created_at": string }

export type HelpRequest = { "id": string; "revision": number; "case_id": string; "attempt_id": string; "kind": "question" | "recognition" | "judgment" | "appeal"; "note": string; "status": HelpStatus; "claimed_by": string | null; "created_at": string; "claimed_at": string | null; "completed_at": string | null; "resolution_note": string | null }

export type Review = { "id": string; "case_id": string; "target_decision_id": string | null; "target_decision_revision": number | null; "action": "confirm" | "replace" | "retract" | "leave_unresolved" | "manual_initial"; "new_decision_id": string | null; "reason": string; "reviewer_id": string; "created_at": string }

export type CorrectionCase = { "id": string; "revision": number; "scope": Scope; "homework_id": string; "question_id": string; "current_question_revision": number; "root_attempt_id": string; "current_attempt_id": string; "progress": CorrectionProgress; "current_decision": Decision | null; "attempt_ids": Array<string>; "check_ids": Array<string>; "hint_ids": Array<string>; "help_request_ids": Array<string>; "review_ids": Array<string>; "created_at": string; "updated_at": string; "lifecycle": RecordLifecycle; "replacement_case_ids": Array<string> }

export type DeletionTargets = { "original_assets": "pending" | "done" | "not_applicable"; "derived_assets": "pending" | "done" | "not_applicable"; "sensitive_records": "pending" | "done" | "not_applicable"; "device_caches": "pending" | "done" | "not_applicable"; "backup_tombstone": "pending" | "done" | "not_applicable" }

export type DeletionRequest = { "id": string; "revision": number; "scope": Scope; "status": DeletionStatus; "targets": DeletionTargets; "requested_at": string; "completed_at": string | null }

export type Problem = { "type": string; "title": string; "status": 401 | 403 | 404 | 409 | 422 | 429 | 503; "category": "authorization" | "not_found" | "conflict" | "validation" | "service"; "code": "authentication_required" | "access_denied" | "not_found" | "revision_conflict" | "stale_input" | "idempotency_conflict" | "invalid_request" | "service_unavailable" | "invalid_credentials" | "invalid_asset_ticket" | "forbidden" | "password_change_required" | "processing_blocked" | "recognition_not_ready" | "help_recorded" | "username_conflict" | "deletion_already_requested" | "projection_pending" | "report_changed" | "optional_gate_closed" | "practice_not_needed" | "course_unknown" | "no_reviewed_items" | "hints_disabled" | "practice_active" | "asset_integrity_mismatch" | "asset_unavailable" | "image_batch_too_large" | "image_too_large" | "invalid_image" | "invalid_region" | "too_many_replacements" | "upload_timeout" | "rate_limited" | "login_rate_limited" | "deletion_ledger_unconfigured" | "storage_error" | "storage_unconfigured"; "detail": string; "request_id": string; "current_revision": number | null; "retry_after_seconds": number | null }

export type HomeworkPage = { "items": Array<HomeworkSession>; "next_cursor": string | null }

export type HelpPage = { "items": Array<HelpRequest>; "next_cursor": string | null }

export type QuestionRead = { "record": CapturedQuestionRevision; "current_revision": number; "currently_effective": boolean }

export type AttemptRead = { "record": Attempt; "current_revision": number; "currently_effective": boolean }

export type DecisionRead = { "record": Decision; "current_revision": number; "currently_effective": boolean }

export type CreateHomework = { "curriculum": CurriculumContext; "title": string | null; "occurred_at": string; "collection_scope": "partial" | "declared_complete" | "unknown" }

export type CreateUpload = { "expected_revision": number; "page_index": number; "sha256": string; "byte_size": number; "mime_type": "image/jpeg" | "image/png" | "image/webp"; "width": number; "height": number }

export type CompleteUpload = { "expected_revision": number; "sha256": string; "byte_size": number }

export type StartRecognition = { "expected_revision": number; "asset_ids": Array<string> }

export type ReviseQuestion = { "expected_revision": number; "stem_text": string; "regions": Array<Region>; "reason": string }

export type ReviseAnswer = { "expected_revision": number; "answer_text": string | null; "answer_regions": Array<Region>; "reason": string }

export type StartCheck = { "expected_revision": number; "attempt_id": string; "question_revision": number; "attempt_recognition_revision": number }

export type RequestHint = { "expected_revision": number; "attempt_id": string; "source_decision_id": string; "source_decision_revision": number; "level": "direction" | "key_step" | "staff_handoff" }

export type ShowHint = { "expected_revision": number; "shown_at": string }

export type CreateCorrection = { "expected_revision": number; "parent_attempt_id": string; "question_revision": number; "asset_ids": Array<string>; "occurred_at": string; "reported_help": "unknown" | "none_reported" | "staff_help" | "answer_shown" }

export type CreateHelp = { "expected_revision": number; "attempt_id": string; "kind": "question" | "recognition" | "judgment" | "appeal"; "note": string }

export type ClaimHelp = { "expected_revision": number }

export type CompleteHelp = { "expected_revision": number; "outcome": "resolved" | "unresolved"; "note": string }

export type SubmitReview = { "expected_revision": number; "target_decision_id": string | null; "target_decision_revision": number | null; "action": "confirm" | "replace" | "retract" | "leave_unresolved" | "manual_initial"; "verdict": Verdict | null; "reason": string; "basis": Array<Basis>; "attempt_id": string; "question_revision": number; "attempt_recognition_revision": number }

export type SetProgress = { "expected_revision": number; "action": "pause" | "resume" }

export type RequestDeletion = { "reason": string; "confirmation": "delete_authorized_student_data" }

export type RenewUpload = { "expected_revision": number }

export type RepairLayout = { "expected_revision": number; "supersede_questions": Array<{ "question_id": string; "expected_revision": number }>; "replacement_regions": Array<{ "regions": Array<Region>; "stem_text": string | null }>; "reason": string }
