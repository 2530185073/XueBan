-- 学伴 v3.1 / custody homework support / PostgreSQL 16+ target, 25 tables.
-- tenant = custody operator; teacher = on-site homework support/review permission.
-- No supplementary teaching program is implied by these technical role names.
-- DRAFT CONTRACT: NOT a completed deployment. Requires real PostgreSQL migration,
-- rollback/restore, concurrent-write, privilege and RLS integration tests before use.
-- Static validation on 2026-10-16: pglast PostgreSQL grammar parser accepted 90
-- top-level statements. No PostgreSQL server execution has been performed.
-- Parser acceptance is NOT server execution, FK resolution, PL/pgSQL or RLS validation.
-- No demonstration identity tokens, seed children or production credentials here.
-- Accessed 2026-10-16: https://www.postgresql.org/docs/current/ddl-constraints.html
-- https://www.postgresql.org/docs/current/ddl-rowsecurity.html
-- https://www.postgresql.org/docs/current/functions-admin.html
--
-- Authoring drafts are edited/reviewed in a SEPARATE restricted staging/editor store,
-- outside these 25 tables. Release validates the complete graph and approved items,
-- then atomically INSERTs a new immutable published curriculum version and approved
-- question versions. There is no UPDATE-draft-to-published path in these tables.
-- Tenant-shared curriculum/content are versioned; every learner-owned foreign key
-- includes (tenant_id,campus_id,student_id). UUID knowledge is not authorization.
-- Application performs command RBAC, staff subject/student assignments, guardian
-- relationship checks, consent, payload validation, novelty/delayed-retention rules,
-- graph-cycle validation, projection rebuild and outbox delivery. RLS is an additional
-- data-scope boundary, NOT a substitute for those checks or a real identity provider.
--
-- Migration owner MUST differ from non-owner, non-superuser, NOBYPASSRLS runtime roles.
-- No PUBLIC table privileges are granted. Provision narrowly scoped runtime roles and
-- column/action GRANTs in a separate reviewed deployment migration. Do not grant users
-- raw SQL access or permission to alter tables, disable triggers or TRUNCATE them.
-- FORCE RLS still does not constrain superusers/BYPASSRLS; FK checks bypass RLS and
-- constraint failures must be mapped to non-enumerating API errors.
--
-- Each request: BEGIN; trusted authentication middleware resolves ACTIVE memberships
-- and an explicit authorized student subset, then calls set_config(name,value,true)
-- for app.tenant_id, app.campus_id, app.actor_user_id, app.actor_role, app.student_ids.
-- app.student_ids is PostgreSQL UUID-array text, e.g. '{uuid1,uuid2}'; empty means none.
-- NEVER take these settings from client headers/payload without server authorization.
-- Custom settings are not cryptographically protected: a holder of the service DB
-- credentials can forge them. Use only a trusted server connection pool, transaction
-- local settings, no user SQL, and test rollback/connection reuse/context omissions.
-- Background workers likewise resolve a narrow per-campus/student batch; no wildcard.
-- Commands lock student FOR UPDATE and compare expected evidence_revision. One command
-- envelope reserves its idempotency key before its domain rows/outbox are inserted in
-- the SAME transaction. On duplicate key compare request_hash; mismatch => 409; replay
-- reads current artifact status, never trusts a cached historical stale=false value.
--
-- No destructive rollback is embedded. Deployment must supply versioned up/down or
-- forward-repair migrations, backup/restore tests and retention/deletion procedures.
-- Append-only is a runtime integrity rule, not permission to retain personal data forever.

BEGIN;
CREATE SCHEMA xueban;
SET LOCAL search_path = xueban, pg_catalog;
REVOKE ALL ON SCHEMA xueban FROM PUBLIC;

-- 01-06: organization, real identity mapping, memberships and guardians.
CREATE TABLE tenant (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','closed')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE campus (
    tenant_id uuid NOT NULL REFERENCES tenant(id),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    name text NOT NULL,
    timezone_name text NOT NULL DEFAULT 'Asia/Shanghai', -- validate against pg_timezone_names in API
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','closed')),
    PRIMARY KEY (tenant_id,id)
);
CREATE TABLE app_user (
    tenant_id uuid NOT NULL REFERENCES tenant(id),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    identity_issuer text NOT NULL,
    identity_subject text NOT NULL,
    display_name text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled','erasure_pending')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id,id),
    UNIQUE (tenant_id,identity_issuer,identity_subject)
);
CREATE TABLE student (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid,
    display_name text NOT NULL,
    grade_code text,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','left','erasure_pending')),
    evidence_revision bigint NOT NULL DEFAULT 0 CHECK (evidence_revision >= 0),
    event_sequence bigint NOT NULL DEFAULT 0 CHECK (event_sequence >= 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id,campus_id,id),
    FOREIGN KEY (tenant_id,campus_id) REFERENCES campus(tenant_id,id),
    FOREIGN KEY (tenant_id,user_id) REFERENCES app_user(tenant_id,id),
    UNIQUE (tenant_id,campus_id,user_id)
);
CREATE TABLE user_scope (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    role_code text NOT NULL CHECK (role_code IN ('student','guardian','teacher','coach','admin','agent_worker','projector','delivery_worker')),
    student_id uuid, -- NULL is campus membership; runtime still supplies explicit student scope
    subject_code text NOT NULL DEFAULT '*',
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
    valid_from timestamptz NOT NULL DEFAULT clock_timestamp(),
    valid_until timestamptz,
    PRIMARY KEY (tenant_id,campus_id,id),
    FOREIGN KEY (tenant_id,campus_id) REFERENCES campus(tenant_id,id),
    FOREIGN KEY (tenant_id,user_id) REFERENCES app_user(tenant_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id) REFERENCES student(tenant_id,campus_id,id),
    UNIQUE NULLS NOT DISTINCT (tenant_id,campus_id,user_id,role_code,student_id,subject_code),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    CHECK (role_code NOT IN ('student','guardian') OR student_id IS NOT NULL)
);
CREATE TABLE guardian_student (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    student_id uuid NOT NULL,
    guardian_user_id uuid NOT NULL,
    relationship text NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','verified','revoked')),
    verified_at timestamptz,
    verified_by uuid,
    consent_receipt_ref text, -- restricted external consent receipt, not a public URL
    PRIMARY KEY (tenant_id,campus_id,student_id,guardian_user_id),
    FOREIGN KEY (tenant_id,campus_id,student_id) REFERENCES student(tenant_id,campus_id,id),
    FOREIGN KEY (tenant_id,guardian_user_id) REFERENCES app_user(tenant_id,id),
    FOREIGN KEY (tenant_id,verified_by) REFERENCES app_user(tenant_id,id),
    CHECK (status <> 'verified' OR (verified_at IS NOT NULL AND verified_by IS NOT NULL))
);

