from datetime import datetime, timezone
from app import db
import enum

class AnnouncementType(enum.Enum):
    GENERAL = 'general'
    MAINTENANCE = 'maintenance'
    EMERGENCY = 'emergency'
    EVENT = 'event'

class AnnouncementPriority(enum.Enum):
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'
    URGENT = 'urgent'

class Announcement(db.Model):
    __tablename__ = 'announcements'
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)  # Database has varchar(255)
    content = db.Column(db.Text, nullable=False)
    # Use String type instead of Enum to avoid validation issues with database enum values
    # Database enum: 'general', 'maintenance', 'emergency', 'event'
    announcement_type = db.Column(db.String(20), default='general', nullable=True)  # DB allows NULL
    # Database enum: 'low', 'medium', 'high', 'urgent'
    priority = db.Column(db.String(20), default='medium', nullable=True)  # DB allows NULL
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=True)  # EXISTS in DB, was missing!
    published_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # DB has published_by, not created_by
    is_published = db.Column(db.Boolean, default=False, nullable=True)  # DB has is_published, not is_active
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), nullable=True)  # DB allows NULL
    
    # Relationships
    property_obj = db.relationship('Property', backref='announcements')  # Renamed from 'property' to avoid conflict with built-in property decorator
    author = db.relationship('User', foreign_keys=[published_by], backref='published_announcements')
    
    # Compatibility method to access property_obj as 'property' (using __getattr__ to avoid shadowing built-in property)
    def __getattr__(self, name):
        """Handle backward compatibility for 'property' attribute."""
        if name == 'property':
            return self.property_obj
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")
    
    # Compatibility properties for backward compatibility with existing code
    @property
    def created_by(self):
        """Alias for published_by for backward compatibility."""
        return self.published_by
    
    @created_by.setter
    def created_by(self, value):
        """Setter for created_by that updates published_by."""
        self.published_by = value
    
    @property
    def is_active(self):
        """Alias for is_published for backward compatibility."""
        return self.is_published if self.is_published is not None else False
    
    @is_active.setter
    def is_active(self, value):
        """Setter for is_active that updates is_published."""
        self.is_published = bool(value) if value is not None else False
    
    # Properties for fields that don't exist in DB (for backward compatibility)
    @property
    def is_pinned(self):
        """Return False since is_pinned doesn't exist in database."""
        return False
    
    @is_pinned.setter
    def is_pinned(self, value):
        """No-op setter since is_pinned doesn't exist in database."""
        pass
    
    @property
    def send_notification(self):
        """Return True since send_notification doesn't exist in database."""
        return True
    
    @send_notification.setter
    def send_notification(self, value):
        """No-op setter since send_notification doesn't exist in database."""
        pass
    
    @property
    def target_audience(self):
        """Return 'all' since target_audience doesn't exist in database."""
        from enum import Enum
        class TargetAudience(Enum):
            ALL = 'all'
            TENANTS = 'tenants'
            STAFF = 'staff'
        return TargetAudience.ALL
    
    @target_audience.setter
    def target_audience(self, value):
        """No-op setter since target_audience doesn't exist in database."""
        pass
    
    @property
    def updated_at(self):
        """Return created_at since updated_at doesn't exist in database."""
        return self.created_at
    
    @updated_at.setter
    def updated_at(self, value):
        """No-op setter since updated_at doesn't exist in database."""
        pass
    
    def to_dict(self, include_author_info=False):
        """Convert announcement to dictionary, matching database structure."""
        result = {
            'id': self.id,
            'title': self.title,
            'content': self.content,
            'announcement_type': self.announcement_type.value if hasattr(self.announcement_type, 'value') else (str(self.announcement_type) if self.announcement_type else 'general'),
            'priority': self.priority.value if hasattr(self.priority, 'value') else (str(self.priority) if self.priority else 'medium'),
            'property_id': self.property_id,
            'published_by': self.published_by,
            'is_published': self.is_published if self.is_published is not None else False,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            # Backward compatibility fields
            'created_by': self.published_by,  # Alias
            'is_active': self.is_published if self.is_published is not None else False,  # Alias
            'is_pinned': False,  # Doesn't exist in DB
            'send_notification': True,  # Doesn't exist in DB
            'target_audience': 'all',  # Doesn't exist in DB
            'updated_at': self.created_at.isoformat() if self.created_at else None  # Alias for created_at
        }
        
        if include_author_info:
            try:
                # Safely access author relationship
                if self.author:
                    role_value = 'unknown'
                    if hasattr(self.author, 'role'):
                        if hasattr(self.author.role, 'value'):
                            role_value = self.author.role.value
                        else:
                            role_value = str(self.author.role)
                    elif isinstance(self.author.role, str):
                        role_value = self.author.role
                    
                    result['author'] = {
                        'id': self.author.id,
                        'first_name': getattr(self.author, 'first_name', ''),
                        'last_name': getattr(self.author, 'last_name', ''),
                        'role': role_value
                    }
                else:
                    # If author doesn't exist, provide minimal info
                    result['author'] = {
                        'id': self.published_by,
                        'first_name': '',
                        'last_name': '',
                        'role': 'unknown'
                    }
            except Exception as author_error:
                # If author relationship fails, provide minimal info
                result['author'] = {
                    'id': self.published_by,
                    'first_name': '',
                    'last_name': '',
                    'role': 'unknown'
                }
        
        return result
