from datetime import datetime, timezone
from app import db
import enum

class NotificationType(enum.Enum):
    """Notification types for tenant and property manager notifications."""
    # Tenant notifications
    BILL_CREATED = 'bill_created'
    BILL_OVERDUE = 'bill_overdue'
    BILL_PAID = 'bill_paid'
    BILL_DUE_REMINDER = 'bill_due_reminder'  # Reminder before bill due date
    PAYMENT_APPROVED = 'payment_approved'
    PAYMENT_REJECTED = 'payment_rejected'
    REQUEST_CREATED = 'request_created'
    REQUEST_ASSIGNED = 'request_assigned'
    REQUEST_COMPLETED = 'request_completed'
    REQUEST_UPDATED = 'request_updated'
    MAINTENANCE_SCHEDULE_REMINDER = 'maintenance_schedule_reminder'  # Reminder for scheduled maintenance
    ANNOUNCEMENT = 'announcement'
    DOCUMENT_UPLOADED = 'document_uploaded'
    LEASE_RENEWAL = 'lease_renewal'
    LEASE_EXPIRING = 'lease_expiring'
    TASK_DEADLINE_REMINDER = 'task_deadline_reminder'  # Reminder for task deadlines
    GENERAL = 'general'
    
    # Property Manager notifications
    PAYMENT_SUBMITTED = 'payment_submitted'  # Tenant submitted payment proof
    NEW_MAINTENANCE_REQUEST = 'new_maintenance_request'  # New request from tenant
    REQUEST_STATUS_CHANGED = 'request_status_changed'  # Request status updated by staff
    FEEDBACK_SUBMITTED = 'feedback_submitted'  # Tenant submitted feedback
    BILL_OVERDUE_ALERT = 'bill_overdue_alert'  # Alert for overdue bills
    TENANT_REGISTERED = 'tenant_registered'  # New tenant registered
    LOGO_UPDATED = 'logo_updated'  # Property logo uploaded/updated
    
    # Staff notifications
    TASK_ASSIGNED = 'task_assigned'  # Task assigned to staff
    TASK_UPDATED = 'task_updated'  # Task details updated
    TASK_COMPLETED = 'task_completed'  # Task marked as completed
    REQUEST_ASSIGNED_TO_STAFF = 'request_assigned_to_staff'  # Maintenance request assigned to staff
    REQUEST_UPDATED_FOR_STAFF = 'request_updated_for_staff'  # Maintenance request updated
    REQUEST_COMPLETED_FOR_STAFF = 'request_completed_for_staff'  # Maintenance request completed
    ANNOUNCEMENT_FOR_STAFF = 'announcement_for_staff'  # Announcement for staff

class NotificationPriority(enum.Enum):
    """Notification priority levels."""
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'
    URGENT = 'urgent'

class Notification(db.Model):
    """Notification model for tenant and property manager notifications."""
    __tablename__ = 'notifications'
    
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=True, index=True)  # Nullable for PM notifications
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)  # Always required - recipient user
    recipient_type = db.Column(db.String(20), default='tenant', nullable=False, index=True)  # 'tenant', 'property_manager', or 'staff'
    
    # Notification Details
    notification_type = db.Column(db.String(50), nullable=False, index=True)
    priority = db.Column(db.String(20), default='medium', nullable=False)
    title = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text, nullable=False)
    
    # Related Entity Information (optional - for linking to bills, requests, etc.)
    related_entity_type = db.Column(db.String(50))  # 'bill', 'request', 'announcement', etc.
    related_entity_id = db.Column(db.Integer)  # ID of the related entity
    
    # Status
    is_read = db.Column(db.Boolean, default=False, nullable=False, index=True)
    read_at = db.Column(db.DateTime, nullable=True)
    
    # Action URL (optional - for deep linking)
    action_url = db.Column(db.String(500))
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False)
    
    # Relationships
    tenant = db.relationship('Tenant', backref='notifications')
    user = db.relationship('User', backref='notifications')
    
    def __init__(self, user_id, notification_type, title, message, **kwargs):
        # tenant_id is optional (for PM notifications)
        self.tenant_id = kwargs.get('tenant_id')
        self.user_id = user_id
        self.recipient_type = kwargs.get('recipient_type', 'tenant')  # Default to tenant
        # Convert enum to string if needed
        if isinstance(notification_type, NotificationType):
            self.notification_type = notification_type.value
        else:
            self.notification_type = str(notification_type)
        
        self.title = title.strip()
        self.message = message.strip()
        
        # Handle priority
        if 'priority' in kwargs:
            priority_val = kwargs['priority']
            if isinstance(priority_val, NotificationPriority):
                self.priority = priority_val.value
            else:
                self.priority = str(priority_val).lower()
        else:
            self.priority = 'medium'
        
        for key, value in kwargs.items():
            if hasattr(self, key) and key != 'priority':
                setattr(self, key, value)
    
    def mark_as_read(self):
        """Mark notification as read."""
        self.is_read = True
        self.read_at = datetime.now(timezone.utc)
        db.session.commit()
    
    def mark_as_unread(self):
        """Mark notification as unread."""
        self.is_read = False
        self.read_at = None
        db.session.commit()
    
    def to_dict(self, include_related=False):
        """Convert notification to dictionary."""
        data = {
            'id': self.id,
            'tenant_id': self.tenant_id,
            'user_id': self.user_id,
            'recipient_type': self.recipient_type,
            'notification_type': self.notification_type,
            'priority': self.priority,
            'title': self.title,
            'message': self.message,
            'related_entity_type': self.related_entity_type,
            'related_entity_id': self.related_entity_id,
            'is_read': self.is_read,
            'read_at': self.read_at.isoformat() if self.read_at else None,
            'action_url': self.action_url,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
        
        if include_related and self.related_entity_type and self.related_entity_id:
            # Optionally include related entity data
            try:
                if self.related_entity_type == 'bill':
                    from models.bill import Bill
                    bill = Bill.query.get(self.related_entity_id)
                    if bill:
                        data['related_entity'] = bill.to_dict()
                elif self.related_entity_type == 'request':
                    from models.request import MaintenanceRequest
                    request = MaintenanceRequest.query.get(self.related_entity_id)
                    if request:
                        data['related_entity'] = request.to_dict()
                elif self.related_entity_type == 'announcement':
                    from models.announcement import Announcement
                    announcement = Announcement.query.get(self.related_entity_id)
                    if announcement:
                        data['related_entity'] = announcement.to_dict()
            except Exception:
                # If related entity fetch fails, just skip it
                pass
        
        return data
    
    def __repr__(self):
        recipient = f'Tenant {self.tenant_id}' if self.tenant_id else f'User {self.user_id} ({self.recipient_type})'
        return f'<Notification {self.id}: {self.title} for {recipient}>'

