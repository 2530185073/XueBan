"""Concrete textbook identity and immutable source review for published graphs."""
from alembic import op
import sqlalchemy as sa

revision = "0012_curriculum_binding"
down_revision = "0011_share_delivery"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("curriculum_graphs", sa.Column("book_identity", sa.JSON(none_as_null=True), nullable=True))
    op.add_column("curriculum_graphs", sa.Column("review_metadata", sa.JSON(none_as_null=True), nullable=True))
    op.add_column("curriculum_graphs", sa.Column("import_digest", sa.String(64), nullable=True))
    with op.batch_alter_table("knowledge_edges") as batch:
        batch.drop_constraint("valid_relation_type", type_="check")
        batch.create_check_constraint("valid_relation_type",
            "relation_type IN ('part_of','prerequisite_of','related_to','equivalent_to','supports','specializes_to')")


def downgrade():
    # Preserve source-authored relation semantics rather than silently convert
    # them to prerequisite edges during a downgrade.
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT COUNT(*) FROM knowledge_edges WHERE relation_type IN ('supports','specializes_to')")).scalar():
        raise RuntimeError("Cannot downgrade while source-reviewed relation types exist.")
    with op.batch_alter_table("knowledge_edges") as batch:
        batch.drop_constraint("valid_relation_type", type_="check")
        batch.create_check_constraint("valid_relation_type",
            "relation_type IN ('part_of','prerequisite_of','related_to','equivalent_to')")
    op.drop_column("curriculum_graphs", "import_digest")
    op.drop_column("curriculum_graphs", "review_metadata")
    op.drop_column("curriculum_graphs", "book_identity")
