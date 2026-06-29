from datetime import datetime, timezone
from app import db
from sqlalchemy import Numeric
import enum

class RequestStatus(enum.Enum):
    PENDING = 'pending'
    IN_PROGRESS = 'in_progress'
    COMPLETED = 'completed'
    CANCELLED = 'cancelled'
    ON_HOLD = 'on_hold'

class RequestPriority(enum.Enum):
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'
    URGENT = 'urgent'

class RequestCategory(enum.Enum):
    PLUMBING = 'plumbing'
    ELECTRICAL = 'electrical'
    HVAC = 'hvac'
    APPLIANCE = 'appliance'
    CARPENTRY = 'carpentry'
    PAINTING = 'painting'
    CLEANING = 'cleaning'
    PEST_CONTROL = 'pest_control'
    SECURITY = 'security'
    OTHER = 'other'

class MaintenanceRequest(db.Model):
    __tablename__ = 'maintenance_requests'
    
    id = db.Column(db.Integer, primary_key=True)
    request_number = db.Column(db.String(50), unique=True, nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey('units.id'), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=False)
    
    # Request Details
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    # Use String type instead of Enum to avoid validation issues with database enum values
    # Database enum: 'plumbing', 'electrical', 'hvac', 'appliance', 'carpentry', 'painting', 'cleaning', 'pest_control', 'security', 'other'
    category = db.Column(db.String(20), nullable=False)
    # Database enum: 'low', 'medium', 'high', 'urgent'
    priority = db.Column(db.String(20), default='medium', nullable=False)
    # Database enum: 'pending', 'in_progress', 'completed', 'cancelled', 'on_hold'
    status = db.Column(db.String(20), default='pending', nullable=False)
    
    # Assignment and Scheduling
    assigned_to = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=True)
    scheduled_date = db.Column(db.DateTime)
    estimated_completion = db.Column(db.DateTime)
    actual_completion = db.Column(db.DateTime)
    
    # Progress and Updates (removed fields that don't exist in database schema)
    work_notes = db.Column(db.Text)
    resolution_notes = db.Column(db.Text)
    tenant_satisfaction_rating = db.Column(db.Integer)  # 1-5 stars
    tenant_feedback = db.Column(db.Text)
    
    # Images and Attachments
    images = db.Column(db.Text)  # Store as longtext (can be JSON string or base64)
    attachments = db.Column(db.Text)  # Store as longtext (can be JSON string or base64)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False)
    
    # Relationships
    unit = db.relationship('Unit', backref='unit_maintenance_requests')
    property_ref = db.relationship('Property', backref='property_maintenance_requests')
    assigned_staff = db.relationship('Staff', backref='assigned_maintenance_requests', foreign_keys=[assigned_to])
    
    def __init__(self, request_number, tenant_id, unit_id, property_id, title, description, category, **kwargs):
        self.request_number = request_number
        self.tenant_id = tenant_id
        self.unit_id = unit_id
        self.property_id = property_id
        self.title = title.strip()
        self.description = description.strip()
        self.category = category
        
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
    
    def assign_staff(self, staff_id, scheduled_date=None):
        """Assign staff to this request."""
        self.assigned_to = staff_id
        self.status = 'in_progress'  # Use string value directly
        if scheduled_date:
            self.scheduled_date = scheduled_date
        db.session.commit()
    
    def mark_completed(self, resolution_notes=None):
        """Mark request as completed."""
        self.status = 'completed'  # Use string value directly
        self.actual_completion = datetime.now(timezone.utc)
        if resolution_notes:
            self.resolution_notes = resolution_notes
        db.session.commit()
    
    def cancel_request(self, reason=None):
        """Cancel the request."""
        self.status = 'cancelled'  # Use string value directly
        if reason:
            self.work_notes = f"{self.work_notes}\nCancellation reason: {reason}" if self.work_notes else f"Cancellation reason: {reason}"
        db.session.commit()
    
    def add_tenant_feedback(self, rating, feedback=None):
        """Add tenant satisfaction feedback."""
        self.tenant_satisfaction_rating = rating
        if feedback:
            self.tenant_feedback = feedback
        db.session.commit()
    
    @property
    def is_overdue(self):
        """Check if request is overdue."""
        status_str = str(self.status).lower()
        if self.scheduled_date and status_str not in ['completed', 'cancelled']:
            now = datetime.now(timezone.utc)
            scheduled = self.scheduled_date
            # Ensure both are timezone-aware
            if scheduled.tzinfo is None:
                # If scheduled_date is naive, assume UTC
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            return now > scheduled
        return False
    
    @property
    def days_since_created(self):
        """Get days since request was created."""
        if not self.created_at:
            return 0
        now = datetime.now(timezone.utc)
        created = self.created_at
        # Ensure both are timezone-aware
        if created.tzinfo is None:
            # If created_at is naive, assume UTC
            created = created.replace(tzinfo=timezone.utc)
        return (now - created).days
    
    def to_dict(self, include_tenant=False, include_unit=False, include_assigned_staff=False):
        """Convert maintenance request to dictionary."""
        data = {
            'id': self.id,
            'request_number': self.request_number,
            'tenant_id': self.tenant_id,
            'unit_id': self.unit_id,
            'property_id': self.property_id,
            'title': self.title,
            'description': self.description,
            'category': str(self.category),
            'priority': str(self.priority),
            'status': str(self.status),
            'assigned_to': self.assigned_to,
            'scheduled_date': self.scheduled_date.isoformat() if self.scheduled_date else None,
            'estimated_completion': self.estimated_completion.isoformat() if self.estimated_completion else None,
            'actual_completion': self.actual_completion.isoformat() if self.actual_completion else None,
            'work_notes': self.work_notes,
            'resolution_notes': self.resolution_notes,
            'tenant_satisfaction_rating': self.tenant_satisfaction_rating,
            'tenant_feedback': self.tenant_feedback,
            'images': self.images if self.images else None,  # Store as text (longtext)
            'attachments': self.attachments if self.attachments else None,  # Store as text (longtext)
            'is_overdue': self.is_overdue,
            'days_since_created': self.days_since_created,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }
        
        if include_tenant and self.tenant:
            data['tenant'] = self.tenant.to_dict(include_user=True)
        
        if include_unit:
            try:
                from sqlalchemy import text
                # Fetch unit info via raw SQL to avoid enum validation issues
                unit_row = db.session.execute(text(
                    "SELECT id, unit_name, property_id FROM units WHERE id = :uid"
                ), {'uid': self.unit_id}).first()
                if unit_row:
                    unit_name = unit_row[1] or f"Unit {unit_row[0]}"
                    property_id = unit_row[2]
                    data['unit'] = {
                        'id': unit_row[0],
                        'unit_number': unit_name,
                        'unit_name': unit_name,
                        'name': unit_name,
                        'property_id': property_id,
                        'property': {
                            'id': property_id
                        }
                    }
                else:
                    data['unit'] = {
                        'id': self.unit_id,
                        'unit_number': None,
                        'unit_name': None,
                        'name': None,
                        'property_id': None
                    }
            except Exception:
                # Fallback to minimal data without invoking ORM enums
                data['unit'] = {
                    'id': self.unit_id,
                    'unit_number': None,
                    'unit_name': None,
                    'name': None,
                    'property_id': None
                }
        
        if include_assigned_staff and self.assigned_staff:
            data['assigned_staff'] = self.assigned_staff.to_dict(include_user=True)
        
        return data
    
    def __repr__(self):
        return f'<MaintenanceRequest {self.request_number}: {self.title}>'