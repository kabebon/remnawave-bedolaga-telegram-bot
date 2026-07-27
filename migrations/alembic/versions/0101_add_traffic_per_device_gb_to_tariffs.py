"""add traffic_per_device_gb to tariffs

Amount of additional traffic (in GB) added to subscription pool per each additional purchased device slot. Defaults to 0 (disabled).

Revision ID: 0101
Revises: 0100
"""

from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = '0101'
down_revision: Union[str, None] = '0100'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return column in [c['name'] for c in inspector.get_columns(table)]


def upgrade() -> None:
    if not _has_column('tariffs', 'traffic_per_device_gb'):
        op.add_column(
            'tariffs',
            sa.Column('traffic_per_device_gb', sa.Integer(), nullable=False, server_default=sa.text('0')),
        )


def downgrade() -> None:
    op.drop_column('tariffs', 'traffic_per_device_gb')
