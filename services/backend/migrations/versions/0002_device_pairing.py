"""Add administrator issued device pairing and revocable sessions."""
from alembic import op
import sqlalchemy as sa

revision = "0002_device_pairing"
down_revision = "0001_identity_homeworks"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("device_pairings",
        sa.Column("code_hash", sa.String(64), primary_key=True),
        sa.Column("principal_id", sa.String(64), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redemption_key_hash", sa.String(64)),
        sa.Column("session_id", sa.String(64)))
    op.create_table("device_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("principal_id", sa.String(64), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False))
    op.create_index("ix_device_sessions_principal_id", "device_sessions", ["principal_id"])


def downgrade():
    op.drop_table("device_sessions")
    op.drop_table("device_pairings")