-- 07-10: shared versioned domain graph/content. Published content is never edited:
-- create a new version; retirement/availability belongs in release service metadata.
-- These are immutable release snapshots; mutable authoring drafts live outside this
-- schema. Publish by inserting a validated new version, never editing a saved release.
CREATE TABLE curriculum_version (
    tenant_id uuid NOT NULL REFERENCES tenant(id),
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    curriculum_key text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    subject_code text NOT NULL,
    grade_code text NOT NULL,
    textbook_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('published','retired')),
    content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id,id),
    UNIQUE (tenant_id,curriculum_key,version)
);
CREATE TABLE knowledge_node (
    tenant_id uuid NOT NULL,
    curriculum_version_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    node_key text NOT NULL,
    label text NOT NULL,
    definition jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(definition) = 'object'),
    PRIMARY KEY (tenant_id,curriculum_version_id,id),
    FOREIGN KEY (tenant_id,curriculum_version_id) REFERENCES curriculum_version(tenant_id,id),
    UNIQUE (tenant_id,curriculum_version_id,node_key)
);
CREATE TABLE knowledge_edge (
    tenant_id uuid NOT NULL,
    curriculum_version_id uuid NOT NULL,
    prerequisite_id uuid NOT NULL,
    dependent_id uuid NOT NULL,
    relation text NOT NULL DEFAULT 'prerequisite' CHECK (relation IN ('prerequisite','related')),
    rationale text NOT NULL,
    PRIMARY KEY (tenant_id,curriculum_version_id,prerequisite_id,dependent_id,relation),
    FOREIGN KEY (tenant_id,curriculum_version_id,prerequisite_id) REFERENCES knowledge_node(tenant_id,curriculum_version_id,id),
    FOREIGN KEY (tenant_id,curriculum_version_id,dependent_id) REFERENCES knowledge_node(tenant_id,curriculum_version_id,id),
    CHECK (prerequisite_id <> dependent_id)
    -- Self-edge prevented here; transitive cycles require release-time DAG validation.
);
CREATE TABLE question (
    tenant_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    version integer NOT NULL CHECK (version > 0),
    curriculum_version_id uuid NOT NULL,
    primary_node_id uuid NOT NULL,
    canonical_item_key text NOT NULL, -- exposure identity across text/version changes
    template_key text NOT NULL,
    content jsonb NOT NULL CHECK (jsonb_typeof(content) = 'object'),
    rubric jsonb NOT NULL CHECK (jsonb_typeof(rubric) = 'object'),
    provenance jsonb NOT NULL CHECK (jsonb_typeof(provenance) = 'object'),
    approved_by uuid NOT NULL,
    approved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    status text NOT NULL CHECK (status IN ('approved','retired')),
    PRIMARY KEY (tenant_id,id,version),
    FOREIGN KEY (tenant_id,curriculum_version_id,primary_node_id) REFERENCES knowledge_node(tenant_id,curriculum_version_id,id),
    FOREIGN KEY (tenant_id,approved_by) REFERENCES app_user(tenant_id,id),
    UNIQUE (tenant_id,id,version,curriculum_version_id,primary_node_id)
    -- Foundation models one primary evidence node; secondary mappings need a later
    -- normalized table/migration, not unchecked authoritative node IDs inside JSON.
);

-- 11: authoritative append-only command/evidence envelope and idempotency reservation.
CREATE TABLE evidence_event (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    student_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    student_revision bigint NOT NULL CHECK (student_revision >= 0),
    event_sequence bigint NOT NULL CHECK (event_sequence > 0),
    actor_user_id uuid NOT NULL,
    family text NOT NULL CHECK (family IN ('attempt','review','observation','memory','agent','task','intervention','assessment','report','communication')),
    command_type text NOT NULL,
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
    request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    occurred_at timestamptz NOT NULL,
    effective_from timestamptz NOT NULL,
    -- Earliest learning time affected, validated by the command service: attempt =
    -- actual attempt time; review/correction/retraction = original attempt time;
    -- memory = earliest dependent learning evidence time. Backfilled evidence keeps
    -- its historical effective time. Never substitute receipt/publication time.
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    curriculum_version_id uuid,
    node_id uuid,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    policy_version text NOT NULL,
    PRIMARY KEY (tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id) REFERENCES student(tenant_id,campus_id,id),
    FOREIGN KEY (tenant_id,actor_user_id) REFERENCES app_user(tenant_id,id),
    FOREIGN KEY (tenant_id,curriculum_version_id,node_id) REFERENCES knowledge_node(tenant_id,curriculum_version_id,id),
    UNIQUE (tenant_id,campus_id,student_id,event_sequence),
    UNIQUE (tenant_id,campus_id,student_id,id,family),
    UNIQUE (tenant_id,campus_id,student_id,actor_user_id,command_type,idempotency_key),
    CHECK ((curriculum_version_id IS NULL) = (node_id IS NULL)),
    CHECK (effective_from <= occurred_at),
    CHECK (occurred_at <= recorded_at + interval '5 minutes')
);
CREATE TABLE attempt (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    student_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    question_id uuid NOT NULL,
    question_version integer NOT NULL,
    curriculum_version_id uuid NOT NULL,
    node_id uuid NOT NULL,
    evidence_id uuid NOT NULL,
    evidence_family text NOT NULL DEFAULT 'attempt' CHECK (evidence_family = 'attempt'),
    assessment_item_id uuid,
    answer jsonb NOT NULL,
    stage text NOT NULL CHECK (stage IN ('homework','correction','practice','assessment','preview')),
    assistance text NOT NULL DEFAULT 'unknown' CHECK (assistance IN ('none','direction_hint','step_hint','answer_seen','unknown')),
    occurred_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    time_status text NOT NULL DEFAULT 'unverified' CHECK (time_status IN ('unverified','verified','imported')),
    time_verified_by uuid,
    PRIMARY KEY (tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id) REFERENCES student(tenant_id,campus_id,id),
    FOREIGN KEY (tenant_id,question_id,question_version,curriculum_version_id,node_id) REFERENCES question(tenant_id,id,version,curriculum_version_id,primary_node_id),
    FOREIGN KEY (tenant_id,campus_id,student_id,evidence_id,evidence_family) REFERENCES evidence_event(tenant_id,campus_id,student_id,id,family),
    FOREIGN KEY (tenant_id,time_verified_by) REFERENCES app_user(tenant_id,id),
    UNIQUE (tenant_id,campus_id,student_id,evidence_id),
    CHECK (occurred_at <= recorded_at + interval '5 minutes'),
    CHECK (time_status <> 'verified' OR time_verified_by IS NOT NULL)
    -- Corrections to facts are new evidence, never UPDATE. Do not trust client novelty.
);
CREATE TABLE review_decision (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    student_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    attempt_id uuid NOT NULL,
    evidence_id uuid NOT NULL,
    evidence_family text NOT NULL DEFAULT 'review' CHECK (evidence_family = 'review'),
    version integer NOT NULL CHECK (version > 0),
    supersedes_id uuid,
    reviewer_user_id uuid NOT NULL,
    verdict text NOT NULL CHECK (verdict IN ('correct','incorrect','partial','unscorable','retracted')),
    -- partial is not a full-correct mastery win; retracted withdraws this decision.
    -- Latest retracted decision removes the attempt's verdict; never resurrect an old one.
    independence_confirmed boolean NOT NULL DEFAULT false,
    reason_code text NOT NULL,
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,attempt_id) REFERENCES attempt(tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,evidence_id,evidence_family) REFERENCES evidence_event(tenant_id,campus_id,student_id,id,family),
    FOREIGN KEY (tenant_id,reviewer_user_id) REFERENCES app_user(tenant_id,id),
    UNIQUE (tenant_id,campus_id,student_id,attempt_id,id),
    UNIQUE (tenant_id,campus_id,student_id,attempt_id,version),
    UNIQUE (tenant_id,campus_id,student_id,supersedes_id),
    UNIQUE (tenant_id,campus_id,student_id,evidence_id),
    FOREIGN KEY (tenant_id,campus_id,student_id,attempt_id,supersedes_id) REFERENCES review_decision(tenant_id,campus_id,student_id,attempt_id,id),
    CHECK ((version = 1 AND supersedes_id IS NULL) OR (version > 1 AND supersedes_id IS NOT NULL)),
    CHECK (id IS DISTINCT FROM supersedes_id),
    CHECK (verdict NOT IN ('unscorable','retracted') OR NOT independence_confirmed)
);
CREATE UNIQUE INDEX one_review_root ON review_decision(tenant_id,campus_id,student_id,attempt_id) WHERE supersedes_id IS NULL;

