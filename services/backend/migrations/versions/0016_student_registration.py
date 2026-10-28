"""Opt-in student self-registration, monotonic names and retry recovery."""
from alembic import op
import sqlalchemy as sa

revision = '0016_student_registration'
down_revision = '0015_observation_bindings'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('registration_counters',
        sa.Column('name', sa.String(64), primary_key=True),
        sa.Column('last_number', sa.Integer(), nullable=False))
    op.create_table('student_registrations',
        sa.Column('key_hash', sa.String(64), primary_key=True),
        sa.Column('request_salt', sa.String(32), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('username', sa.String(64), sa.ForeignKey('password_accounts.username', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('grade', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('student_registrations')
    op.drop_table('registration_counters')
