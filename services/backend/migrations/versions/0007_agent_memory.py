"""Per-student agent memory items and agent run audit records."""
from alembic import op
import sqlalchemy as sa

revision = '0007_agent_memory'
down_revision = '0006_learning_graph'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('agent_memory_items',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('student_id', sa.String(64), nullable=False),
        sa.Column('category', sa.String(32), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('knowledge_id', sa.String(64)),
        sa.Column('evidence_ids', sa.JSON(), nullable=False),
        sa.Column('source', sa.String(16), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('status_reason', sa.String(48)),
        sa.Column('rule_key', sa.String(160)),
        sa.Column('proposed_by', sa.String(64)),
        sa.Column('confirmed_by', sa.String(64)),
        sa.Column('confirmed_at', sa.DateTime(timezone=True)),
        sa.Column('valid_until', sa.DateTime(timezone=True)),
        sa.Column('supersedes_id', sa.String(64)),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['student_id', 'tenant_id'], ['students.id', 'students.tenant_id']),
        sa.CheckConstraint("category IN ('recurring_difficulty', 'progress_milestone', 'effective_support', 'support_agreement', 'learning_goal', 'interface_preference')", name='valid_memory_category'),
        sa.CheckConstraint("source IN ('system_rule', 'model', 'staff', 'student')", name='valid_memory_source'),
        sa.CheckConstraint("status IN ('proposed', 'confirmed', 'superseded', 'expired', 'retracted', 'invalidated')", name='valid_memory_status'))
    op.create_index('ix_agent_memory_student', 'agent_memory_items', ['student_id', 'status'])
    op.create_index('ix_agent_memory_rule', 'agent_memory_items', ['student_id', 'rule_key'])
    op.create_table('agent_runs',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('student_id', sa.String(64), nullable=False),
        sa.Column('purpose', sa.String(32), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('subject_ref', sa.String(64)),
        sa.Column('context', sa.JSON(), nullable=False),
        sa.Column('output', sa.JSON()),
        sa.Column('model_metadata', sa.JSON()),
        sa.Column('requested_by', sa.String(64)),
        sa.Column('failure_code', sa.String(64)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(['student_id', 'tenant_id'], ['students.id', 'students.tenant_id']),
        sa.CheckConstraint("purpose IN ('hint_context', 'staff_summary')", name='valid_agent_purpose'),
        sa.CheckConstraint("status IN ('queued', 'completed', 'needs_human', 'failed', 'stale')", name='valid_agent_status'))
    op.create_index('ix_agent_runs_student', 'agent_runs', ['student_id', 'purpose'])


def downgrade():
    op.drop_table('agent_runs')
    op.drop_table('agent_memory_items')