-- 14-17: reproducible projection and per-student agent state.
CREATE TABLE student_knowledge_state (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    student_id uuid NOT NULL,
    curriculum_version_id uuid NOT NULL,
    node_id uuid NOT NULL,
    state text NOT NULL CHECK (state IN ('insufficient_evidence','awaiting_independent_check','needs_diagnosis','needs_support','consolidating','stable_for_now','conflicting_evidence')),
    source_revision bigint NOT NULL CHECK (source_revision >= 0),
    policy_version text NOT NULL,
    last_evidence_id uuid,
    independent_count integer NOT NULL DEFAULT 0 CHECK (independent_count >= 0),
    total_verified_count integer NOT NULL DEFAULT 0 CHECK (total_verified_count >= independent_count),
    template_count integer NOT NULL DEFAULT 0 CHECK (template_count >= 0 AND template_count <= independent_count),
    delayed_verified boolean NOT NULL DEFAULT false,
    review_due_at timestamptz,
    rebuilt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id,campus_id,student_id,curriculum_version_id,node_id),
    FOREIGN KEY (tenant_id,campus_id,student_id) REFERENCES student(tenant_id,campus_id,id),
    FOREIGN KEY (tenant_id,curriculum_version_id,node_id) REFERENCES knowledge_node(tenant_id,curriculum_version_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,last_evidence_id) REFERENCES evidence_event(tenant_id,campus_id,student_id,id)
    -- Rebuild from current review-chain heads at source_revision; do not decrement
    -- counters heuristically on retraction. Need real elapsed delay + independent new
    -- items + sufficient sample/template coverage; all thresholds belong to policy.
);
CREATE TABLE student_agent (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    student_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','retired')),
    config_version integer NOT NULL DEFAULT 1 CHECK (config_version > 0),
    policy_version text NOT NULL,
    prompt_version text NOT NULL,
    row_version bigint NOT NULL DEFAULT 0 CHECK (row_version >= 0),
    PRIMARY KEY (tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id) REFERENCES student(tenant_id,campus_id,id),
    UNIQUE (tenant_id,campus_id,student_id)
);
CREATE TABLE memory_item (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    student_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    agent_id uuid NOT NULL,
    memory_key text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    supersedes_id uuid,
    status text NOT NULL CHECK (status IN ('candidate','confirmed','retracted','expired')),
    source_evidence_id uuid NOT NULL,
    confirmed_by uuid,
    content jsonb NOT NULL CHECK (jsonb_typeof(content) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz,
    PRIMARY KEY (tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,agent_id) REFERENCES student_agent(tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,source_evidence_id) REFERENCES evidence_event(tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,confirmed_by) REFERENCES app_user(tenant_id,id),
    UNIQUE (tenant_id,campus_id,student_id,agent_id,memory_key,id),
    UNIQUE (tenant_id,campus_id,student_id,agent_id,memory_key,version),
    UNIQUE (tenant_id,campus_id,student_id,supersedes_id),
    FOREIGN KEY (tenant_id,campus_id,student_id,agent_id,memory_key,supersedes_id) REFERENCES memory_item(tenant_id,campus_id,student_id,agent_id,memory_key,id),
    CHECK ((version = 1 AND supersedes_id IS NULL) OR (version > 1 AND supersedes_id IS NOT NULL)),
    CHECK (id IS DISTINCT FROM supersedes_id),
    CHECK (status <> 'confirmed' OR confirmed_by IS NOT NULL),
    CHECK (expires_at IS NULL OR expires_at > recorded_at)
);
CREATE UNIQUE INDEX one_memory_root ON memory_item(tenant_id,campus_id,student_id,agent_id,memory_key) WHERE supersedes_id IS NULL;
CREATE TABLE agent_run (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    student_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    agent_id uuid NOT NULL,
    requested_by uuid NOT NULL,
    idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
    request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    source_revision bigint NOT NULL CHECK (source_revision >= 0),
    model_version text NOT NULL,
    prompt_version text NOT NULL,
    policy_version text NOT NULL,
    graph_version_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed','cancelled','stale')),
    context_snapshot jsonb NOT NULL CHECK (jsonb_typeof(context_snapshot) = 'object'),
    output jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    row_version bigint NOT NULL DEFAULT 0 CHECK (row_version >= 0),
    PRIMARY KEY (tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,agent_id) REFERENCES student_agent(tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,requested_by) REFERENCES app_user(tenant_id,id),
    FOREIGN KEY (tenant_id,graph_version_id) REFERENCES curriculum_version(tenant_id,id),
    UNIQUE (tenant_id,campus_id,student_id,requested_by,idempotency_key),
    CHECK (finished_at IS NULL OR finished_at >= created_at)
);

-- 18-21: actions, ownership and independent follow-up.
CREATE TABLE learning_task (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    student_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    plan_key uuid NOT NULL,
    plan_version integer NOT NULL CHECK (plan_version > 0),
    source_revision bigint NOT NULL CHECK (source_revision >= 0),
    agent_run_id uuid,
    curriculum_version_id uuid,
    node_id uuid,
    task_type text NOT NULL CHECK (task_type IN ('school_homework','homework_question','correction','targeted_practice','independent_check','delayed_review','preview','habit')),
    task_origin text NOT NULL DEFAULT 'optional' CHECK (task_origin IN ('school','optional')),
    school_source_ref text,
    school_tasks_complete boolean NOT NULL DEFAULT false,
    child_willing boolean NOT NULL DEFAULT false,
    has_capacity boolean NOT NULL DEFAULT false,
    extra_minutes integer NOT NULL DEFAULT 0 CHECK (extra_minutes BETWEEN 0 AND 120),
    carry_over_required boolean NOT NULL DEFAULT false,
    -- Service verifies current origin/gates; whole-plan optional total is capped
    -- transactionally. Per-row checks cannot prove willingness or available time.
    status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','approved','in_progress','paused','skipped','done','cancelled','stale')),
    estimated_minutes integer NOT NULL CHECK (estimated_minutes BETWEEN 1 AND 120),
    owner_user_id uuid NOT NULL,
    approved_by uuid,
    instructions jsonb NOT NULL CHECK (jsonb_typeof(instructions) = 'object'),
    due_at timestamptz,
    row_version bigint NOT NULL DEFAULT 0 CHECK (row_version >= 0),
    PRIMARY KEY (tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id) REFERENCES student(tenant_id,campus_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,agent_run_id) REFERENCES agent_run(tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,curriculum_version_id,node_id) REFERENCES knowledge_node(tenant_id,curriculum_version_id,id),
    FOREIGN KEY (tenant_id,owner_user_id) REFERENCES app_user(tenant_id,id),
    FOREIGN KEY (tenant_id,approved_by) REFERENCES app_user(tenant_id,id),
    CHECK ((curriculum_version_id IS NULL) = (node_id IS NULL)),
    CHECK (status NOT IN ('approved','in_progress','done') OR approved_by IS NOT NULL),
    CHECK (task_origin <> 'school' OR (school_source_ref IS NOT NULL AND length(btrim(school_source_ref)) > 0 AND extra_minutes = 0)),
    CHECK (task_origin <> 'optional' OR (NOT carry_over_required AND due_at IS NULL)),
    CHECK (task_origin <> 'optional' OR status NOT IN ('approved','in_progress','done') OR
        (school_tasks_complete AND child_willing AND has_capacity AND extra_minutes >= estimated_minutes))
);
CREATE TABLE intervention (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    student_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    task_id uuid,
    domain text NOT NULL CHECK (domain IN ('academic','habit')),
    owner_user_id uuid NOT NULL,
    source_evidence_id uuid NOT NULL,
    action_text text NOT NULL CHECK (length(btrim(action_text)) > 0),
    service_status text NOT NULL DEFAULT 'planned' CHECK (service_status IN ('planned','accepted','delivered','cancelled')),
    learning_status text NOT NULL DEFAULT 'not_assessed' CHECK (learning_status IN ('not_assessed','awaiting_check','supported','not_yet_supported','insufficient_evidence')),
    outcome_evidence_id uuid,
    due_at timestamptz,
    delivered_at timestamptz,
    row_version bigint NOT NULL DEFAULT 0 CHECK (row_version >= 0),
    PRIMARY KEY (tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id) REFERENCES student(tenant_id,campus_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,task_id) REFERENCES learning_task(tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,source_evidence_id) REFERENCES evidence_event(tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,outcome_evidence_id) REFERENCES evidence_event(tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,owner_user_id) REFERENCES app_user(tenant_id,id),
    CHECK (service_status <> 'delivered' OR delivered_at IS NOT NULL),
    CHECK (learning_status NOT IN ('supported','not_yet_supported') OR outcome_evidence_id IS NOT NULL)
);
CREATE TABLE assessment (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    student_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    assessment_key uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    purpose text NOT NULL CHECK (purpose IN ('baseline','immediate_transfer','delayed_retention','fortnightly')),
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','ready','active','paused','skipped','submitted','reviewed','cancelled')),
    participation_optional boolean NOT NULL DEFAULT true CHECK (participation_optional),
    school_tasks_complete boolean NOT NULL DEFAULT false,
    child_willing boolean NOT NULL DEFAULT false,
    has_capacity boolean NOT NULL DEFAULT false,
    extra_minutes integer NOT NULL DEFAULT 0 CHECK (extra_minutes BETWEEN 0 AND 120),
    estimated_minutes integer NOT NULL DEFAULT 0 CHECK (estimated_minutes BETWEEN 0 AND 120),
    -- Shares the student's optional-work budget with learning_task; service must
    -- reserve/check the combined total transactionally, never grant a second pool.
    -- Half-month reports are independent of participation; no makeup-test debt.
    source_revision bigint NOT NULL CHECK (source_revision >= 0),
    policy_version text NOT NULL,
    planned_for timestamptz NOT NULL,
    started_at timestamptz,
    ended_at timestamptz,
    proctor_user_id uuid,
    row_version bigint NOT NULL DEFAULT 0 CHECK (row_version >= 0),
    PRIMARY KEY (tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id) REFERENCES student(tenant_id,campus_id,id),
    FOREIGN KEY (tenant_id,proctor_user_id) REFERENCES app_user(tenant_id,id),
    UNIQUE (tenant_id,campus_id,student_id,assessment_key,version),
    CHECK (ended_at IS NULL OR (started_at IS NOT NULL AND ended_at >= started_at)),
    CHECK (status NOT IN ('active','submitted','reviewed') OR
        (school_tasks_complete AND child_willing AND has_capacity AND estimated_minutes > 0 AND extra_minutes >= estimated_minutes))
);
CREATE TABLE assessment_item (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    student_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    assessment_id uuid NOT NULL,
    position integer NOT NULL CHECK (position > 0),
    question_id uuid NOT NULL,
    question_version integer NOT NULL,
    status text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','presented','answered','skipped','invalidated')),
    invalidation_reason text,
    PRIMARY KEY (tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,assessment_id) REFERENCES assessment(tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,question_id,question_version) REFERENCES question(tenant_id,id,version),
    UNIQUE (tenant_id,campus_id,student_id,assessment_id,position),
    UNIQUE (tenant_id,campus_id,student_id,id,question_id,question_version),
    CHECK (status <> 'invalidated' OR invalidation_reason IS NOT NULL)
);
ALTER TABLE attempt ADD CONSTRAINT attempt_assessment_same_question
    FOREIGN KEY (tenant_id,campus_id,student_id,assessment_item_id,question_id,question_version)
    REFERENCES assessment_item(tenant_id,campus_id,student_id,id,question_id,question_version);

-- 22-25: reviewable report snapshots, communication, reliable delivery and audit.
CREATE TABLE report (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    student_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    report_key uuid NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    supersedes_id uuid,
    source_revision bigint NOT NULL CHECK (source_revision >= 0),
    graph_version_id uuid NOT NULL,
    policy_version text NOT NULL,
    period_start timestamptz NOT NULL,
    period_end timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','withdrawn')),
    stale boolean NOT NULL DEFAULT false,
    snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
    published_by uuid,
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    row_version bigint NOT NULL DEFAULT 0 CHECK (row_version >= 0),
    PRIMARY KEY (tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id) REFERENCES student(tenant_id,campus_id,id),
    FOREIGN KEY (tenant_id,graph_version_id) REFERENCES curriculum_version(tenant_id,id),
    FOREIGN KEY (tenant_id,published_by) REFERENCES app_user(tenant_id,id),
    UNIQUE (tenant_id,campus_id,student_id,report_key,id),
    UNIQUE (tenant_id,campus_id,student_id,report_key,version),
    UNIQUE (tenant_id,campus_id,student_id,supersedes_id),
    FOREIGN KEY (tenant_id,campus_id,student_id,report_key,supersedes_id) REFERENCES report(tenant_id,campus_id,student_id,report_key,id),
    CHECK (period_start < period_end), -- all event selection and snapshot cutoffs use [start,end)
    CHECK (id IS DISTINCT FROM supersedes_id),
    CHECK ((version = 1 AND supersedes_id IS NULL) OR (version > 1 AND supersedes_id IS NOT NULL)),
    CHECK (status <> 'published' OR (published_by IS NOT NULL AND published_at IS NOT NULL))
);
CREATE UNIQUE INDEX one_report_root ON report(tenant_id,campus_id,student_id,report_key) WHERE supersedes_id IS NULL;
CREATE TABLE communication (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    student_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    report_id uuid,
    guardian_user_id uuid NOT NULL,
    owner_user_id uuid NOT NULL,
    channel text NOT NULL CHECK (channel IN ('in_person','phone','portal','approved_messaging')),
    status text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','held','followup_due','closed','cancelled')),
    summary jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(summary) = 'object'),
    followup_task_id uuid,
    happened_at timestamptz,
    row_version bigint NOT NULL DEFAULT 0 CHECK (row_version >= 0),
    PRIMARY KEY (tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,guardian_user_id) REFERENCES guardian_student(tenant_id,campus_id,student_id,guardian_user_id),
    FOREIGN KEY (tenant_id,campus_id,student_id,report_id) REFERENCES report(tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,followup_task_id) REFERENCES learning_task(tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,owner_user_id) REFERENCES app_user(tenant_id,id)
);
CREATE TABLE outbox_event (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    student_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    source_evidence_id uuid NOT NULL,
    communication_id uuid,
    report_id uuid,
    guardian_user_id uuid,
    topic text NOT NULL,
    deduplication_key text NOT NULL CHECK (length(deduplication_key) BETWEEN 1 AND 200),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','leased','delivered','failed','dead','cancelled')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    lease_until timestamptz,
    delivered_at timestamptz,
    provider_receipt text,
    row_version bigint NOT NULL DEFAULT 0 CHECK (row_version >= 0),
    PRIMARY KEY (tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,source_evidence_id) REFERENCES evidence_event(tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,communication_id) REFERENCES communication(tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,report_id) REFERENCES report(tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id,guardian_user_id) REFERENCES guardian_student(tenant_id,campus_id,student_id,guardian_user_id),
    UNIQUE (tenant_id,campus_id,student_id,topic,deduplication_key),
    CHECK (status <> 'leased' OR lease_until IS NOT NULL),
    CHECK (status <> 'delivered' OR delivered_at IS NOT NULL)
    -- Consumer must recheck active guardian, report-period relevant revisions/stale/status,
    -- consent and recipient before sending. Retries require provider idempotency or
    -- reconciliation; transactional outbox alone does not guarantee exactly-once.
);
CREATE TABLE audit_event (
    tenant_id uuid NOT NULL,
    campus_id uuid NOT NULL,
    student_id uuid NOT NULL,
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    actor_user_id uuid NOT NULL,
    action_code text NOT NULL,
    target_type text NOT NULL,
    target_ref text NOT NULL, -- descriptive audit label, never used as an authorized FK
    request_id uuid NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('allowed','denied','failed')),
    metadata jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(metadata) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id,campus_id,student_id,id),
    FOREIGN KEY (tenant_id,campus_id,student_id) REFERENCES student(tenant_id,campus_id,id),
    FOREIGN KEY (tenant_id,actor_user_id) REFERENCES app_user(tenant_id,id)
    -- Learner audit only. Unknown identities/cross-tenant denied attempts belong in
    -- a separate restricted security log, not a forged child scope or raw PII payload.
);

