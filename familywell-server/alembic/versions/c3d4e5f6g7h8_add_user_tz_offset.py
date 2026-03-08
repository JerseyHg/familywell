"""add tz_offset to user table

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-03-07

Changes:
- Add tz_offset column (SmallInteger, nullable) to store user's timezone offset
  from JS getTimezoneOffset() for cron jobs to use per-user local date
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'c3d4e5f6g7h8'
down_revision: Union[str, None] = 'b2c3d4e5f6g7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user', sa.Column('tz_offset', sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('user', 'tz_offset')
