"""Add two factor authentication and profile fields to users table

Revision ID: add_2fa_to_users
Revises: 9eb66a668f70
Create Date: 2025-01-27 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_2fa_to_users'
down_revision = '9eb66a668f70'
branch_labels = None
depends_on = None


def upgrade():
    # Add two-factor authentication columns
    with op.batch_alter_table('users', schema=None) as batch_op:
        # Check if columns exist before adding (for safety)
        try:
            batch_op.add_column(sa.Column('two_factor_enabled', sa.Boolean(), nullable=False, server_default='0'))
        except Exception:
            pass  # Column might already exist
        
        try:
            batch_op.add_column(sa.Column('two_factor_secret', sa.String(length=255), nullable=True))
        except Exception:
            pass
        
        try:
            batch_op.add_column(sa.Column('two_factor_backup_codes', sa.Text(), nullable=True))
        except Exception:
            pass
        
        # Add profile fields if they don't exist
        try:
            batch_op.add_column(sa.Column('avatar_url', sa.String(length=255), nullable=True))
        except Exception:
            pass
        
        try:
            batch_op.add_column(sa.Column('address', sa.Text(), nullable=True))
        except Exception:
            pass
        
        try:
            batch_op.add_column(sa.Column('emergency_contact_name', sa.String(length=100), nullable=True))
        except Exception:
            pass
        
        try:
            batch_op.add_column(sa.Column('emergency_contact_phone', sa.String(length=20), nullable=True))
        except Exception:
            pass


def downgrade():
    # Remove the columns
    with op.batch_alter_table('users', schema=None) as batch_op:
        try:
            batch_op.drop_column('emergency_contact_phone')
        except Exception:
            pass
        
        try:
            batch_op.drop_column('emergency_contact_name')
        except Exception:
            pass
        
        try:
            batch_op.drop_column('address')
        except Exception:
            pass
        
        try:
            batch_op.drop_column('avatar_url')
        except Exception:
            pass
        
        try:
            batch_op.drop_column('two_factor_backup_codes')
        except Exception:
            pass
        
        try:
            batch_op.drop_column('two_factor_secret')
        except Exception:
            pass
        
        try:
            batch_op.drop_column('two_factor_enabled')
        except Exception:
            pass

