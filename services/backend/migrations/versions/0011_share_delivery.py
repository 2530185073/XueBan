"""Record, per guardian link, that staff copied the message and that staff confirmed sending it."""
from alembic import op
import sqlalchemy as sa

revision = '0011_share_delivery'
down_revision = '0010_daily_feedback'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('report_share_links') as batch:
        batch.add_column(sa.Column('message_copied_at', sa.DateTime(timezone=True)))
        batch.add_column(sa.Column('sent_confirmed_at', sa.DateTime(timezone=True)))
        batch.add_column(sa.Column('sent_confirmed_by', sa.String(64)))
        batch.add_column(sa.Column('sent_note', sa.Text()))


def downgrade():
    with op.batch_alter_table('report_share_links') as batch:
        batch.drop_column('sent_note')
        batch.drop_column('sent_confirmed_by')
        batch.drop_column('sent_confirmed_at')
        batch.drop_column('message_copied_at')
