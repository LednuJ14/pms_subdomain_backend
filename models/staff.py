from datetime import datetime, timezone
from app import db
from sqlalchemy import Numeric
import enum

class StaffRole(enum.Enum):
    MAINTENANCE = 'maintenance'
    SECURITY = 'security'
    CLEANING = 'cleaning'
    MANAGER = 'manager'
    ADMIN_ASSISTANT = 'admin_assistant'
    OTHER = 'other'

class EmploymentStatus(enum.Enum):
    ACTIVE = 'active'
    INACTIVE = 'inactive'
    ON_LEAVE = 'on_leave'
    TERMINATED = 'terminated'

class Staff(db.Model):
    __tablename__ = 'staff'
    
    # Only columns that exist in the database
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    employee_id = db.Column(db.String(20), unique=True, nullable=False)
    # Use String type instead of Enum to avoid validation issues with database enum values
    # Database enum: 'maintenance','security','cleaning','manager','admin_assistant','other'
    staff_role = db.Column(db.String(20), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=False, index=True)  # Staff belongs to specific property
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False)
    
    # Relationships
    # Define user relationship with back_populates to match User model (which now uses back_populates)
    user = db.relationship('User', back_populates='staff_profile')
    property_obj = db.relationship('Property', foreign_keys=[property_id], backref='staff_members')
    
    def __init__(self, user_id, employee_id, staff_role, property_id, **kwargs):
        self.user_id = user_id
        self.employee_id = employee_id
        self.property_id = property_id
        # Convert enum to string if needed
        if hasattr(staff_role, 'value'):
            self.staff_role = staff_role.value
        else:
            self.staff_role = str(staff_role)
        
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
    
    # Compatibility properties for backward compatibility (columns don't exist in DB)
    @property
    def is_active(self):
        """Always return True since employment_status doesn't exist in database."""
        return True
    
    @property
    def job_title(self):
        """Return staff_role as job_title for backward compatibility."""
        return self.staff_role.title() if self.staff_role else ''
    
    @property
    def department(self):
        """Return None since department doesn't exist in database."""
        return None
    
    @property
    def hire_date(self):
        """Return created_at as hire_date for backward compatibility."""
        return self.created_at.date() if self.created_at else None
    
    @property
    def employment_status(self):
        """Return ACTIVE enum for backward compatibility."""
        return EmploymentStatus.ACTIVE
    
    @property
    def years_of_service(self):
        """Calculate years of service from created_at."""
        if self.created_at:
            delta = datetime.now().date() - self.created_at.date()
            return round(delta.days / 365.25, 1)
        return 0
    
    @property
    def pending_tasks_count(self):
        """Get count of pending tasks."""
        try:
            from models.task import Task, TaskStatus
            return Task.query.filter_by(
                assigned_to=self.id,
                status=TaskStatus.PENDING
            ).count()
        except Exception:
            return 0
    
    @property
    def completed_tasks_count(self):
        """Get count of completed tasks."""
        try:
            from models.task import Task, TaskStatus
            return Task.query.filter_by(
                assigned_to=self.id,
                status=TaskStatus.COMPLETED
            ).count()
        except Exception:
            return 0
    
    def to_dict(self, include_user=False, include_supervisor=False):
        """Convert staff to dictionary - only includes columns that exist in database."""
        # Get staff_role as string (it's stored as string in DB)
        staff_role_str = self.staff_role.value if hasattr(self.staff_role, 'value') else str(self.staff_role)
        
        data = {
            'id': self.id,
            'user_id': self.user_id,
            'employee_id': self.employee_id,
            'staff_role': staff_role_str,
            'property_id': self.property_id,
            # Backward compatibility fields (using properties)
            'job_title': self.job_title,
            'department': self.department,
            'hire_date': self.hire_date.isoformat() if self.hire_date else None,
            'employment_status': self.employment_status.value if hasattr(self.employment_status, 'value') else 'active',
            'years_of_service': self.years_of_service,
            'pending_tasks_count': self.pending_tasks_count,
            'completed_tasks_count': self.completed_tasks_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            # Default values for fields that don't exist in database (for frontend compatibility)
            'position': self.job_title,
            'salary': 0,
            'status': 'Active',
            'working_hours': '40/week',
            'supervisor': 'N/A',
            'hourly_rate': None,
            'monthly_salary': None,
            'employment_type': 'full_time'
        }
        
        if include_user and self.user:
            try:
                data['user'] = self.user.to_dict()
            except Exception:
                # Fallback if user serialization fails
                data['user'] = {
                    'id': self.user.id,
                    'email': getattr(self.user, 'email', ''),
                    'first_name': getattr(self.user, 'first_name', ''),
                    'last_name': getattr(self.user, 'last_name', ''),
                    'username': getattr(self.user, 'username', ''),
                    'phone_number': getattr(self.user, 'phone_number', '')
                }
        
        return data
    
    def __repr__(self):
        return f'<Staff {self.employee_id}: {self.user.full_name if self.user else self.id}>'