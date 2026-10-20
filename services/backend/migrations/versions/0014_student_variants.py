"""Child-owned, AI-verified variants and structured voluntary answers."""
from alembic import op
import sqlalchemy as sa

revision = "0014_student_variants"
down_revision = "0013_practice_graph_binding"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("practice_items") as batch:
        batch.alter_column("knowledge_id", existing_type=sa.String(64), nullable=True)
        for name, kind in (("owner_student_id", sa.String(64)), ("question_type", sa.String(32)),
                           ("question_kind", sa.String(16)), ("options", sa.JSON()), ("answer_fields", sa.JSON()),
                           ("field_answers", sa.JSON()), ("verification", sa.JSON())):
            batch.add_column(sa.Column(name, kind, nullable=True))
        batch.create_foreign_key("fk_practice_item_owner", "students", ["owner_student_id", "tenant_id"], ["id", "tenant_id"])
        batch.drop_constraint("valid_practice_status", type_="check")
        batch.create_check_constraint("valid_practice_status", "status IN ('candidate', 'approved', 'ai_verified', 'rejected', 'retired')")
    with op.batch_alter_table("practice_responses") as batch:
        batch.alter_column("knowledge_id", existing_type=sa.String(64), nullable=True)
        batch.add_column(sa.Column("details", sa.JSON(), nullable=True))
    op.create_table("variant_generations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False), sa.Column("student_id", sa.String(64), nullable=False),
        sa.Column("source_case_id", sa.String(64), nullable=False), sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("graph_version", sa.String(64)), sa.Column("knowledge_id", sa.String(64)),
        sa.Column("question_type", sa.String(32), nullable=False), sa.Column("question_kind", sa.String(16), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure_code", sa.String(64)), sa.Column("item_id", sa.String(64)), sa.Column("session_id", sa.String(64)),
        sa.Column("outbox_id", sa.String(64)), sa.Column("audit", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["student_id", "tenant_id"], ["students.id", "students.tenant_id"]),
        sa.CheckConstraint("status IN ('queued','generating','verifying','ready','failed','superseded')", name="valid_variant_generation_status"))
    op.create_index("ix_variant_generation_source", "variant_generations", ["student_id", "source_fingerprint"], unique=True)
    op.create_index("ix_variant_generation_student", "variant_generations", ["student_id", "created_at"])

    op.create_table("practice_assessments",
        sa.Column("id", sa.String(64), primary_key=True), sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("student_id", sa.String(64), nullable=False), sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("item_id", sa.String(64), nullable=False), sa.Column("graph_version", sa.String(64)),
        sa.Column("item_revision", sa.Integer(), nullable=False), sa.Column("item_digest", sa.String(64), nullable=False),
        sa.Column("submission", sa.JSON(), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", sa.String(16)), sa.Column("feedback", sa.Text()), sa.Column("outbox_id", sa.String(64)),
        sa.Column("audit", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["student_id", "tenant_id"], ["students.id", "students.tenant_id"]),
        sa.CheckConstraint("status IN ('queued','grading','completed','failed','superseded')", name="valid_practice_assessment_status"))
    op.create_index("ix_practice_assessment_item", "practice_assessments", ["session_id", "item_id", "created_at"])


def downgrade():
    # A downgrade with child-owned records would discard evidence. Refuse it;
    # empty/test databases can still round-trip the schema safely.
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT count(*) FROM variant_generations")).scalar() or connection.execute(
            sa.text("SELECT count(*) FROM practice_items WHERE owner_student_id IS NOT NULL")).scalar():
        raise RuntimeError("Cannot downgrade student variants while generation evidence exists")
    op.drop_table("practice_assessments")
    op.drop_table("variant_generations")
    with op.batch_alter_table("practice_responses") as batch:
        batch.drop_column("details")
        batch.alter_column("knowledge_id", existing_type=sa.String(64), nullable=False)
    with op.batch_alter_table("practice_items") as batch:
        batch.drop_constraint("fk_practice_item_owner", type_="foreignkey")
        batch.drop_constraint("valid_practice_status", type_="check")
        batch.create_check_constraint("valid_practice_status", "status IN ('candidate', 'approved', 'rejected', 'retired')")
        for name in ("verification", "field_answers", "answer_fields", "options", "question_kind", "question_type", "owner_student_id"):
            batch.drop_column(name)
        batch.alter_column("knowledge_id", existing_type=sa.String(64), nullable=False)
