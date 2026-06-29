from datetime import datetime, timezone, date
from app import db
from sqlalchemy import Numeric
import enum

class RentStatus(enum.Enum):
    ACTIVE = 'active'
    EXPIRED = 'expired'
    TERMINATED = 'terminated'
    PENDING = 'pending'

class Tenant(db.Model):
    __tablename__ = 'tenants'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=False)
    
    # Contact Information (simplified schema)
    phone_number = db.Column(db.String(20))
    email = db.Column(db.String(120))
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False)
    
    # Relationships (defined here to avoid conflicts)
    # Define user relationship with back_populates to match User model (which now uses back_populates)
    user = db.relationship('User', back_populates='tenant_profile')
    # Use property_obj to avoid conflict with Python's built-in property
    property_obj = db.relationship('Property', foreign_keys=[property_id], backref='tenants')
    tenant_units = db.relationship('TenantUnit', backref='tenant', cascade='all, delete-orphan')
    bills = db.relationship('Bill', back_populates='tenant', cascade='all, delete-orphan')
    maintenance_requests = db.relationship('MaintenanceRequest', backref='tenant', cascade='all, delete-orphan')
    
    # Compatibility properties for backward compatibility (return None for fields that don't exist)
    @property
    def occupation(self):
        return None
    
    @property
    def employer(self):
        return None
    
    @property
    def monthly_income(self):
        return None
    
    @property
    def assigned_room(self):
        """Get room number from tenant_units if available."""
        try:
            current_rent = self.current_rent
            if current_rent and current_rent.unit:
                return current_rent.unit.unit_number
        except Exception:
            pass
        return None
    
    @property
    def room_number(self):
        """Alias for assigned_room."""
        return self.assigned_room
    
    @property
    def is_approved(self):
        """Tenant is approved if they have an active rental."""
        try:
            return self.current_rent is not None
        except Exception:
            return False
    
    @property
    def has_pets(self):
        return False
    
    @property
    def pet_details(self):
        return None
    
    @property
    def has_vehicle(self):
        return False
    
    @property
    def vehicle_details(self):
        return None
    
    @property
    def background_check_status(self):
        return 'approved' if self.is_approved else 'pending'
    
    @property
    def credit_score(self):
        return None
    
    def __init__(self, user_id, **kwargs):
        self.user_id = user_id
        
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
    
    @property
    def current_unit(self):
        """Get tenant's current unit."""
        try:
            from datetime import date
            from sqlalchemy import text
            # Check for active tenant_unit using new structure (move_in_date/move_out_date)
            active_tenant_unit = db.session.execute(text(
                """
                SELECT tu.unit_id FROM tenant_units tu
                WHERE tu.tenant_id = :tenant_id 
                  AND (
                    (tu.move_in_date IS NOT NULL AND tu.move_out_date IS NOT NULL 
                     AND tu.move_out_date >= CURDATE())
                    OR
                    (tu.is_active = TRUE)
                  )
                LIMIT 1
                """
            ), {'tenant_id': self.id}).first()
            
            if active_tenant_unit:
                from models.property import Unit
                return Unit.query.get(active_tenant_unit[0])
        except Exception:
            pass
        return None
    
    @property
    def current_rent(self):
        """Get tenant's current active rental."""
        try:
            from datetime import date, datetime
            from sqlalchemy import text
            
            # First, try to get the most recent tenant_unit for this tenant
            # This handles cases where is_active column might not exist or dates might be in the past
            # We'll get the most recent one and check if it's still valid
            
            # Try multiple query strategies to handle different database schemas
            queries = [
                # Strategy 1: Check for active rental with is_active column (if it exists)
                """
                SELECT tu.id FROM tenant_units tu
                WHERE tu.tenant_id = :tenant_id 
                  AND (
                    (tu.move_out_date IS NULL)
                    OR
                    (tu.move_out_date >= CURDATE())
                    OR
                    (tu.is_active = TRUE)
                  )
                ORDER BY tu.created_at DESC
                LIMIT 1
                """,
                # Strategy 2: Check without is_active (in case column doesn't exist)
                """
                SELECT tu.id FROM tenant_units tu
                WHERE tu.tenant_id = :tenant_id 
                  AND (
                    (tu.move_out_date IS NULL)
                    OR
                    (tu.move_out_date >= CURDATE())
                  )
                ORDER BY tu.created_at DESC
                LIMIT 1
                """,
                # Strategy 3: Just get the most recent one (fallback)
                """
                SELECT tu.id FROM tenant_units tu
                WHERE tu.tenant_id = :tenant_id
                ORDER BY tu.created_at DESC
                LIMIT 1
                """
            ]
            
            active_tenant_unit_id = None
            for query_str in queries:
                try:
                    result = db.session.execute(text(query_str), {'tenant_id': self.id}).first()
                    if result:
                        active_tenant_unit_id = result
                        break
                except Exception as query_error:
                    # If query fails (e.g., column doesn't exist), try next strategy
                    continue
            
            if active_tenant_unit_id:
                # Get the tenant unit and ensure unit relationship is loaded
                tenant_unit = TenantUnit.query.get(active_tenant_unit_id[0])
                if tenant_unit:
                    if tenant_unit.unit_id and not tenant_unit.unit:
                        # Force load the unit if relationship not loaded
                        from models.property import Unit
                        tenant_unit.unit = Unit.query.get(tenant_unit.unit_id)
                    return tenant_unit
        except Exception as e:
            # Log error for debugging
            try:
                from flask import current_app
                current_app.logger.warning(f"Error getting current_rent for tenant {self.id}: {str(e)}")
            except:
                print(f"Error getting current_rent for tenant {self.id}: {str(e)}")
        return None
    
    @property
    def rent_history(self):
        """Get tenant's rental history."""
        try:
            return TenantUnit.query.filter_by(tenant_id=self.id).all()
        except Exception:
            return []
    
    @property
    def total_rent_paid(self):
        """Calculate total rent paid by tenant."""
        from models.bill import Payment, Bill, BillStatus
        total = db.session.query(db.func.sum(Payment.amount)).join(
            Bill, Payment.bill_id == Bill.id
        ).filter(
            Bill.tenant_id == self.id,
            Payment.status == 'completed'
        ).scalar()
        return float(total) if total else 0.0
    
    @property
    def outstanding_balance(self):
        """Calculate tenant's outstanding balance."""
        from models.bill import Bill, BillStatus
        # Calculate sum of amount_due for pending/overdue bills
        # amount_due is a property, so we need to calculate it manually
        bills = Bill.query.filter(
            Bill.tenant_id == self.id,
            Bill.status.in_([BillStatus.PENDING, BillStatus.OVERDUE])
        ).all()
        total = sum(float(bill.amount_due) for bill in bills)
        return total
    
    def approve_tenant(self):
        """Approve tenant application."""
        self.is_approved = True
        self.approval_date = datetime.now(timezone.utc)
        self.background_check_status = 'approved'
        db.session.commit()
    
    def reject_tenant(self):
        """Reject tenant application."""
        self.is_approved = False
        self.background_check_status = 'rejected'
        db.session.commit()
    
    def to_dict(self, include_user=False, include_rent=False):
        """Convert tenant to dictionary (simplified schema)."""
        data = {
            'id': self.id,
            'user_id': self.user_id,
            'property_id': self.property_id,
            'phone_number': self.phone_number,
            'email': self.email,
            'assigned_room': self.assigned_room,
            'room_number': self.room_number,  # Alias
            'is_approved': self.is_approved,
            'status': 'Active' if self.is_approved else 'Pending',
            'background_check_status': self.background_check_status,
            'total_rent_paid': self.total_rent_paid,
            'outstanding_balance': self.outstanding_balance,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
        
        # Include property info if available
        if hasattr(self, 'property_obj') and self.property_obj:
            data['property'] = {
                'id': self.property_obj.id,
                'name': getattr(self.property_obj, 'name', None) or getattr(self.property_obj, 'title', None) or getattr(self.property_obj, 'building_name', None)
            }
        elif self.property_id:
            data['property'] = {
                'id': self.property_id,
                'name': f'Property {self.property_id}'
            }
        
        if include_user and self.user:
            data['user'] = self.user.to_dict()
        
        if include_rent:
            # COMPREHENSIVE APPROACH: Always try to get rent data, with multiple fallbacks
            rent_dict = None
            
            try:
                from sqlalchemy import text
                from models.property import Unit
                
                # STEP 1: Direct database query - most reliable
                try:
                    # Get tenant_unit record directly from database
                    tu_result = db.session.execute(
                        text("""
                            SELECT tu.id, tu.unit_id, tu.move_in_date, tu.move_out_date, 
                                   tu.monthly_rent, tu.security_deposit, tu.created_at, tu.updated_at
                            FROM tenant_units tu
                            WHERE tu.tenant_id = :tenant_id
                            ORDER BY tu.created_at DESC
                            LIMIT 1
                        """),
                        {'tenant_id': self.id}
                    ).first()
                    
                    if tu_result:
                        tenant_unit_id = tu_result[0]
                        unit_id = tu_result[1]
                        
                        # STEP 2: Load the unit
                        unit_dict = None
                        if unit_id:
                            try:
                                unit = Unit.query.get(unit_id)
                                if unit:
                                    unit_dict = unit.to_dict()
                            except Exception as unit_err:
                                # If Unit model fails, try raw SQL
                                try:
                                    unit_data = db.session.execute(
                                        text("SELECT id, unit_name, unit_number FROM units WHERE id = :unit_id"),
                                        {'unit_id': unit_id}
                                    ).first()
                                    if unit_data:
                                        unit_dict = {
                                            'id': unit_data[0],
                                            'unit_name': unit_data[1] or unit_data[2],
                                            'unit_number': unit_data[2] or unit_data[1],
                                            'name': unit_data[1] or unit_data[2]
                                        }
                                except:
                                    pass
                        
                        # STEP 3: Build rent_dict directly from database data
                        rent_dict = {
                            'id': tenant_unit_id,
                            'tenant_id': self.id,
                            'unit_id': unit_id,
                            'move_in_date': tu_result[2].isoformat() if tu_result[2] else None,
                            'move_out_date': tu_result[3].isoformat() if tu_result[3] else None,
                            'monthly_rent': float(tu_result[4]) if tu_result[4] else None,
                            'security_deposit': float(tu_result[5]) if tu_result[5] else None,
                            'created_at': tu_result[6].isoformat() if tu_result[6] else None,
                            'updated_at': tu_result[7].isoformat() if tu_result[7] else None,
                        }
                        
                        # Always include unit if we have it
                        if unit_dict:
                            rent_dict['unit'] = unit_dict
                        elif unit_id:
                            # Last resort: try to get unit name directly
                            try:
                                unit_name_result = db.session.execute(
                                    text("SELECT unit_name FROM units WHERE id = :unit_id"),
                                    {'unit_id': unit_id}
                                ).first()
                                if unit_name_result and unit_name_result[0]:
                                    rent_dict['unit'] = {
                                        'id': unit_id,
                                        'unit_name': unit_name_result[0],
                                        'unit_number': unit_name_result[0],
                                        'name': unit_name_result[0]
                                    }
                            except:
                                pass
                
                except Exception as direct_query_error:
                    # FALLBACK: Try using TenantUnit model
                    try:
                        rent_obj = TenantUnit.query.filter_by(tenant_id=self.id).order_by(TenantUnit.created_at.desc()).first()
                        if rent_obj:
                            # Try to get unit
                            unit_obj = None
                            if rent_obj.unit_id:
                                try:
                                    unit_obj = Unit.query.get(rent_obj.unit_id)
                                except:
                                    pass
                            
                            # Serialize
                            rent_dict = rent_obj.to_dict(include_unit=True) if hasattr(rent_obj, 'to_dict') else {}
                            
                            # Ensure unit is included
                            if not rent_dict.get('unit') and unit_obj:
                                rent_dict['unit'] = unit_obj.to_dict()
                            elif not rent_dict.get('unit') and rent_obj.unit_id:
                                # Last resort for unit
                                try:
                                    unit_name_result = db.session.execute(
                                        text("SELECT unit_name FROM units WHERE id = :unit_id"),
                                        {'unit_id': rent_obj.unit_id}
                                    ).first()
                                    if unit_name_result and unit_name_result[0]:
                                        rent_dict['unit'] = {
                                            'id': rent_obj.unit_id,
                                            'unit_name': unit_name_result[0],
                                            'unit_number': unit_name_result[0],
                                            'name': unit_name_result[0]
                                        }
                                except:
                                    pass
                    except Exception as model_error:
                        # If all else fails, log but don't crash
                        try:
                            from flask import current_app
                            current_app.logger.warning(f"Tenant {self.id}: All rent query methods failed. Direct: {str(direct_query_error)}, Model: {str(model_error)}")
                        except:
                            pass
                
                # STEP 4: Always set current_rent if we have data
                if rent_dict:
                    data['current_rent'] = rent_dict
                    try:
                        from flask import current_app
                        unit_name = 'Unknown'
                        if rent_dict.get('unit'):
                            unit_name = (rent_dict['unit'].get('unit_name') or 
                                       rent_dict['unit'].get('unit_number') or 
                                       rent_dict['unit'].get('name') or 
                                       'Unknown')
                        current_app.logger.info(f"✓ Tenant {self.id}: Successfully included current_rent with unit_id={rent_dict.get('unit_id')}, unit_name={unit_name}")
                    except:
                        pass
                else:
                    try:
                        from flask import current_app
                        current_app.logger.warning(f"✗ Tenant {self.id}: No rent data found after all attempts")
                    except:
                        pass
                        
            except Exception as final_error:
                # Last resort error handling - log but don't crash
                try:
                    from flask import current_app
                    import traceback
                    current_app.logger.error(f"CRITICAL: Error in include_rent for tenant {self.id}: {str(final_error)}\n{traceback.format_exc()}")
                except:
                    import traceback
                    print(f"CRITICAL: Error in include_rent for tenant {self.id}: {str(final_error)}\n{traceback.format_exc()}")
                # Don't set current_rent if there was a critical error
                pass
        
        return data
    
    def __repr__(self):
        return f'<Tenant {self.user.full_name if self.user else self.id}>'

class TenantUnit(db.Model):
    __tablename__ = 'tenant_units'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey('units.id'), nullable=False)
    
    # Rent Information
    # Note: Database may only have move_in_date/move_out_date, so make these nullable
    rent_start_date = db.Column(db.Date, nullable=True)  # Made nullable to match actual DB (alias for move_in_date)
    rent_end_date = db.Column(db.Date, nullable=True)  # Made nullable to match actual DB (alias for move_out_date)
    monthly_rent = db.Column(Numeric(10, 2), nullable=True)  # Made nullable to match actual DB
    security_deposit = db.Column(Numeric(10, 2), nullable=True)  # Made nullable to match actual DB
    
    # Status - these columns may not exist in actual database
    rent_status = db.Column(db.Enum(RentStatus), default=RentStatus.ACTIVE, nullable=True)  # Made nullable
    is_active = db.Column(db.Boolean, default=True, nullable=True)  # Made nullable - may not exist in DB
    
    # Move-in/Move-out Information - these match the actual database schema
    move_in_date = db.Column(db.Date, nullable=True)
    move_out_date = db.Column(db.Date, nullable=True)
    move_in_inspection_notes = db.Column(db.Text)
    move_out_inspection_notes = db.Column(db.Text)
    
    # Financial Information
    deposit_returned = db.Column(db.Boolean, default=False)
    deposit_return_amount = db.Column(Numeric(10, 2))
    deposit_return_date = db.Column(db.Date)
    
    # Renewal Information
    renewal_offered = db.Column(db.Boolean, default=False)
    renewal_accepted = db.Column(db.Boolean, default=False)
    renewal_date = db.Column(db.DateTime)
    
    # Notice and Termination
    notice_given = db.Column(db.Boolean, default=False)
    notice_date = db.Column(db.Date)
    termination_reason = db.Column(db.String(255))
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False)
    
    # Relationships
    # Use back_populates to match Unit model's relationship definition
    unit = db.relationship('Unit', foreign_keys=[unit_id], back_populates='tenant_units')
    
    # Unique constraint - one active lease per unit
    __table_args__ = (
        db.Index('idx_unit_active', 'unit_id', 'is_active'),
    )
    
    def __init__(self, tenant_id, unit_id, rent_start_date=None, rent_end_date=None, monthly_rent=None, security_deposit=None, **kwargs):
        self.tenant_id = tenant_id
        self.unit_id = unit_id
        # Map move_in_date/move_out_date to rent dates if provided
        if rent_start_date:
            self.rent_start_date = rent_start_date
        if rent_end_date:
            self.rent_end_date = rent_end_date
        if monthly_rent is not None:
            self.monthly_rent = monthly_rent
        if security_deposit is not None:
            self.security_deposit = security_deposit
        
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
    
    def __repr__(self):
        return f'<TenantUnit Tenant:{self.tenant_id} Unit:{self.unit_id}>'
    
    @property
    def is_expired(self):
        """Check if rental is expired."""
        try:
            rent_end = self.rent_end_date or self.move_out_date
            if rent_end:
                return rent_end < date.today()
        except:
            pass
        return False
    
    @property
    def days_until_expiry(self):
        """Get days until rental expiry."""
        try:
            rent_end = self.rent_end_date or self.move_out_date
            if rent_end:
                delta = rent_end - date.today()
                return delta.days
        except:
            pass
        return None
    
    @property
    def rent_duration_months(self):
        """Get rental duration in months."""
        try:
            rent_start = self.rent_start_date or self.move_in_date
            rent_end = self.rent_end_date or self.move_out_date
            if rent_start and rent_end:
                delta = rent_end - rent_start
                return round(delta.days / 30.44)  # Average days per month
        except:
            pass
        return None
    
    def terminate_rent(self, termination_reason=None):
        """Terminate the rental."""
        if hasattr(self, 'rent_status'):
            self.rent_status = RentStatus.TERMINATED
        if hasattr(self, 'is_active'):
            self.is_active = False
        self.move_out_date = date.today()
        if termination_reason and hasattr(self, 'termination_reason'):
            self.termination_reason = termination_reason
        
        # Update unit status to available
        if self.unit:
            from models.property import UnitStatus
            self.unit.status = 'available'  # Use string value since status is String type
        
        db.session.commit()
    
    def renew_rent(self, new_end_date, new_monthly_rent=None, extend_rent_dates=True):
        """
        Renew the rental.
        
        Args:
            new_end_date: New end date for the rental period
            new_monthly_rent: Optional new monthly rent amount
            extend_rent_dates: If True, extends both rent_end_date and move_out_date.
                             If False, only extends move_out_date (tenant stays longer physically but rent period doesn't change)
        """
        # Update move_out_date (physical move-out date)
        self.move_out_date = new_end_date
        
        # Update rent_end_date (rental billing period end date)
        # If extend_rent_dates is True, extend the rent period to match move-out
        # If False, keep rent_end_date as is (rent period ends earlier than physical move-out)
        if extend_rent_dates:
            if hasattr(self, 'rent_end_date'):
                self.rent_end_date = new_end_date
            # If rent_end_date column doesn't exist, it will use move_out_date as fallback
        
        if new_monthly_rent:
            self.monthly_rent = new_monthly_rent
        
        # Mark renewal as accepted
        if hasattr(self, 'renewal_accepted'):
            self.renewal_accepted = True
        if hasattr(self, 'renewal_date'):
            self.renewal_date = datetime.now(timezone.utc)
        if hasattr(self, 'rent_status'):
            self.rent_status = RentStatus.ACTIVE
        
        db.session.commit()
    
    def to_dict(self, include_tenant=False, include_unit=False):
        """Convert tenant unit relationship to dictionary."""
        # Handle nullable fields that may not exist in database
        rent_start = self.rent_start_date or self.move_in_date
        rent_end = self.rent_end_date or self.move_out_date
        
        # Safely get properties that might fail
        try:
            is_expired_val = self.is_expired
        except:
            is_expired_val = False
        
        try:
            days_until_expiry_val = self.days_until_expiry
        except:
            days_until_expiry_val = None
        
        try:
            rent_duration_months_val = self.rent_duration_months
        except:
            rent_duration_months_val = None
        
        data = {
            'id': self.id,
            'tenant_id': self.tenant_id,
            'unit_id': self.unit_id,
            'rent_start_date': rent_start.isoformat() if rent_start else None,
            'rent_end_date': rent_end.isoformat() if rent_end else None,
            'monthly_rent': float(self.monthly_rent) if self.monthly_rent else None,
            'security_deposit': float(self.security_deposit) if self.security_deposit else None,
            'rent_status': self.rent_status.value if hasattr(self, 'rent_status') and self.rent_status else 'active',
            'is_active': self.is_active if self.is_active is not None else True,
            'is_expired': is_expired_val,
            'days_until_expiry': days_until_expiry_val,
            'rent_duration_months': rent_duration_months_val,
            'move_in_date': self.move_in_date.isoformat() if self.move_in_date else None,
            'move_out_date': self.move_out_date.isoformat() if self.move_out_date else None,
            'move_in_inspection_notes': getattr(self, 'move_in_inspection_notes', None),
            'move_out_inspection_notes': getattr(self, 'move_out_inspection_notes', None),
            'deposit_returned': getattr(self, 'deposit_returned', False),
            'deposit_return_amount': float(self.deposit_return_amount) if getattr(self, 'deposit_return_amount', None) else None,
            'deposit_return_date': self.deposit_return_date.isoformat() if getattr(self, 'deposit_return_date', None) else None,
            'renewal_offered': getattr(self, 'renewal_offered', False),
            'renewal_accepted': getattr(self, 'renewal_accepted', False),
            'renewal_date': self.renewal_date.isoformat() if getattr(self, 'renewal_date', None) else None,
            'notice_given': getattr(self, 'notice_given', False),
            'notice_date': self.notice_date.isoformat() if getattr(self, 'notice_date', None) else None,
            'termination_reason': getattr(self, 'termination_reason', None),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
        
        if include_tenant and self.tenant:
            data['tenant'] = self.tenant.to_dict(include_user=True)
        
        if include_unit:
            # Always try to load unit if we have unit_id
            if self.unit_id:
                if self.unit:
                    # Unit relationship is already loaded
                    data['unit'] = self.unit.to_dict()
                else:
                    # Fallback: load unit if relationship not loaded
                    try:
                        from models.property import Unit
                        unit = Unit.query.get(self.unit_id)
                        if unit:
                            data['unit'] = unit.to_dict()
                            # Also set it on the object for future use
                            self.unit = unit
                    except Exception as e:
                        # Log warning if logger available
                        try:
                            from flask import current_app
                            current_app.logger.warning(f"Failed to load unit {self.unit_id} for tenant_unit {self.id}: {str(e)}")
                        except:
                            print(f"Failed to load unit {self.unit_id} for tenant_unit {self.id}: {str(e)}")
        
        return data
    
    def __repr__(self):
        return f'<TenantUnit Tenant:{self.tenant_id} Unit:{self.unit_id}>'