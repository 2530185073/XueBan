"""Private assets, versioned correction records and leased durable tasks."""
from alembic import op
import sqlalchemy as sa

revision = "0003_correction"
down_revision = "0002_device_pairing"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("homeworks", sa.Column("coverage_json", sa.JSON(), nullable=True))
    op.create_table("correction_assets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("homework_id", sa.String(64), sa.ForeignKey("homeworks.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("upload_token_hash", sa.String(64)), sa.Column("upload_actor_id", sa.String(64), sa.ForeignKey("principals.id")),
        sa.Column("upload_expires_at", sa.DateTime(timezone=True)),
        sa.Column("read_token_hash", sa.String(64)), sa.Column("read_actor_id", sa.String(64), sa.ForeignKey("principals.id")),
        sa.Column("read_expires_at", sa.DateTime(timezone=True)))
    op.create_index("ix_correction_assets_homework_id", "correction_assets", ["homework_id"])
    op.create_table("correction_entities",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("homework_id", sa.String(64), sa.ForeignKey("homeworks.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False), sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False), sa.Column("annotations", sa.JSON()))
    op.create_index("ix_correction_entities_homework_id", "correction_entities", ["homework_id"])
    op.create_index("ix_correction_entity_homework_kind", "correction_entities", ["homework_id", "kind"])
    op.create_table("correction_snapshots",
        sa.Column("entity_id", sa.String(64), sa.ForeignKey("correction_entities.id"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True), sa.Column("document", sa.JSON(), nullable=False))
    op.create_table("correction_tasks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("homework_id", sa.String(64), sa.ForeignKey("homeworks.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False), sa.Column("audit", sa.JSON()),
        sa.Column("tries", sa.Integer(), nullable=False), sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(64)), sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.create_index("ix_correction_tasks_homework_id", "correction_tasks", ["homework_id"])
    op.create_index("ix_correction_task_claim", "correction_tasks", ["status", "available_at", "lease_expires_at"])
    op.create_table("correction_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("homework_id", sa.String(64), sa.ForeignKey("homeworks.id"), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False), sa.Column("aggregate_revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False), sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("aggregate_id", "aggregate_revision", "kind", name="uq_correction_event_revision"))
    op.create_index("ix_correction_events_homework_id", "correction_events", ["homework_id"])


def downgrade():
    for name in ("correction_events", "correction_tasks", "correction_snapshots", "correction_entities", "correction_assets"):
        op.drop_table(name)
    op.drop_column("homeworks", "coverage_json")
