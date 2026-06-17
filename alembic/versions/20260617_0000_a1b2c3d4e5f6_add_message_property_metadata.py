"""add_message_property_metadata

Añade has_properties / property_count a messages para preservar la metadata
de compactación de contexto al restaurar una sesión desde la DB.

Revision ID: a1b2c3d4e5f6
Revises:     da871408ec95
Create Date: 2026-06-17 00:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'da871408ec95'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'messages',
        sa.Column('has_properties', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'messages',
        sa.Column('property_count', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('messages', 'property_count')
    op.drop_column('messages', 'has_properties')
