"""Reviewed practice bank, daily optional-activity windows, voluntary sessions and responses."""
from alembic import op
import sqlalchemy as sa

revision = '0009_optional_practice'
down_revision = '0008_learning_reports'
branch_labels = None
depends_on = None


def student_fk():
    return sa.ForeignKeyConstraint(['student_id', 'tenant_id'], ['students.id', 'students.tenant_id'])


def upgrade():
    op.create_table('practice_items',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('knowledge_id', sa.String(64), nullable=False),
        sa.Column('item_family_id', sa.String(64), nullable=False),
        sa.Column('stem_text', sa.Text(), nullable=False),
        sa.Column('answer_kind', sa.String(16), nullable=False),
        sa.Column('answer_values', sa.JSON(), nullable=False),
        sa.Column('hint_text', sa.Text()),
        sa.Column('solution_text', sa.Text(), nullable=False),
        sa.Column('difficulty', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(16), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('generation', sa.JSON()),
        sa.Column('created_by', sa.String(64)),
        sa.Column('reviewed_by', sa.String(64)),
        sa.Column('reviewed_at', sa.DateTime(timezone=True)),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("answer_kind IN ('number', 'choice', 'text')", name='valid_practice_answer_kind'),
        sa.CheckConstraint("status IN ('candidate', 'approved', 'rejected', 'retired')", name='valid_practice_status'),
        sa.CheckConstraint("source IN ('staff', 'model')", name='valid_practice_source'),
        sa.CheckConstraint("difficulty BETWEEN 1 AND 3", name='valid_practice_difficulty'))
    op.create_index('ix_practice_items_knowledge', 'practice_items', ['tenant_id', 'knowledge_id', 'status'])
    op.create_table('optional_windows',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('student_id', sa.String(64), nullable=False),
        sa.Column('local_date', sa.String(10), nullable=False),
        sa.Column('capacity_observed', sa.Boolean(), nullable=False),
        sa.Column('extra_minutes', sa.Integer(), nullable=False),
        sa.Column('school_tasks_confirmed', sa.Boolean()),
        sa.Column('updated_by', sa.String(64), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        student_fk(),
        sa.CheckConstraint("extra_minutes BETWEEN 0 AND 60", name='valid_optional_minutes'))
    op.create_index('ix_optional_windows_day', 'optional_windows', ['student_id', 'local_date'], unique=True)
    op.create_table('practice_sessions',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('student_id', sa.String(64), nullable=False),
        sa.Column('purpose', sa.String(16), nullable=False),
        sa.Column('knowledge_ids', sa.JSON(), nullable=False),
        sa.Column('source_case_id', sa.String(64)),
        sa.Column('item_ids', sa.JSON(), nullable=False),
        sa.Column('hints_used', sa.JSON(), nullable=False),
        sa.Column('gate', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('exit_reason', sa.String(32)),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('budget_ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True)),
        student_fk(),
        sa.CheckConstraint("purpose IN ('variant', 'preview')", name='valid_practice_purpose'),
        sa.CheckConstraint("status IN ('active', 'completed', 'exited', 'time_up')", name='valid_practice_session_status'))
    op.create_index('ix_practice_sessions_student', 'practice_sessions', ['student_id', 'started_at'])
    op.create_table('practice_responses',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('student_id', sa.String(64), nullable=False),
        sa.Column('session_id', sa.String(64), nullable=False),
        sa.Column('item_id', sa.String(64), nullable=False),
        sa.Column('knowledge_id', sa.String(64), nullable=False),
        sa.Column('event_type', sa.String(24), nullable=False),
        sa.Column('answer_text', sa.Text(), nullable=False),
        sa.Column('result', sa.String(16), nullable=False),
        sa.Column('hint_used', sa.Boolean(), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        student_fk(),
        sa.CheckConstraint("event_type IN ('optional_variant', 'optional_quiz')", name='valid_practice_event'),
        sa.CheckConstraint("result IN ('correct', 'incorrect', 'unscorable')", name='valid_practice_result'))
    op.create_index('ix_practice_responses_item', 'practice_responses', ['session_id', 'item_id'], unique=True)
    op.create_index('ix_practice_responses_student', 'practice_responses', ['student_id', 'knowledge_id'])


def downgrade():
    for table in ('practice_responses', 'practice_sessions', 'optional_windows', 'practice_items'):
        op.drop_table(table)
