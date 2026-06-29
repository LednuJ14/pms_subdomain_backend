from datetime import datetime, timezone, date
from decimal import Decimal
from app import db
from sqlalchemy import Numeric
import enum

class BillType(enum.Enum):
    RENT = 'rent'
    UTILITIES = 'utilities'
    MAINTENANCE = 'maintenance'
    PARKING = 'parking'
    OTHER = 'other'

class BillStatus(enum.Enum):
    PENDING = 'pending'
    PAID = 'paid'
    PARTIAL = 'partial'
    OVERDUE = 'overdue'
    CANCELLED = 'cancelled'

class PaymentStatus(enum.Enum):
    PENDING_APPROVAL = 'pending_approval'  # Tenant submitted proof, waiting for manager approval
    APPROVED = 'approved'  # Manager approved the payment proof
    REJECTED = 'rejected'  # Manager rejected the payment proof
    COMPLETED = 'completed'  # Payment fully processed
    FAILED = 'failed'
    REFUNDED = 'refunded'

class PaymentMethod(enum.Enum):
    CASH = 'cash'
    GCASH = 'gcash'  # Added GCash payment method
    CHECK = 'check'
    BANK_TRANSFER = 'bank_transfer'
    CREDIT_CARD = 'credit_card'
    DEBIT_CARD = 'debit_card'
    ONLINE = 'online'
    MOBILE = 'mobile'

