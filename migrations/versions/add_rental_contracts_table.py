"""Add rental contracts table

Revision ID: add_rental_contracts
Revises: 9eb66a668f70
Create Date: 2025-01-XX XX:XX:XX.XXXXXX

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy import Numeric

# revision identifiers, used by Alembic.
revision = 'add_rental_contracts'
down_revision = '9eb66a668f70'
branch_labels = None
depends_on = None


def upgrade():
    # Create rental_contracts table
    op.create_table(
        'rental_contracts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('contract_number', sa.String(50), nullable=False),
        sa.Column('tenant_unit_id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('unit_id', sa.Integer(), nullable=False),
        sa.Column('property_id', sa.Integer(), nullable=False),
        sa.Column('contract_type', sa.String(20), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('monthly_rent', Numeric(10, 2), nullable=False),
        sa.Column('security_deposit', Numeric(10, 2), nullable=True),
        sa.Column('total_contract_value', Numeric(10, 2), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='draft'),
        sa.Column('terms_and_conditions', sa.Text(), nullable=True),
        sa.Column('special_conditions', sa.Text(), nullable=True),
        sa.Column('is_renewal', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('parent_contract_id', sa.Integer(), nullable=True),
        sa.Column('renewal_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tenant_signed', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('tenant_signed_date', sa.DateTime(), nullable=True),
        sa.Column('landlord_signed', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('landlord_signed_date', sa.DateTime(), nullable=True),
        sa.Column('landlord_signed_by', sa.Integer(), nullable=True),
        sa.Column('termination_date', sa.Date(), nullable=True),
        sa.Column('termination_reason', sa.Text(), nullable=True),
        sa.Column('terminated_by', sa.Integer(), nullable=True),
        sa.Column('contract_document_path', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['tenant_unit_id'], ['tenant_units.id'], name='fk_contract_tenant_unit'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name='fk_contract_tenant'),
        sa.ForeignKeyConstraint(['unit_id'], ['units.id'], name='fk_contract_unit'),
        sa.ForeignKeyConstraint(['property_id'], ['properties.id'], name='fk_contract_property'),
        sa.ForeignKeyConstraint(['parent_contract_id'], ['rental_contracts.id'], name='fk_contract_parent'),
        sa.ForeignKeyConstraint(['landlord_signed_by'], ['users.id'], name='fk_contract_landlord_signed_by'),
        sa.ForeignKeyConstraint(['terminated_by'], ['users.id'], name='fk_contract_terminated_by'),
    )
    
    # Create indexes
    op.create_index('idx_contract_number', 'rental_contracts', ['contract_number'], unique=True)
    op.create_index('idx_contract_tenant_unit', 'rental_contracts', ['tenant_unit_id'])
    op.create_index('idx_contract_tenant', 'rental_contracts', ['tenant_id'])
    op.create_index('idx_contract_status', 'rental_contracts', ['status'])
    op.create_index('idx_contract_dates', 'rental_contracts', ['start_date', 'end_date'])


def downgrade():
    # Drop indexes
    op.drop_index('idx_contract_dates', table_name='rental_contracts')
    op.drop_index('idx_contract_status', table_name='rental_contracts')
    op.drop_index('idx_contract_tenant', table_name='rental_contracts')
    op.drop_index('idx_contract_tenant_unit', table_name='rental_contracts')
    op.drop_index('idx_contract_number', table_name='rental_contracts')
    
    # Drop table
    op.drop_table('rental_contracts')

