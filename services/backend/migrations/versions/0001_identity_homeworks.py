"""Identity mappings, authorized students, durable homework and idempotency."""
from alembic import op
import sqlalchemy as sa

revision = "0001_identity_homeworks"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("tenants", sa.Column("id", sa.String(64), primary_key=True))
    op.create_table("campuses",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.UniqueConstraint("id", "tenant_id"),
    )
    op.create_table("principals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("issuer", "subject"),
        sa.UniqueConstraint("id", "tenant_id"),
    )
    op.create_table("principal_roles",
        sa.Column("principal_id", sa.String(64), sa.ForeignKey("principals.id"), primary_key=True),
        sa.Column("role", sa.String(32), primary_key=True),
        sa.CheckConstraint("role IN ('student', 'sitting_teacher', 'campus_admin', 'data_steward')", name="valid_principal_role"),
    )
    op.create_table("students",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("campus_id", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["campus_id", "tenant_id"], ["campuses.id", "campuses.tenant_id"]),
        sa.UniqueConstraint("id", "tenant_id"),
        sa.UniqueConstraint("id", "campus_id", "tenant_id"),
    )
    op.create_table("student_grants",
        sa.Column("principal_id", sa.String(64), primary_key=True),
        sa.Column("student_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["principal_id", "tenant_id"], ["principals.id", "principals.tenant_id"]),
        sa.ForeignKeyConstraint(["student_id", "tenant_id"], ["students.id", "students.tenant_id"]),
    )
    op.create_table("homeworks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("campus_id", sa.String(64), nullable=False),
        sa.Column("student_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("curriculum", sa.JSON(), nullable=False),
        sa.Column("title", sa.String(160)),
        sa.Column("collection_scope", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processing_blocked", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["student_id", "campus_id", "tenant_id"], ["students.id", "students.campus_id", "students.tenant_id"]),
        sa.CheckConstraint("revision >= 1", name="positive_homework_revision"),
    )
    op.create_index("ix_homeworks_student_id", "homeworks", ["student_id"])
    op.create_table("idempotency_records",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("principal_id", sa.String(64), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("student_id", sa.String(64), sa.ForeignKey("students.id"), nullable=False),
        sa.Column("operation", sa.String(128), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("response_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("principal_id", "student_id", "operation", "key", name="uq_idempotency_scope"),
    )


def downgrade():
    op.drop_table("idempotency_records")
    op.drop_index("ix_homeworks_student_id", table_name="homeworks")
    op.drop_table("homeworks")
    op.drop_table("student_grants")
    op.drop_table("students")
    op.drop_table("principal_roles")
    op.drop_table("principals")
    op.drop_table("campuses")
    op.drop_table("tenants")
