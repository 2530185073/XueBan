"""Long-lived student accounts and explicit campus management authorization."""
from alembic import op
import sqlalchemy as sa
revision = '0004_password_accounts'
down_revision = '0003_correction'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('password_accounts',
        sa.Column('username', sa.String(64), primary_key=True),
        sa.Column('principal_id', sa.String(64), nullable=False, unique=True),
        sa.Column('student_id', sa.String(64), unique=True), sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('password_hash', sa.String(256), nullable=False), sa.Column('password_version', sa.Integer(), nullable=False),
        sa.Column('must_change_password', sa.Boolean(), nullable=False), sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('failed_attempts', sa.Integer(), nullable=False), sa.Column('locked_until', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['principal_id', 'tenant_id'], ['principals.id', 'principals.tenant_id']),
        sa.ForeignKeyConstraint(['student_id', 'tenant_id'], ['students.id', 'students.tenant_id']))
    op.create_table('principal_campus_grants',
        sa.Column('principal_id', sa.String(64), primary_key=True), sa.Column('campus_id', sa.String(64), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False), sa.Column('active', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['principal_id', 'tenant_id'], ['principals.id', 'principals.tenant_id']),
        sa.ForeignKeyConstraint(['campus_id', 'tenant_id'], ['campuses.id', 'campuses.tenant_id']))
    op.create_table('auth_operations',
        sa.Column('id', sa.String(64), primary_key=True), sa.Column('principal_id', sa.String(64), sa.ForeignKey('principals.id'), nullable=False),
        sa.Column('operation', sa.String(96), nullable=False), sa.Column('key_hash', sa.String(64), nullable=False),
        sa.Column('request_salt', sa.String(32), nullable=False), sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('session_id', sa.String(64), sa.ForeignKey('device_sessions.id')), sa.Column('previous_session_id', sa.String(64)),
        sa.Column('credential_version', sa.Integer()), sa.Column('response_json', sa.JSON()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('principal_id', 'operation', 'key_hash', name='uq_auth_operation'))
    op.create_table('login_limits', sa.Column('key', sa.String(64), primary_key=True),
        sa.Column('window_started', sa.DateTime(timezone=True), nullable=False), sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('locked_until', sa.DateTime(timezone=True)))


def downgrade():
    for name in ['login_limits', 'auth_operations', 'principal_campus_grants', 'password_accounts']:
        op.drop_table(name)
