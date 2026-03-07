"""add index on family_member.user_id

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-06

Changes:
- Add idx_fm_user index on family_member(user_id) for faster user-based lookups
"""
from typing import Sequence, Union
from alembic import op

revision: str = 'b2c3d4e5f6g7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('idx_fm_user', 'family_member', ['user_id'])


def downgrade() -> None:
    op.drop_index('idx_fm_user', table_name='family_member')
