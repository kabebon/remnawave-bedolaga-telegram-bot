"""add whitelist_blocked to subscriptions

Display-only flag indicating that the whitelist (premium servers) was
exhausted and removed by the external traffic-limiter service. Does not
affect renewal/balance/traffic topup logic. Only set for tariffs with a
traffic limit (traffic_limit_gb > 0). Defaults to false.

Revision ID: 0102
Revises: 0101
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = '0102'
down_revision: Union[str, None] = '0101'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return column in [c['name'] for c in inspector.get_columns(table)]


def upgrade() -> None:
    if not _has_column('subscriptions', 'whitelist_blocked'):
        op.add_column(
            'subscriptions',
            sa.Column(
                'whitelist_blocked', sa.Boolean(), nullable=False, server_default=sa.text('false')
            ),
        )


def downgrade() -> None:
    op.drop_column('subscriptions', 'whitelist_blocked')
