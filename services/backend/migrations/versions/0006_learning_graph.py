"""Curriculum graph, knowledge links, learning evidence and per-student state."""
from datetime import datetime, timezone
import json
from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = '0006_learning_graph'
down_revision = '0005_deletion_requests'
branch_labels = None
depends_on = None

SEED = Path(__file__).resolve().parents[2] / 'xueban_backend' / 'data' / 'curriculum' / 'pep-math-junior-pilot-1.json'


def student_fk():
    return sa.ForeignKeyConstraint(['student_id', 'tenant_id'], ['students.id', 'students.tenant_id'])


def ts(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade():
    graphs = op.create_table('curriculum_graphs',
        sa.Column('version', sa.String(64), primary_key=True),
        sa.Column('subject', sa.String(32), nullable=False),
        sa.Column('title', sa.String(160), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('review_status', sa.String(32), nullable=False),
        sa.Column('source_note', sa.Text()),
        ts('published_at', True),
        sa.CheckConstraint("status IN ('draft', 'published', 'retired')", name='valid_graph_status'))
    nodes = op.create_table('knowledge_nodes',
        sa.Column('graph_version', sa.String(64), sa.ForeignKey('curriculum_graphs.version'), primary_key=True),
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('node_type', sa.String(16), nullable=False),
        sa.Column('parent_id', sa.String(64)),
        sa.Column('name', sa.String(160), nullable=False),
        sa.Column('definition', sa.Text()),
        sa.Column('observable_objective', sa.Text()),
        sa.Column('curriculum_refs', sa.JSON(), nullable=False),
        sa.Column('coverage_facets', sa.JSON(), nullable=False),
        sa.Column('misconception_codes', sa.JSON(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.CheckConstraint("node_type IN ('subject', 'chapter', 'concept', 'skill')", name='valid_node_type'))
    edges = op.create_table('knowledge_edges',
        sa.Column('graph_version', sa.String(64), sa.ForeignKey('curriculum_graphs.version'), primary_key=True),
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('from_id', sa.String(64), nullable=False),
        sa.Column('to_id', sa.String(64), nullable=False),
        sa.Column('relation_type', sa.String(32), nullable=False),
        sa.Column('scope', sa.Text()),
        sa.Column('rationale', sa.Text()),
        sa.CheckConstraint("relation_type IN ('part_of', 'prerequisite_of', 'related_to', 'equivalent_to')", name='valid_relation_type'))
    op.create_table('student_learning_profiles',
        sa.Column('student_id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('document', sa.JSON(), nullable=False),
        sa.Column('updated_by', sa.String(64), nullable=False),
        ts('updated_at'),
        student_fk())
    op.create_table('question_knowledge_links',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('student_id', sa.String(64), nullable=False),
        sa.Column('homework_id', sa.String(64), nullable=False),
        sa.Column('question_id', sa.String(64), nullable=False),
        sa.Column('question_revision', sa.Integer(), nullable=False),
        sa.Column('knowledge_id', sa.String(64), nullable=False),
        sa.Column('graph_version', sa.String(64), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('source', sa.String(16), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('rationale', sa.Text()),
        sa.Column('actor_id', sa.String(64)),
        sa.Column('supersedes_id', sa.String(64)),
        ts('created_at'),
        student_fk(),
        sa.CheckConstraint("role IN ('primary', 'secondary')", name='valid_link_role'),
        sa.CheckConstraint("source IN ('model', 'staff')", name='valid_link_source'),
        sa.CheckConstraint("status IN ('candidate', 'confirmed', 'rejected')", name='valid_link_status'))
    op.create_index('ix_qkl_question', 'question_knowledge_links', ['question_id'])
    op.create_index('ix_qkl_student', 'question_knowledge_links', ['student_id'])
    op.create_table('attempt_conditions',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('student_id', sa.String(64), nullable=False),
        sa.Column('attempt_id', sa.String(64), nullable=False),
        sa.Column('assistance_level', sa.String(24), nullable=False),
        sa.Column('note', sa.Text()),
        sa.Column('actor_id', sa.String(64), nullable=False),
        ts('created_at'),
        student_fk(),
        sa.CheckConstraint("assistance_level IN ('independent', 'direction', 'key_step', 'solution_seen', 'staff_help', 'unknown')", name='valid_assistance_level'))
    op.create_index('ix_attempt_conditions_attempt', 'attempt_conditions', ['attempt_id'])
    op.create_index('ix_attempt_conditions_student', 'attempt_conditions', ['student_id'])
    op.create_table('knowledge_diagnoses',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('student_id', sa.String(64), nullable=False),
        sa.Column('knowledge_id', sa.String(64), nullable=False),
        sa.Column('kind', sa.String(24), nullable=False),
        sa.Column('scope_text', sa.Text(), nullable=False),
        sa.Column('evidence_ids', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('actor_id', sa.String(64), nullable=False),
        sa.Column('supersedes_id', sa.String(64)),
        ts('created_at'),
        student_fk(),
        sa.CheckConstraint("kind IN ('suspected_cause', 'verified_weak_scope')", name='valid_diagnosis_kind'),
        sa.CheckConstraint("status IN ('active', 'retracted')", name='valid_diagnosis_status'))
    op.create_index('ix_knowledge_diagnoses_student', 'knowledge_diagnoses', ['student_id', 'knowledge_id'])
    op.create_table('learning_evidence',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('campus_id', sa.String(64), nullable=False),
        sa.Column('student_id', sa.String(64), nullable=False),
        sa.Column('knowledge_id', sa.String(64), nullable=False),
        sa.Column('source_key', sa.String(64), nullable=False),
        sa.Column('document', sa.JSON(), nullable=False),
        ts('occurred_at'),
        ts('recorded_at'),
        sa.Column('validity', sa.String(16), nullable=False),
        ts('invalidated_at', True),
        sa.Column('invalidation_reason', sa.String(48)),
        student_fk(),
        sa.CheckConstraint("validity IN ('valid', 'invalidated')", name='valid_evidence_validity'))
    op.create_index('ix_learning_evidence_student', 'learning_evidence', ['student_id', 'validity'])
    op.create_index('ix_learning_evidence_key', 'learning_evidence', ['student_id', 'source_key'])
    op.create_table('student_knowledge_states',
        sa.Column('student_id', sa.String(64), primary_key=True),
        sa.Column('knowledge_id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('graph_version', sa.String(64), nullable=False),
        sa.Column('rule_version', sa.String(32), nullable=False),
        sa.Column('state_version', sa.Integer(), nullable=False),
        sa.Column('document', sa.JSON(), nullable=False),
        ts('computed_at'),
        student_fk())
    op.create_table('learning_projections',
        sa.Column('student_id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('source_fingerprint', sa.String(64), nullable=False),
        sa.Column('evidence_revision', sa.Integer(), nullable=False),
        sa.Column('state_version', sa.Integer(), nullable=False),
        ts('projected_at'),
        student_fk())
    op.create_table('learning_outbox',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('student_id', sa.String(64), nullable=False),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('dedupe_key', sa.String(160), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('tries', sa.Integer(), nullable=False),
        ts('available_at'),
        sa.Column('lease_token', sa.String(64)),
        ts('lease_expires_at', True),
        sa.Column('last_error', sa.String(64)),
        ts('created_at'),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'dead')", name='valid_outbox_status'))
    op.create_index('ix_learning_outbox_claim', 'learning_outbox', ['status', 'available_at'])
    op.create_index('ix_learning_outbox_student', 'learning_outbox', ['student_id'])
    op.create_index('ix_learning_outbox_dedupe', 'learning_outbox', ['dedupe_key', 'status'])

    seed = json.loads(SEED.read_text(encoding='utf-8'))
    op.bulk_insert(graphs, [{'version': seed['graph_version'], 'subject': seed['subject'], 'title': seed['title'],
                             'status': 'published', 'review_status': seed['review_status'],
                             'source_note': seed['source_note'], 'published_at': datetime(2026, 10, 17, tzinfo=timezone.utc)}])
    op.bulk_insert(nodes, [{'graph_version': seed['graph_version'], **node} for node in seed['nodes']])
    op.bulk_insert(edges, [{'graph_version': seed['graph_version'], **edge} for edge in seed['edges']])


def downgrade():
    for table in ('learning_outbox', 'learning_projections', 'student_knowledge_states', 'learning_evidence',
                  'knowledge_diagnoses', 'attempt_conditions', 'question_knowledge_links',
                  'student_learning_profiles', 'knowledge_edges', 'knowledge_nodes', 'curriculum_graphs'):
        op.drop_table(table)
