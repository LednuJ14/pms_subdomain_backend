from datetime import datetime, timezone
from app import db
import bcrypt
import enum

class UserRole(enum.Enum):
    # Note: ADMIN removed from subdomain - only property managers, staff, and tenants
    MANAGER = 'MANAGER'  # Maps to property_manager in code
    PROPERTY_MANAGER = 'MANAGER'  # Alias for MANAGER
    STAFF = 'STAFF'
    TENANT = 'TENANT'
    
    # Helper methods for backward compatibility
    @classmethod
    def from_string(cls, value):
        """Convert string to UserRole, handling both old and new formats."""
        if not value:
            return cls.TENANT
        value_upper = value.upper()
        # Map old format to new format
        if value_upper == 'PROPERTY_MANAGER':
            return cls.MANAGER
        # ADMIN is not supported in subdomain - treat as MANAGER for backward compatibility
        if value_upper == 'ADMIN':
            return cls.MANAGER  # Map ADMIN to MANAGER for backward compatibility
        if value_upper in ['MANAGER', 'STAFF', 'TENANT']:
            return cls[value_upper]
        return cls.TENANT

class UserStatus(enum.Enum):
    """User status enumeration."""
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING_VERIFICATION = "pending_verification"

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    username = db.Column(db.String(80), unique=True, nullable=True, index=True)  # Changed to nullable=True
    password_hash = db.Column(db.String(255), nullable=False)
    
    # Personal Information
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    phone_number = db.Column(db.String(20))
    date_of_birth = db.Column(db.Date)
    
    # Role and Status - Match database enum values exactly
    # Note: Database may still have ADMIN in enum, but subdomain doesn't use it
    # Database has: enum('ADMIN','MANAGER','TENANT','STAFF')
    role = db.Column(db.Enum('ADMIN', 'MANAGER', 'TENANT', 'STAFF', name='role', create_constraint=False), nullable=False, default='TENANT')
    status = db.Column(db.Enum(UserStatus), nullable=False, default=UserStatus.PENDING_VERIFICATION)
    # Database has email_verified, not is_verified
    email_verified = db.Column(db.Boolean, default=False, nullable=True)
    
    # Timestamps - Database allows NULL
    created_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=True)
    last_login = db.Column(db.DateTime, nullable=True)
    
    # Password Reset - Match database column names exactly
    password_reset_token = db.Column(db.String(255), nullable=True)  # Database column name
    password_reset_expires = db.Column(db.DateTime, nullable=True)  # Database column name
    
    # Backward compatibility properties
    @property
    def is_verified(self):
        """Map email_verified to is_verified for backward compatibility."""
        return getattr(self, 'email_verified', False) or False
    
    @property
    def reset_token(self):
        """Map password_reset_token to reset_token for backward compatibility."""
        return getattr(self, 'password_reset_token', None)
    
    @property
    def reset_token_expiry(self):
        """Map password_reset_expires to reset_token_expiry for backward compatibility."""
        return getattr(self, 'password_reset_expires', None)
    
    # Profile Information
    profile_image_url = db.Column(db.String(255))
    address = db.Column(db.Text)
    emergency_contact_name = db.Column(db.String(100))
    emergency_contact_phone = db.Column(db.String(20))
    
    # Two-Factor Authentication (Email-based, like main domain)
    two_factor_enabled = db.Column(db.Boolean, default=False, nullable=False)
    two_factor_email_code = db.Column(db.String(8))
    two_factor_email_expires = db.Column(db.DateTime)
    
    # Relationships
    # Use back_populates instead of backref to avoid conflicts
    tenant_profile = db.relationship('Tenant', back_populates='user', uselist=False, cascade='all, delete-orphan')
    staff_profile = db.relationship('Staff', back_populates='user', uselist=False, cascade='all, delete-orphan')
    
    def __init__(self, email, username=None, password=None, first_name=None, last_name=None, role='TENANT', status=None, **kwargs):
        self.email = email.lower().strip() if email else ''
        self.username = username.lower().strip() if username else None
        self.first_name = first_name.strip() if first_name else ''
        self.last_name = last_name.strip() if last_name else ''
        # Handle role - convert UserRole enum to string if needed
        if isinstance(role, UserRole):
            self.role = role.value
        elif isinstance(role, str):
            self.role = role.upper()
        else:
            self.role = 'TENANT'
        # Handle status - use provided status or default to PENDING_VERIFICATION
        if status is None:
            self.status = UserStatus.PENDING_VERIFICATION
        elif isinstance(status, UserStatus):
            self.status = status
        elif isinstance(status, str):
            try:
                self.status = UserStatus(status.lower())
            except ValueError:
                self.status = UserStatus.PENDING_VERIFICATION
        else:
            self.status = UserStatus.PENDING_VERIFICATION
        if password:
            self.set_password(password)
        
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
    
    def set_password(self, password):
        """Hash and set user password."""
        if password:
            self.password_hash = bcrypt.hashpw(
                password.encode('utf-8'), 
                bcrypt.gensalt()
            ).decode('utf-8')
    
    def check_password(self, password):
        """Verify user password."""
        if not password or not self.password_hash:
            return False
        return bcrypt.checkpw(
            password.encode('utf-8'), 
            self.password_hash.encode('utf-8')
        )
    
    @property
    def full_name(self):
        """Get user's full name."""
        try:
            first = getattr(self, 'first_name', '') or ''
            last = getattr(self, 'last_name', '') or ''
            return f"{first} {last}".strip()
        except Exception:
            return ''
    
    @property
    def name(self):
        """Alias for full_name for backward compatibility."""
        try:
            return self.full_name
        except Exception:
            return ''
    
    def update_last_login(self):
        """Update user's last login timestamp."""
        self.last_login = datetime.now(timezone.utc)
        try:
            db.session.commit()
        except Exception as e:
            # If commit fails, rollback and let the caller handle it
            db.session.rollback()
            raise e
    
    def is_property_manager(self):
        """Check if user is a property manager."""
        role_str = str(self.role).upper() if self.role else ''
        # ADMIN is not supported in subdomain - treat as MANAGER for backward compatibility
        return role_str in ['MANAGER', 'ADMIN'] or (isinstance(self.role, UserRole) and self.role == UserRole.MANAGER)
    
    def is_staff(self):
        """Check if user is staff."""
        role_str = str(self.role).upper() if self.role else ''
        return role_str == 'STAFF' or (isinstance(self.role, UserRole) and self.role == UserRole.STAFF)
    
    def is_tenant(self):
        """Check if user is a tenant."""
        role_str = str(self.role).upper() if self.role else ''
        return role_str == 'TENANT' or (isinstance(self.role, UserRole) and self.role == UserRole.TENANT)
    
    def is_active_user(self):
        """Check if user account is active."""
        # Handle both enum and string status values
        if isinstance(self.status, UserStatus):
            return self.status == UserStatus.ACTIVE
        status_str = str(self.status).lower() if self.status else ''
        return status_str == 'active'
    
    def to_dict(self, include_sensitive=False):
        """Convert user to dictionary."""
        try:
            # Safely get role value - handle both enum and string
            if isinstance(self.role, UserRole):
                role_value = self.role.value.lower() if hasattr(self.role, 'value') else str(self.role).lower()
            else:
                role_str = str(self.role).upper() if self.role else 'TENANT'
                # Map database values to model values
                # ADMIN is not supported in subdomain - map to property_manager for backward compatibility
                role_map = {
                    'ADMIN': 'property_manager',  # Map ADMIN to property_manager in subdomain
                    'MANAGER': 'property_manager',
                    'STAFF': 'staff',
                    'TENANT': 'tenant'
                }
                role_value = role_map.get(role_str, 'tenant')
        except (AttributeError, ValueError):
            role_value = 'tenant'
        
        # Safely get all fields, handling missing columns gracefully
        data = {
            'id': self.id,
            'email': getattr(self, 'email', '') or '',
            'username': getattr(self, 'username', '') or '',
            'first_name': getattr(self, 'first_name', '') or '',
            'last_name': getattr(self, 'last_name', '') or '',
            'full_name': self.full_name if hasattr(self, 'full_name') else f"{getattr(self, 'first_name', '')} {getattr(self, 'last_name', '')}".strip(),
            'name': self.name if hasattr(self, 'name') else self.full_name if hasattr(self, 'full_name') else '',
            'phone_number': getattr(self, 'phone_number', None),
            'date_of_birth': self.date_of_birth.isoformat() if hasattr(self, 'date_of_birth') and self.date_of_birth else None,
            'role': role_value,
            'status': getattr(self.status, 'value', str(self.status)) if hasattr(self, 'status') and self.status else 'pending_verification',
            'is_active': self.is_active_user(),  # Computed from status for backward compatibility
            'is_verified': self.is_verified,  # Use property that maps email_verified
            'created_at': self.created_at.isoformat() if hasattr(self, 'created_at') and self.created_at else None,
            'updated_at': self.updated_at.isoformat() if hasattr(self, 'updated_at') and self.updated_at else None,
            'last_login': self.last_login.isoformat() if hasattr(self, 'last_login') and self.last_login else None,
            'profile_image_url': getattr(self, 'profile_image_url', None),
            'avatar_url': getattr(self, 'profile_image_url', None),  # Backward compatibility alias
            'address': getattr(self, 'address', None),
            'emergency_contact_name': getattr(self, 'emergency_contact_name', None),
            'emergency_contact_phone': getattr(self, 'emergency_contact_phone', None),
            'two_factor_enabled': getattr(self, 'two_factor_enabled', False) or False
        }
        
        if include_sensitive:
            data.update({
                'reset_token': self.reset_token,  # Use property
                'reset_token_expiry': self.reset_token_expiry.isoformat() if self.reset_token_expiry else None
            })
        
        return data
    
    def __repr__(self):
        role_str = str(self.role) if self.role else 'UNKNOWN'
        username_str = self.username or self.email or 'NO_USERNAME'
        return f'<User {username_str} ({role_str})>'