-- Query support. Production workload must EXPLAIN/measure and add indexes as needed.
CREATE INDEX attempt_timeline ON attempt(tenant_id,campus_id,student_id,occurred_at,id);
CREATE INDEX question_exposure_identity ON question(tenant_id,canonical_item_key);
CREATE INDEX attempt_exposure ON attempt(tenant_id,campus_id,student_id,question_id,question_version,occurred_at);
CREATE INDEX review_head_lookup ON review_decision(tenant_id,campus_id,student_id,attempt_id,version DESC);
CREATE INDEX evidence_report_impact ON evidence_event(tenant_id,campus_id,student_id,effective_from,student_revision) WHERE family IN ('attempt','review','observation','memory');
CREATE INDEX evidence_node_lookup ON evidence_event(tenant_id,campus_id,student_id,curriculum_version_id,node_id,student_revision);
CREATE INDEX task_due ON learning_task(tenant_id,campus_id,student_id,status,due_at);
CREATE INDEX outbox_ready ON outbox_event(tenant_id,campus_id,status,available_at) WHERE status IN ('pending','failed');
CREATE INDEX guardian_lookup ON guardian_student(tenant_id,guardian_user_id,campus_id,student_id) WHERE status = 'verified';

-- Append-only history. Privileged erasure/retention workflows need a separately
-- audited migration/service; application roles may not bypass these guards.
CREATE FUNCTION reject_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'append_only_history: %', TG_TABLE_NAME USING ERRCODE = '55000';
END;
$$;
DO $$ DECLARE n text; BEGIN
    FOREACH n IN ARRAY ARRAY['curriculum_version','knowledge_node','knowledge_edge','question','evidence_event','attempt','review_decision','memory_item','audit_event'] LOOP
        EXECUTE format('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON xueban.%I FOR EACH ROW EXECUTE FUNCTION xueban.reject_history_mutation()',n);
    END LOOP;
