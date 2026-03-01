"""add openid to user and nullable password_hash

Revision ID: a1b2c3d4e5f6
Revises: 7d2e52671ffb
Create Date: 2026-03-01

Changes:
- [P1-2] Add openid column to user table (unique, nullable, indexed)
- [P1-2] Make password_hash nullable (for WeChat-only users)
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '7d2e52671ffb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add openid column
    op.add_column('user', sa.Column('openid', sa.String(100), nullable=True))
    op.create_index('idx_user_openid', 'user', ['openid'], unique=True)

    # Make password_hash nullable (微信登录用户没有密码)
    op.alter_column('user', 'password_hash',
                     existing_type=sa.String(255),
                     nullable=True)


def downgrade() -> None:
    op.alter_column('user', 'password_hash',
                     existing_type=sa.String(255),
                     nullable=False)
    op.drop_index('idx_user_openid', table_name='user')
    op.drop_column('user', 'openid')