class Bill(db.Model):
    __tablename__ = 'bills'
    
    id = db.Column(db.Integer, primary_key=True)
    bill_number = db.Column(db.String(50), unique=True, nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey('units.id'), nullable=False)
    
    # Bill Details
    # Use String type instead of Enum to avoid validation issues with database enum values
    # Database enum: 'rent', 'utilities', 'maintenance', 'parking', 'other'
    bill_type = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    
    # Financial Information (simplified - single amount field)
    amount = db.Column(Numeric(10, 2), nullable=False)
    
    # Dates
    bill_date = db.Column(db.Date, nullable=False, default=date.today)
    due_date = db.Column(db.Date, nullable=False)
    period_start = db.Column(db.Date)  # For recurring bills
    period_end = db.Column(db.Date)    # For recurring bills
    
    # Status and Flags
    # Use String type instead of Enum to avoid validation issues with database enum values
    # Database enum: 'pending', 'paid', 'partial', 'overdue', 'cancelled'
    status = db.Column(db.String(20), default='pending', nullable=False)
    is_recurring = db.Column(db.Boolean, default=False)
    recurring_frequency = db.Column(db.String(20))  # monthly, quarterly, annually
    is_auto_generated = db.Column(db.Boolean, default=False)
    
    # Payment Information
    paid_date = db.Column(db.Date)
    payment_confirmation_number = db.Column(db.String(100))
    
    # Notes and Additional Info
    notes = db.Column(db.Text)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False)
    
    # Relationships
    # Note: Tenant model already defines bills relationship with backref='tenant'
    # So we use back_populates to avoid conflicts
    tenant = db.relationship('Tenant', back_populates='bills')
    unit = db.relationship('Unit', backref='bills')
    payments = db.relationship('Payment', backref='bill', cascade='all, delete-orphan')
    
    def __init__(self, bill_number, tenant_id, unit_id, bill_type, title, amount, due_date, **kwargs):
        self.bill_number = bill_number
        self.tenant_id = tenant_id
        self.unit_id = unit_id
        self.bill_type = bill_type
        self.title = title.strip()
        self.amount = amount
        self.due_date = due_date
        
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
    
    @property
    def is_overdue(self):
        """Check if bill is overdue."""
        status_str = str(self.status).lower() if self.status else 'pending'
        return self.due_date < date.today() and status_str not in ['paid', 'cancelled']
    
    @property
    def days_overdue(self):
        """Get number of days overdue."""
        if self.is_overdue:
            return (date.today() - self.due_date).days
        return 0
    
    @property
    def is_paid(self):
        """Check if bill is fully paid."""
        status_str = str(self.status).lower() if self.status else 'pending'
        return status_str == 'paid'
    
    @property
    def amount_paid(self):
        """Calculate total amount paid from completed payments."""
        from sqlalchemy import func
        total = db.session.query(func.sum(Payment.amount)).filter(
            Payment.bill_id == self.id,
            Payment.status.in_(['completed', 'approved'])  # Use string literals
        ).scalar()
        return Decimal(str(total)) if total else Decimal('0.00')
    
    @property
    def amount_due(self):
        """Calculate amount due (bill amount - amount paid)."""
        return max(Decimal('0.00'), self.amount - self.amount_paid)
    
    @property
    def is_partial_paid(self):
        """Check if bill is partially paid."""
        paid = self.amount_paid
        return paid > 0 and paid < self.amount
    
    def cancel_bill(self, reason=None):
        """Cancel the bill."""
        self.status = 'cancelled'  # Use string value
        if reason:
            self.notes = f"{self.notes}\nCancellation reason: {reason}" if self.notes else f"Cancellation reason: {reason}"
        db.session.commit()
    
    def update_status(self):
        """Update bill status based on payment and due date."""
        status_str = str(self.status).lower() if self.status else 'pending'
        if status_str == 'cancelled':
            return
        
        paid = self.amount_paid
        due = self.amount_due
        
        if due == 0:
            self.status = 'paid'  # Use string value
            if not self.paid_date:
                self.paid_date = date.today()
        elif paid > 0:
            self.status = 'partial'  # Use string value
        elif self.is_overdue:
            self.status = 'overdue'  # Use string value
        else:
            self.status = 'pending'  # Use string value
        
        db.session.commit()
    
    def to_dict(self, include_payments=False, include_tenant=False, include_unit=False):
        """Convert bill to dictionary."""
        data = {
            'id': self.id,
            'bill_number': self.bill_number,
            'tenant_id': self.tenant_id,
            'unit_id': self.unit_id,
            'bill_type': str(self.bill_type) if self.bill_type else 'other',
            'title': self.title,
            'description': self.description,
            'amount': float(self.amount),
            'amount_paid': float(self.amount_paid),
            'amount_due': float(self.amount_due),
            'bill_date': self.bill_date.isoformat(),
            'due_date': self.due_date.isoformat(),
            'period_start': self.period_start.isoformat() if self.period_start else None,
            'period_end': self.period_end.isoformat() if self.period_end else None,
            'status': str(self.status) if self.status else 'pending',
            'is_recurring': self.is_recurring,
            'recurring_frequency': self.recurring_frequency,
            'is_auto_generated': self.is_auto_generated,
            'is_overdue': self.is_overdue,
            'days_overdue': self.days_overdue,
            'is_paid': self.is_paid,
            'is_partial_paid': self.is_partial_paid,
            'paid_date': self.paid_date.isoformat() if self.paid_date else None,
            'payment_confirmation_number': self.payment_confirmation_number,
            'notes': self.notes,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }
        
        if include_payments:
            data['payments'] = [payment.to_dict() for payment in self.payments]
        
        if include_tenant:
            try:
                # Access tenant through relationship (back_populates)
                tenant = getattr(self, 'tenant', None)
                if tenant:
                    if hasattr(tenant, 'to_dict'):
                        tenant_dict = tenant.to_dict(include_user=True)
                        # Always include property information if available
                        if hasattr(tenant, 'property_id') and tenant.property_id:
                            tenant_dict['property_id'] = tenant.property_id
                            # Try to get property name from property relationship
                            if hasattr(tenant, 'property_obj') and tenant.property_obj:
                                property_obj = tenant.property_obj
                                tenant_dict['property_obj'] = {
                                    'id': property_obj.id,
                                    'name': getattr(property_obj, 'name', None) or getattr(property_obj, 'title', None) or getattr(property_obj, 'building_name', None) or f'Property {property_obj.id}',
                                    'title': getattr(property_obj, 'title', None),
                                    'building_name': getattr(property_obj, 'building_name', None)
                                }
                                tenant_dict['property'] = tenant_dict['property_obj']  # Alias
                            elif tenant.property_id:
                                tenant_dict['property'] = {
                                    'id': tenant.property_id,
                                    'name': f'Property {tenant.property_id}'
                                }
                        
                        # Include tenant_units for rent dates (rent_start_date, rent_end_date, move_in_date, move_out_date)
                        try:
                            from models.tenant import TenantUnit
                            from sqlalchemy import text
                            from datetime import date
                            
                            # Get tenant_units for this tenant, especially the one matching this bill's unit_id
                            tenant_units_result = db.session.execute(text(
                                """
                                SELECT tu.id, tu.tenant_id, tu.unit_id, tu.property_id,
                                       tu.rent_start_date, tu.rent_end_date, tu.move_in_date, tu.move_out_date,
                                       tu.monthly_rent, tu.security_deposit, tu.is_active, tu.rent_status
                                FROM tenant_units tu
                                WHERE tu.tenant_id = :tenant_id
                                ORDER BY tu.created_at DESC
                                """
                            ), {'tenant_id': tenant.id}).fetchall()
                            
                            tenant_units_list = []
                            for tu_row in tenant_units_result:
                                tenant_units_list.append({
                                    'id': tu_row[0],
                                    'tenant_id': tu_row[1],
                                    'unit_id': tu_row[2],
                                    'property_id': tu_row[3],
                                    'rent_start_date': tu_row[4].isoformat() if tu_row[4] else None,
                                    'rent_end_date': tu_row[5].isoformat() if tu_row[5] else None,
                                    'move_in_date': tu_row[6].isoformat() if tu_row[6] else None,
                                    'move_out_date': tu_row[7].isoformat() if tu_row[7] else None,
                                    'monthly_rent': float(tu_row[8]) if tu_row[8] else None,
                                    'security_deposit': float(tu_row[9]) if tu_row[9] else None,
                                    'is_active': bool(tu_row[10]) if tu_row[10] is not None else True,
                                    'rent_status': str(tu_row[11]) if tu_row[11] else 'active'
                                })
                            
                            tenant_dict['tenant_units'] = tenant_units_list
                        except Exception as tu_error:
                            # If tenant_units query fails, log but don't break
                            try:
                                from flask import current_app
                                current_app.logger.warning(f"Error getting tenant_units for tenant {tenant.id}: {str(tu_error)}")
                            except Exception:
                                pass
                            tenant_dict['tenant_units'] = []
                        
                        data['tenant'] = tenant_dict
                    else:
                        # Fallback: minimal tenant data
                        data['tenant'] = {
                            'id': tenant.id,
                            'user_id': getattr(tenant, 'user_id', None),
                            'property_id': getattr(tenant, 'property_id', None),
                            'user': None
                        }
                        if getattr(tenant, 'user', None):
                            user = tenant.user
                            data['tenant']['user'] = {
                                'id': user.id,
                                'first_name': getattr(user, 'first_name', None),
                                'last_name': getattr(user, 'last_name', None),
                                'email': getattr(user, 'email', None)
                            }
            except Exception as tenant_error:
                # Include minimal tenant data if serialization fails
                try:
                    from flask import current_app
                    current_app.logger.warning(f"Error including tenant info for bill {self.id}: {str(tenant_error)}")
                except Exception:
                    pass
                try:
                    data['tenant'] = {
                        'id': self.tenant_id,
                        'user_id': None,
                        'property_id': None,
                        'user': None
                    }
                except Exception:
                    pass
        
        if include_unit:
            try:
                # Use raw SQL to get unit name directly from database to avoid enum validation issues
                from sqlalchemy import text
                from app import db
                
                unit_id = self.unit_id
                if unit_id:
                    # Fetch unit name directly from database using raw SQL
                    # The database column is 'unit_name' (mapped to model attribute 'unit_number')
                    try:
                        unit_result = db.session.execute(text(
                            "SELECT id, unit_name, property_id FROM units WHERE id = :unit_id"
                        ), {'unit_id': unit_id}).first()
                    except Exception as query_error:
                        # If query fails, log and create minimal unit data
                        try:
                            from flask import current_app
                            current_app.logger.warning(f"Error querying unit {unit_id} for bill {self.id}: {str(query_error)}")
                        except Exception:
                            pass
                        unit_result = None
                    
                    if unit_result:
                        unit_id_from_db = unit_result[0]
                        # Get unit_name from database - handle NULL and empty strings
                        unit_name_raw = unit_result[1]
                        unit_name_from_db = None
                        
                        if unit_name_raw:
                            # Check if it's a non-empty string
                            if isinstance(unit_name_raw, str) and unit_name_raw.strip():
                                unit_name_from_db = unit_name_raw.strip()
                        
                        # If unit_name is NULL or empty in database, generate one from unit_id
                        if not unit_name_from_db:
                            unit_name_from_db = f"Unit {unit_id_from_db}"
                        
                        property_id_from_db = unit_result[2]
                        
                        # Get property name
                        property_name = f'Property {property_id_from_db}'
                        if property_id_from_db:
                            try:
                                prop_result = db.session.execute(text(
                                    "SELECT name, title, building_name FROM properties WHERE id = :prop_id"
                                ), {'prop_id': property_id_from_db}).first()
                                if prop_result:
                                    property_name = prop_result[0] or prop_result[1] or prop_result[2] or property_name
                            except Exception:
                                pass
                        
                        unit_dict = {
                            'id': unit_id_from_db,
                            'unit_number': unit_name_from_db,  # Map to unit_number for compatibility
                            'unit_name': unit_name_from_db,    # The actual unit name from database
                            'name': unit_name_from_db,          # Alias for compatibility
                            'property_id': property_id_from_db,
                            'property': {
                                'id': property_id_from_db,
                                'name': property_name,
                                'title': property_name,
                                'building_name': property_name
                            },
                            'property_obj': {
                                'id': property_id_from_db,
                                'name': property_name,
                                'title': property_name,
                                'building_name': property_name
                            }
                        }
                        data['unit'] = unit_dict
                        
                        # Log for debugging (only in development)
                        try:
                            from flask import current_app
                            if current_app.config.get('DEBUG', False):
                                current_app.logger.debug(f"Bill {self.id}: Unit {unit_id_from_db} - unit_name='{unit_name_from_db}'")
                        except Exception:
                            pass
                    else:
                        # Unit not found, create minimal data
                        data['unit'] = {
                            'id': unit_id,
                            'unit_number': None,
                            'unit_name': None,
                            'name': None,
                            'property_id': None
                        }
                else:
                    # No unit_id, create empty unit data
                    data['unit'] = {
                        'id': None,
                        'unit_number': None,
                        'unit_name': None,
                        'name': None,
                        'property_id': None
                    }
            except Exception as unit_error:
                # Include minimal unit data if serialization fails
                try:
                    from flask import current_app
                    current_app.logger.warning(f"Error including unit info for bill {self.id}: {str(unit_error)}")
                except Exception:
                    pass
                try:
                    data['unit'] = {
                        'id': self.unit_id,
                        'property_id': None,
                        'unit_number': None,
                        'unit_name': None
                    }
                except Exception:
                    pass
        
        return data
    
    def __repr__(self):
        return f'<Bill {self.bill_number}: {self.title}>'