END $$;

-- Reserve a monotonically increasing event sequence under a row lock. Only learning
-- evidence changes advance evidence_revision. Publication/delivery/task bookkeeping
-- must NOT invalidate its own report or reset the learning state.
-- Duplicate-key rollback also rolls back this increment; do not catch and commit a
-- partial command. recorded_at is server assigned, distinct from observed time.
CREATE FUNCTION reserve_evidence_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.recorded_at := clock_timestamp();
    UPDATE xueban.student
      SET event_sequence = event_sequence + 1,
          evidence_revision = evidence_revision + CASE WHEN NEW.family IN ('attempt','review','observation','memory') THEN 1 ELSE 0 END
      WHERE tenant_id = NEW.tenant_id AND campus_id = NEW.campus_id AND id = NEW.student_id
      RETURNING evidence_revision,event_sequence INTO NEW.student_revision,NEW.event_sequence;
    IF NOT FOUND THEN RAISE EXCEPTION 'student_scope_unavailable' USING ERRCODE='42501'; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER evidence_revision BEFORE INSERT ON evidence_event FOR EACH ROW EXECUTE FUNCTION reserve_evidence_revision();

CREATE FUNCTION invalidate_derived_artifacts() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.family NOT IN ('attempt','review','observation','memory') THEN RETURN NEW; END IF;
    UPDATE xueban.report SET stale=true, row_version=row_version+1
      WHERE tenant_id=NEW.tenant_id AND campus_id=NEW.campus_id AND student_id=NEW.student_id
        AND NOT stale AND source_revision < NEW.student_revision
        AND NEW.effective_from < period_end;
    UPDATE xueban.learning_task SET status='stale', row_version=row_version+1
      WHERE tenant_id=NEW.tenant_id AND campus_id=NEW.campus_id AND student_id=NEW.student_id
        AND status IN ('proposed','approved','in_progress') AND source_revision < NEW.student_revision;
    RETURN NEW;
