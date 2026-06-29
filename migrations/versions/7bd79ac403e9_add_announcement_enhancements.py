"""Add announcement enhancements

Revision ID: 7bd79ac403e9
Revises: 3a451f551be7
Create Date: 2025-09-30 08:26:31.282166

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7bd79ac403e9'
down_revision = '3a451f551be7'
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns to announcements table
    op.add_column('announcements', sa.Column('priority', sa.Enum('LOW', 'MEDIUM', 'HIGH', name='announcementpriority'), nullable=False, server_default='MEDIUM'))
    op.add_column('announcements', sa.Column('target_audience', sa.Enum('ALL', 'TENANTS', 'STAFF', 'OWNERS', name='targetaudience'), nullable=False, server_default='ALL'))
    op.add_column('announcements', sa.Column('is_pinned', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column('announcements', sa.Column('send_notification', sa.Boolean(), nullable=False, server_default='1'))
    
    # Update announcement_type enum to include 'PAYMENT'
    # First, create a new enum with all values
    op.execute("ALTER TABLE announcements MODIFY COLUMN announcement_type ENUM('general', 'maintenance', 'emergency', 'event', 'policy', 'payment') NOT NULL DEFAULT 'general'")


def downgrade():
    # Remove the new columns
    op.drop_column('announcements', 'send_notification')
    op.drop_column('announcements', 'is_pinned')
    op.drop_column('announcements', 'target_audience')
    op.drop_column('announcements', 'priority')
    
    # Revert announcement_type enum to original values
    op.execute("ALTER TABLE announcements MODIFY COLUMN announcement_type ENUM('general', 'maintenance', 'emergency', 'event', 'policy') NOT NULL DEFAULT 'general'")
