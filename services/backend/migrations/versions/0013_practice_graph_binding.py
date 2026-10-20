"""Pin practice content and history to the graph that authored it."""
import json

from alembic import op
import sqlalchemy as sa

revision = "0013_practice_graph_binding"
down_revision = "0012_curriculum_binding"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("practice_items", "practice_sessions", "practice_responses"):
        op.add_column(table, sa.Column("graph_version", sa.String(64), nullable=True))
    connection = op.get_bind()
    # Old records never stored a graph version. Only an unambiguous legacy
    # node may be restored; unresolved history stays NULL, never guessed.
    versions = {}
    for knowledge_id, version in connection.execute(sa.text(
            "SELECT n.id, n.graph_version FROM knowledge_nodes n "
            "JOIN curriculum_graphs g ON g.version=n.graph_version WHERE g.book_identity IS NULL")):
        versions.setdefault(knowledge_id, []).append(version)
    items = {}
    for item_id, knowledge_id in connection.execute(sa.text("SELECT id, knowledge_id FROM practice_items")):
        candidates = versions.get(knowledge_id, [])
        version = candidates[0] if len(candidates) == 1 else None
        items[item_id] = version
        if version:
            connection.execute(sa.text("UPDATE practice_items SET graph_version=:version WHERE id=:id"),
                               {"version": version, "id": item_id})
    for row_id, item_ids in connection.execute(sa.text("SELECT id, item_ids FROM practice_sessions")):
        ids = json.loads(item_ids) if isinstance(item_ids, str) else item_ids
        candidates = {items.get(item_id) for item_id in ids}
        if ids and len(candidates) == 1 and None not in candidates:
            connection.execute(sa.text("UPDATE practice_sessions SET graph_version=:version WHERE id=:id"),
                               {"version": candidates.pop(), "id": row_id})
    connection.execute(sa.text(
        "UPDATE practice_responses SET graph_version=(SELECT graph_version FROM practice_items "
        "WHERE practice_items.id=practice_responses.item_id)"))
    with op.batch_alter_table("practice_items") as batch:
        batch.create_foreign_key("fk_practice_item_knowledge_graph", "knowledge_nodes",
                                 ["graph_version", "knowledge_id"], ["graph_version", "id"])
        batch.create_index("ix_practice_items_graph", ["tenant_id", "graph_version", "knowledge_id", "status"])
    with op.batch_alter_table("practice_sessions") as batch:
        batch.create_foreign_key("fk_practice_session_graph", "curriculum_graphs", ["graph_version"], ["version"])
    with op.batch_alter_table("practice_responses") as batch:
        batch.create_foreign_key("fk_practice_response_knowledge_graph", "knowledge_nodes",
                                 ["graph_version", "knowledge_id"], ["graph_version", "id"])


def downgrade():
    with op.batch_alter_table("practice_responses") as batch:
        batch.drop_constraint("fk_practice_response_knowledge_graph", type_="foreignkey")
        batch.drop_column("graph_version")
    with op.batch_alter_table("practice_sessions") as batch:
        batch.drop_constraint("fk_practice_session_graph", type_="foreignkey")
        batch.drop_column("graph_version")
    with op.batch_alter_table("practice_items") as batch:
        batch.drop_index("ix_practice_items_graph")
        batch.drop_constraint("fk_practice_item_knowledge_graph", type_="foreignkey")
        batch.drop_column("graph_version")
