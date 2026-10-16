"""Student data deletion requests with per-medium execution status."""
from alembic import op
import sqlalchemy as sa

revision = '0005_deletion_requests'
down_revision = '0004_password_accounts'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('deletion_requests',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('campus_id', sa.String(64), nullable=False),
        sa.Column('student_id', sa.String(64), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('targets', sa.JSON(), nullable=False),
        sa.Column('requested_by', sa.String(64), sa.ForeignKey('principals.id'), nullable=False),
        sa.Column('reason_hash', sa.String(64), nullable=False),
        sa.Column('idempotency_key_hash', sa.String(64), nullable=False),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('server_completed_at', sa.DateTime(timezone=True)),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.Column('lease_token', sa.String(64)),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True)),
        sa.Column('failure_code', sa.String(64)),
        sa.ForeignKeyConstraint(['student_id', 'tenant_id'], ['students.id', 'students.tenant_id']),
        sa.CheckConstraint("status IN ('accepted', 'running', 'waiting_devices', 'completed', 'failed')", name='valid_deletion_status'))
    op.create_index('ix_deletion_requests_student', 'deletion_requests', ['student_id'])
    op.create_index('ix_deletion_requests_claim', 'deletion_requests', ['status', 'lease_expires_at'])


def downgrade():
    op.drop_index('ix_deletion_requests_claim', 'deletion_requests')
    op.drop_index('ix_deletion_requests_student', 'deletion_requests')
    op.drop_table('deletion_requests')