END;
$$;
CREATE TRIGGER invalidate_artifacts AFTER INSERT ON evidence_event FOR EACH ROW EXECUTE FUNCTION invalidate_derived_artifacts();
-- Reports contain cumulative knowledge as of their exclusive period_end. Invalidate
-- only later revisions that affect learning BEFORE that cutoff; ordinary next-period
-- evidence does not invalidate historical reports. Current tasks still conservatively
-- become stale and are revalidated. Emit envelope first, then dependent artifacts.

CREATE FUNCTION validate_review_chain() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prior_version integer; a xueban.attempt%ROWTYPE;
BEGIN
    NEW.recorded_at := clock_timestamp();
    SELECT * INTO STRICT a FROM xueban.attempt WHERE tenant_id=NEW.tenant_id
      AND campus_id=NEW.campus_id AND student_id=NEW.student_id AND id=NEW.attempt_id;
    IF NEW.independence_confirmed AND (a.assistance <> 'none' OR a.stage NOT IN ('homework','practice','assessment')) THEN
        RAISE EXCEPTION 'independence_incompatible_with_attempt' USING ERRCODE='23514';
    END IF;
    IF NEW.supersedes_id IS NOT NULL THEN
        SELECT version INTO STRICT prior_version FROM xueban.review_decision
          WHERE tenant_id=NEW.tenant_id AND campus_id=NEW.campus_id
            AND student_id=NEW.student_id AND attempt_id=NEW.attempt_id AND id=NEW.supersedes_id;
        IF NEW.version <> prior_version+1 THEN RAISE EXCEPTION 'review_version_gap' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER review_chain BEFORE INSERT ON review_decision FOR EACH ROW EXECUTE FUNCTION validate_review_chain();

CREATE FUNCTION protect_report_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE current_revision bigint;
BEGIN
    IF TG_OP='UPDATE' THEN
        IF NEW.snapshot IS DISTINCT FROM OLD.snapshot OR NEW.source_revision<>OLD.source_revision
           OR NEW.period_start<>OLD.period_start OR NEW.period_end<>OLD.period_end
           OR NEW.graph_version_id<>OLD.graph_version_id OR NEW.policy_version<>OLD.policy_version THEN
            RAISE EXCEPTION 'report_snapshot_requires_new_version' USING ERRCODE='55000';
        END IF;
        IF OLD.status IN ('published','withdrawn') AND NEW.status='draft' THEN
            RAISE EXCEPTION 'report_cannot_return_to_draft' USING ERRCODE='55000';
        END IF;
        IF OLD.status='withdrawn' AND NEW.status<>'withdrawn' THEN
            RAISE EXCEPTION 'withdrawn_report_requires_new_version' USING ERRCODE='55000';
        END IF;
        IF OLD.status IN ('published','withdrawn') AND (NEW.published_by IS DISTINCT FROM OLD.published_by OR NEW.published_at IS DISTINCT FROM OLD.published_at) THEN
            RAISE EXCEPTION 'publication_receipt_is_immutable' USING ERRCODE='55000';
        END IF;
        IF OLD.stale AND NOT NEW.stale THEN
            RAISE EXCEPTION 'stale_report_requires_new_version' USING ERRCODE='55000';
        END IF;
    END IF;
    IF NEW.status='published' AND (TG_OP='INSERT' OR OLD.status IS DISTINCT FROM 'published') THEN
        IF xueban.actor_role() IS DISTINCT FROM 'teacher' OR NEW.published_by IS DISTINCT FROM xueban.ctx_uuid('app.actor_user_id') THEN
            RAISE EXCEPTION 'publication_requires_confirming_teacher' USING ERRCODE='42501';
        END IF;
        SELECT evidence_revision INTO STRICT current_revision FROM xueban.student
          WHERE tenant_id=NEW.tenant_id AND campus_id=NEW.campus_id AND id=NEW.student_id FOR UPDATE;
        -- The lock serializes against all evidence writers. Global revision equality
        -- would incorrectly reject an unchanged historical report after a new period.
        IF NEW.source_revision > current_revision THEN
            RAISE EXCEPTION 'report_source_revision_in_future' USING ERRCODE='23514';
        END IF;
        IF NEW.stale OR EXISTS (
            SELECT 1 FROM xueban.evidence_event e
              WHERE e.tenant_id=NEW.tenant_id AND e.campus_id=NEW.campus_id
                AND e.student_id=NEW.student_id AND e.student_revision>NEW.source_revision
                AND e.family IN ('attempt','review','observation','memory')
                AND e.effective_from<NEW.period_end
        ) THEN
            RAISE EXCEPTION 'stale_report_regenerate_required' USING ERRCODE='40001';
        END IF;
        NEW.published_at := clock_timestamp();
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER report_snapshot BEFORE INSERT OR UPDATE ON report FOR EACH ROW EXECUTE FUNCTION protect_report_snapshot();

