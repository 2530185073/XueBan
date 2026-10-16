"""Half-month learning reports with immutable published versions and guardian share links."""
from alembic import op
import sqlalchemy as sa

revision = '0008_learning_reports'
down_revision = '0007_agent_memory'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('learning_reports',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('campus_id', sa.String(64), nullable=False),
        sa.Column('student_id', sa.String(64), nullable=False),
        sa.Column('period_start', sa.String(10), nullable=False),
        sa.Column('period_end', sa.String(10), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('source_digest', sa.String(64), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('staff_note', sa.Text()),
        sa.Column('next_step', sa.Text()),
        sa.Column('correction_note', sa.Text()),
        sa.Column('supersedes_id', sa.String(64)),
        sa.Column('regenerated_reason', sa.String(48)),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('published_by', sa.String(64)),
        sa.Column('published_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['student_id', 'tenant_id'], ['students.id', 'students.tenant_id']),
        sa.CheckConstraint("status IN ('draft', 'published', 'superseded')", name='valid_report_status'),
        sa.CheckConstraint("version >= 1", name='positive_report_version'))
    op.create_index('ix_learning_reports_period', 'learning_reports', ['student_id', 'period_start', 'version'], unique=True)
    op.create_index('ix_learning_reports_status', 'learning_reports', ['tenant_id', 'campus_id', 'status'])
    op.create_table('report_share_links',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('student_id', sa.String(64), nullable=False),
        sa.Column('report_id', sa.String(64), nullable=False),
        sa.Column('token_digest', sa.String(64), nullable=False),
        sa.Column('created_by', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True)),
        sa.Column('view_count', sa.Integer(), nullable=False),
        sa.Column('last_viewed_at', sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(['student_id', 'tenant_id'], ['students.id', 'students.tenant_id']))
    op.create_index('ix_report_share_token', 'report_share_links', ['token_digest'], unique=True)


def downgrade():
    op.drop_table('report_share_links')
    op.drop_table('learning_reports')