class Payment(db.Model):
    __tablename__ = 'payments'
    
    id = db.Column(db.Integer, primary_key=True)
    bill_id = db.Column(db.Integer, db.ForeignKey('bills.id'), nullable=False)
    
    # Payment Details
    amount = db.Column(Numeric(10, 2), nullable=False)
    # Use String type instead of Enum to avoid validation issues with database enum values
    # Database enum: 'cash', 'gcash', 'check', 'bank_transfer', 'credit_card', 'debit_card', 'online', 'mobile'
    payment_method = db.Column(db.String(20), nullable=False)
    # Database enum: 'pending_approval', 'approved', 'rejected', 'completed', 'failed', 'refunded'
    status = db.Column(db.String(20), default='pending_approval', nullable=False)
    
    # Payment Information
    payment_date = db.Column(db.Date, nullable=False, default=date.today)
    reference_number = db.Column(db.String(100))  # GCash reference, transaction ID, etc.
    transaction_id = db.Column(db.String(100))
    confirmation_number = db.Column(db.String(100))
    
    # Proof of Payment (like subscription system)
    proof_of_payment = db.Column(db.Text)  # URL or base64 image of payment proof
    remarks = db.Column(db.Text)  # Tenant's remarks when submitting proof
    
    # Processing/Approval Information
    processed_by = db.Column(db.Integer, db.ForeignKey('users.id'))  # Who processed/approved
    verified_by = db.Column(db.Integer, db.ForeignKey('users.id'))  # Who verified the payment
    verified_at = db.Column(db.DateTime)  # When payment was verified/approved
    
    # Notes and Additional Info
    notes = db.Column(db.Text)
    receipt_url = db.Column(db.String(255))
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False)
    
    # Relationships
    processor = db.relationship('User', foreign_keys=[processed_by], backref='processed_payments')
    verifier = db.relationship('User', foreign_keys=[verified_by], backref='verified_payments')
    
    def __init__(self, bill_id, amount, payment_method, **kwargs):
        self.bill_id = bill_id
        self.amount = amount
        # Convert enum to string if needed
        if isinstance(payment_method, PaymentMethod):
            self.payment_method = payment_method.value
        elif isinstance(payment_method, str):
            self.payment_method = payment_method.lower()
        else:
            self.payment_method = str(payment_method).lower()
        
        # Handle status - convert enum to string if needed
        if 'status' in kwargs:
            status_val = kwargs['status']
            if isinstance(status_val, PaymentStatus):
                kwargs['status'] = status_val.value
            elif isinstance(status_val, str):
                kwargs['status'] = status_val.lower()
            else:
                kwargs['status'] = str(status_val).lower() if status_val else 'pending_approval'
        elif not hasattr(self, 'status') or not self.status:
            self.status = 'pending_approval'
        
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
    
    def mark_as_failed(self, reason=None):
        """Mark payment as failed."""
        self.status = 'failed'  # Use string literal
        if reason:
            self.notes = f"{self.notes}\nFailure reason: {reason}" if self.notes else f"Failure reason: {reason}"
        db.session.commit()
    
    def process_refund(self, refund_amount=None, reason=None):
        """Process refund for this payment."""
        refund_amount = refund_amount or self.amount
        self.status = 'refunded'  # Use string literal
        
        # Update bill status (amount_paid and amount_due are calculated properties)
        bill = self.bill
        bill.update_status()  # This will recalculate based on completed payments
        
        if reason:
            self.notes = f"{self.notes}\nRefund reason: {reason}" if self.notes else f"Refund reason: {reason}"
        
        db.session.commit()
    
    def to_dict(self, include_bill=False):
        """Convert payment to dictionary."""
        data = {
            'id': self.id,
            'bill_id': self.bill_id,
            'amount': float(self.amount),
            'payment_method': self.payment_method.value if hasattr(self.payment_method, 'value') else str(self.payment_method),
            'status': self.status.value if hasattr(self.status, 'value') else str(self.status),
            'payment_date': self.payment_date.isoformat(),
            'reference_number': self.reference_number,
            'transaction_id': self.transaction_id,
            'confirmation_number': self.confirmation_number,
            'proof_of_payment': self.proof_of_payment,  # Added proof of payment
            'remarks': self.remarks,  # Added remarks
            'processed_by': self.processed_by,
            'verified_by': self.verified_by,  # Added verified_by
            'verified_at': self.verified_at.isoformat() if self.verified_at else None,  # Added verified_at
            'notes': self.notes,
            'receipt_url': self.receipt_url,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }
        
        if include_bill and self.bill:
            data['bill'] = self.bill.to_dict()
        
        return data
    
    def __repr__(self):
        return f'<Payment {self.id}: ${self.amount} for Bill {self.bill_id}>'