-- Client-supplied receipt times must not defeat the observed-time ceiling.
CREATE FUNCTION stamp_server_recorded_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.recorded_at := clock_timestamp(); RETURN NEW; END;
$$;
CREATE TRIGGER attempt_received_time BEFORE INSERT ON attempt FOR EACH ROW EXECUTE FUNCTION stamp_server_recorded_at();
CREATE TRIGGER memory_received_time BEFORE INSERT ON memory_item FOR EACH ROW EXECUTE FUNCTION stamp_server_recorded_at();
CREATE TRIGGER audit_received_time BEFORE INSERT ON audit_event FOR EACH ROW EXECUTE FUNCTION stamp_server_recorded_at();

-- Every memory/report replacement must point to the immediately preceding version.
CREATE FUNCTION validate_versioned_append() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prior_version integer;
BEGIN
    IF NEW.supersedes_id IS NOT NULL THEN
        EXECUTE format('SELECT version FROM xueban.%I WHERE tenant_id=$1 AND campus_id=$2 AND student_id=$3 AND id=$4',TG_TABLE_NAME)
          INTO STRICT prior_version USING NEW.tenant_id,NEW.campus_id,NEW.student_id,NEW.supersedes_id;
        IF NEW.version <> prior_version+1 THEN RAISE EXCEPTION 'artifact_version_gap' USING ERRCODE='23514'; END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER memory_chain BEFORE INSERT ON memory_item FOR EACH ROW EXECUTE FUNCTION validate_versioned_append();
CREATE TRIGGER report_chain BEFORE INSERT ON report FOR EACH ROW EXECUTE FUNCTION validate_versioned_append();

-- Fail-closed RLS context helpers: missing/malformed settings do not widen access.
CREATE FUNCTION ctx_uuid(setting_name text) RETURNS uuid LANGUAGE plpgsql STABLE
SET search_path=pg_catalog AS $$
BEGIN RETURN nullif(current_setting(setting_name,true),'')::uuid;
EXCEPTION WHEN invalid_text_representation THEN RETURN NULL;
END;
$$;
CREATE FUNCTION ctx_students() RETURNS uuid[] LANGUAGE plpgsql STABLE
SET search_path=pg_catalog AS $$
BEGIN RETURN coalesce(nullif(current_setting('app.student_ids',true),'')::uuid[],ARRAY[]::uuid[]);
EXCEPTION WHEN invalid_text_representation THEN RETURN ARRAY[]::uuid[];
END;
$$;
CREATE FUNCTION actor_role() RETURNS text LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
    SELECT nullif(current_setting('app.actor_role',true),'');
$$;
CREATE FUNCTION tenant_scope(t uuid) RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog,xueban AS $$
    SELECT coalesce(t=xueban.ctx_uuid('app.tenant_id') AND xueban.ctx_uuid('app.actor_user_id') IS NOT NULL
       AND xueban.actor_role() IN ('student','guardian','teacher','coach','admin','agent_worker','projector','delivery_worker'),false);
$$;
CREATE FUNCTION campus_scope(t uuid,c uuid) RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog,xueban AS $$
    SELECT coalesce(xueban.tenant_scope(t) AND c=xueban.ctx_uuid('app.campus_id'),false);
$$;
CREATE FUNCTION student_scope(t uuid,c uuid,s uuid) RETURNS boolean LANGUAGE sql STABLE SET search_path=pg_catalog,xueban AS $$
    SELECT coalesce(xueban.campus_scope(t,c) AND s=ANY(xueban.ctx_students()),false);
$$;

-- Enable+FORCE every table; until an allow policy AND matching SQL GRANT exist,
-- ordinary roles have no access. Scope gates are RESTRICTIVE so future permissive
-- action policies cannot OR their way around tenant/campus/student restrictions.
DO $$ DECLARE n text; BEGIN
    FOREACH n IN ARRAY ARRAY['tenant','campus','app_user','student','user_scope','guardian_student','curriculum_version','knowledge_node','knowledge_edge','question','evidence_event','attempt','review_decision','student_knowledge_state','student_agent','memory_item','agent_run','learning_task','intervention','assessment','assessment_item','report','communication','outbox_event','audit_event'] LOOP
        EXECUTE format('ALTER TABLE xueban.%I ENABLE ROW LEVEL SECURITY',n);
        EXECUTE format('ALTER TABLE xueban.%I FORCE ROW LEVEL SECURITY',n);
    END LOOP;
END $$;
CREATE POLICY scope_gate ON tenant AS RESTRICTIVE FOR ALL USING(tenant_scope(id)) WITH CHECK(tenant_scope(id));
CREATE POLICY scope_gate ON campus AS RESTRICTIVE FOR ALL USING(campus_scope(tenant_id,id)) WITH CHECK(campus_scope(tenant_id,id));
CREATE POLICY scope_gate ON student AS RESTRICTIVE FOR ALL USING(student_scope(tenant_id,campus_id,id)) WITH CHECK(student_scope(tenant_id,campus_id,id));
DO $$ DECLARE n text; BEGIN
    FOREACH n IN ARRAY ARRAY['app_user','curriculum_version','knowledge_node','knowledge_edge','question'] LOOP
        EXECUTE format('CREATE POLICY scope_gate ON xueban.%I AS RESTRICTIVE FOR ALL USING(xueban.tenant_scope(tenant_id)) WITH CHECK(xueban.tenant_scope(tenant_id))',n);
    END LOOP;
    FOREACH n IN ARRAY ARRAY['guardian_student','evidence_event','attempt','review_decision','student_knowledge_state','student_agent','memory_item','agent_run','learning_task','intervention','assessment','assessment_item','report','communication','outbox_event','audit_event'] LOOP
        EXECUTE format('CREATE POLICY scope_gate ON xueban.%I AS RESTRICTIVE FOR ALL USING(xueban.student_scope(tenant_id,campus_id,student_id)) WITH CHECK(xueban.student_scope(tenant_id,campus_id,student_id))',n);
    END LOOP;
END $$;
CREATE POLICY scope_gate ON user_scope AS RESTRICTIVE FOR ALL
    USING(campus_scope(tenant_id,campus_id) AND (student_id IS NULL OR student_scope(tenant_id,campus_id,student_id)))
    WITH CHECK(campus_scope(tenant_id,campus_id) AND (student_id IS NULL OR student_scope(tenant_id,campus_id,student_id)));

