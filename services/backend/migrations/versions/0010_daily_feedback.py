"""Daily guardian feedback shares the report table, keyed by cadence."""
from alembic import op
import sqlalchemy as sa

revision = '0010_daily_feedback'
down_revision = '0009_optional_practice'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('learning_reports') as batch:
        batch.add_column(sa.Column('cadence', sa.String(16), nullable=False, server_default='half_month'))
        batch.create_check_constraint('valid_report_cadence', "cadence IN ('half_month', 'daily')")
        batch.drop_index('ix_learning_reports_period')
        batch.create_index('ix_learning_reports_period', ['student_id', 'cadence', 'period_start', 'version'], unique=True)


def downgrade():
    op.execute("DELETE FROM report_share_links WHERE report_id IN (SELECT id FROM learning_reports WHERE cadence = 'daily')")
    op.execute("DELETE FROM learning_reports WHERE cadence = 'daily'")
    with op.batch_alter_table('learning_reports') as batch:
        batch.drop_index('ix_learning_reports_period')
        batch.create_index('ix_learning_reports_period', ['student_id', 'period_start', 'version'], unique=True)
        batch.drop_constraint('valid_report_cadence', type_='check')
        batch.drop_column('cadence')
