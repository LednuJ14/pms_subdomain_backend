"""Merge multiple heads

Revision ID: 6a73de349206
Revises: add_2fa_to_users, add_notifications_table, add_rental_contracts
Create Date: 2026-06-29 21:36:35.388866

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6a73de349206'
down_revision = ('add_2fa_to_users', 'add_notifications_table', 'add_rental_contracts')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