-- RLS cannot by itself prevent moving an object between two students both present
-- in one request scope. Freeze ownership and version lineage on mutable aggregates.
CREATE FUNCTION freeze_identity_fields() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE key_name text;
BEGIN
    FOREACH key_name IN ARRAY ARRAY['tenant_id','campus_id','student_id','id','guardian_user_id','question_id','question_version','report_key','version','supersedes_id','agent_id','assessment_id'] LOOP
        IF (to_jsonb(NEW)->key_name) IS DISTINCT FROM (to_jsonb(OLD)->key_name) THEN
            RAISE EXCEPTION 'immutable_identity_field: %',key_name USING ERRCODE='55000';
        END IF;
    END LOOP;
    IF TG_TABLE_NAME='student_knowledge_state' AND ((to_jsonb(NEW)->'curriculum_version_id') IS DISTINCT FROM (to_jsonb(OLD)->'curriculum_version_id') OR (to_jsonb(NEW)->'node_id') IS DISTINCT FROM (to_jsonb(OLD)->'node_id')) THEN
        RAISE EXCEPTION 'immutable_projection_key' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END;
$$;
DO $$ DECLARE n text; BEGIN
    FOREACH n IN ARRAY ARRAY['tenant','campus','app_user','student','user_scope','guardian_student','student_knowledge_state','student_agent','agent_run','learning_task','intervention','assessment','assessment_item','report','communication','outbox_event'] LOOP
        EXECUTE format('CREATE TRIGGER freeze_identity BEFORE UPDATE ON xueban.%I FOR EACH ROW EXECUTE FUNCTION xueban.freeze_identity_fields()',n);
    END LOOP;
END $$;

-- Minimal ALLOW examples: no runtime provisioning/membership writes, no broad ALL
-- policy for parent/student roles. Trusted service endpoints still enforce operations.
CREATE POLICY self_identity ON app_user FOR SELECT USING(id=ctx_uuid('app.actor_user_id'));
CREATE POLICY self_memberships ON user_scope FOR SELECT USING(user_id=ctx_uuid('app.actor_user_id'));
CREATE POLICY guardian_relationships ON guardian_student FOR SELECT
    USING(guardian_user_id=ctx_uuid('app.actor_user_id') OR actor_role() IN ('teacher','admin','delivery_worker'));
CREATE POLICY student_profile_read ON student FOR SELECT USING(true);
CREATE POLICY student_revision_write ON student FOR UPDATE
    USING(actor_role() IN ('teacher','admin','agent_worker','projector'))
    WITH CHECK(actor_role() IN ('teacher','admin','agent_worker','projector'));
-- Deployment grants evidence_revision/event_sequence columns only to authorized command execution;
-- profile editing is a separately authorized endpoint. Invoker triggers need grants.
DO $$ DECLARE n text; BEGIN
    FOREACH n IN ARRAY ARRAY['tenant','campus','curriculum_version','knowledge_node','knowledge_edge'] LOOP
        EXECUTE format('CREATE POLICY scoped_read ON xueban.%I FOR SELECT USING(true)',n);
    END LOOP;
    FOREACH n IN ARRAY ARRAY['question','evidence_event','attempt','review_decision','student_knowledge_state','student_agent','memory_item','agent_run','learning_task','intervention','assessment','assessment_item','report','communication','audit_event'] LOOP
        EXECUTE format('CREATE POLICY internal_read ON xueban.%I FOR SELECT USING(xueban.actor_role() IN (''teacher'',''admin'',''agent_worker'',''projector''))',n);
    END LOOP;
    FOREACH n IN ARRAY ARRAY['attempt','agent_run','learning_task','intervention','assessment','assessment_item','report','communication'] LOOP
        EXECUTE format('CREATE POLICY staff_insert ON xueban.%I FOR INSERT WITH CHECK(xueban.actor_role() IN (''teacher'',''admin'',''agent_worker'',''projector''))',n);
    END LOOP;
    FOREACH n IN ARRAY ARRAY['agent_run','learning_task','intervention','assessment','assessment_item','report','communication'] LOOP
        EXECUTE format('CREATE POLICY staff_update ON xueban.%I FOR UPDATE USING(xueban.actor_role() IN (''teacher'',''admin'',''agent_worker'',''projector'')) WITH CHECK(xueban.actor_role() IN (''teacher'',''admin'',''agent_worker'',''projector''))',n);
    END LOOP;
END $$;
CREATE POLICY review_teacher_insert ON review_decision FOR INSERT WITH CHECK(actor_role()='teacher' AND reviewer_user_id=ctx_uuid('app.actor_user_id'));
CREATE POLICY evidence_command_insert ON evidence_event FOR INSERT
    WITH CHECK(actor_user_id=ctx_uuid('app.actor_user_id') AND actor_role() IN ('teacher','admin','agent_worker','projector'));
CREATE POLICY memory_internal_insert ON memory_item FOR INSERT WITH CHECK(actor_role() IN ('teacher','agent_worker','projector'));
CREATE POLICY knowledge_state_projection ON student_knowledge_state FOR ALL USING(actor_role()='projector') WITH CHECK(actor_role()='projector');
CREATE POLICY agent_internal_write ON student_agent FOR ALL USING(actor_role() IN ('admin','projector')) WITH CHECK(actor_role() IN ('admin','projector'));
CREATE POLICY audit_append ON audit_event FOR INSERT WITH CHECK(actor_user_id=ctx_uuid('app.actor_user_id'));
CREATE POLICY outbox_internal ON outbox_event FOR ALL
    USING(actor_role() IN ('teacher','admin','projector','delivery_worker'))
    WITH CHECK(actor_role() IN ('teacher','admin','projector','delivery_worker'));
-- Historical published reports remain readable, including stale=true correction
-- markers. The API must prominently label stale reports as superseded/being corrected,
-- show the replacement link when available, and never present their old conclusion as
-- a current verified claim. Ordinary new-period evidence does not hide prior history.
-- Withdrawn reports are not returned by this policy.
CREATE POLICY guardian_published_report ON report FOR SELECT USING(
    actor_role()='guardian' AND status='published'
    AND EXISTS(SELECT 1 FROM xueban.guardian_student g WHERE g.tenant_id=report.tenant_id
      AND g.campus_id=report.campus_id AND g.student_id=report.student_id
      AND g.guardian_user_id=ctx_uuid('app.actor_user_id') AND g.status='verified')
);
-- Deliberately no direct student read of rubric/raw review/agent memory, no guardian
-- read of raw observations, no coach access to academic review. Deliver redacted DTOs
-- through authorized server operations; add integration-tested role policies as needed.
-- Student/coach commands must use narrowly scoped trusted command workers; the above
-- minimal examples alone do not make every service flow operational.
REVOKE ALL ON ALL TABLES IN SCHEMA xueban FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA xueban FROM PUBLIC;
COMMIT;
