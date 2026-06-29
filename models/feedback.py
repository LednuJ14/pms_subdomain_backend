from datetime import datetime, timezone
from app import db
from sqlalchemy.orm import backref
from sqlalchemy import text
import enum

class FeedbackType(enum.Enum):
    COMPLAINT = 'complaint'
    SUGGESTION = 'suggestion'
    COMPLIMENT = 'compliment'
    OTHER = 'other'

class Feedback(db.Model):
    __tablename__ = 'feedback'
    
    # Primary key
    id = db.Column(db.Integer, primary_key=True)
    
    # Feedback Details - Match actual database schema exactly
    subject = db.Column(db.String(255), nullable=True)
    message = db.Column(db.Text, nullable=False)  # Database has 'message' not 'content'
    feedback_type = db.Column(db.String(50), nullable=True, default='other')  # enum as string
    rating = db.Column(db.Integer, nullable=True)  # 1-5 stars, nullable
    
    # Source - Match actual database schema
    submitted_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # Database has 'submitted_by' not 'tenant_id'
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=True)
    
    # Status - Match actual database enum: 'new','reviewed','responded','resolved'
    status = db.Column(db.String(50), nullable=True, default='new')  # enum as string
    
    # Timestamps
    created_at = db.Column(db.DateTime, nullable=True, server_default=db.func.current_timestamp())
    
    # Relationships - Use lazy='noload' to prevent automatic queries
    submitter = db.relationship('User', foreign_keys=[submitted_by], backref=backref('submitted_feedback', lazy='noload'))
    property_obj = db.relationship('Property', backref='feedback')
    
    # Compatibility properties for fields that don't exist in database
    @property
    def content(self):
        """Compatibility property - maps to message."""
        return self.message
    
    @property
    def tenant_id(self):
        """Compatibility property - maps to submitted_by."""
        return self.submitted_by
    
    @property
    def response(self):
        """Compatibility property - returns None since column doesn't exist."""
        return None
    
    @property
    def responded_by(self):
        """Compatibility property - returns None since column doesn't exist."""
        return None
    
    @property
    def responded_at(self):
        """Compatibility property - returns None since column doesn't exist."""
        return None
    
    @property
    def updated_at(self):
        """Compatibility property - returns created_at as fallback."""
        return self.created_at
    
    def to_dict(self):
        """Convert feedback to dictionary - matches actual database schema."""
        try:
            # Safely get submitter name
            submitter_name = None
            if self.submitted_by:
                try:
                    from models.user import User
                    submitter = User.query.get(self.submitted_by)
                    if submitter:
                        submitter_name = f"{getattr(submitter, 'first_name', '')} {getattr(submitter, 'last_name', '')}".strip() or None
                except:
                    pass
            
            # Safely get property name
            property_name = None
            if self.property_id:
                try:
                    from models.property import Property
                    property_obj = Property.query.get(self.property_id)
                    if property_obj:
                        # Try name, title, building_name - Property model uses 'title' column
                        property_name = (
                            getattr(property_obj, 'name', None) or
                            getattr(property_obj, 'title', None) or
                            getattr(property_obj, 'building_name', None) or
                            f'Property {self.property_id}'
                        )
                except:
                    pass
            
            # Get tenant and unit information
            tenant_name = submitter_name
            tenant_email = None
            tenant_phone = None
            unit_name = None
            
            if self.submitted_by:
                try:
                    from models.tenant import Tenant, TenantUnit
                    from models.property import Unit
                    from sqlalchemy import text
                    from datetime import date
                    
                    # Get tenant record
                    tenant = Tenant.query.filter_by(user_id=self.submitted_by).first()
                    if tenant:
                        # Get tenant user info
                        submitter = User.query.get(self.submitted_by)
                        if submitter:
                            tenant_email = getattr(submitter, 'email', None)
                            tenant_phone = getattr(submitter, 'phone_number', None)
                        
                        # Get active unit for this tenant
                        today = date.today()
                        active_unit = db.session.execute(text("""
                            SELECT tu.unit_id, u.unit_name, u.property_id
                            FROM tenant_units tu
                            INNER JOIN units u ON tu.unit_id = u.id
                            WHERE tu.tenant_id = :tenant_id 
                            AND tu.property_id = :property_id
                            AND (tu.move_out_date IS NULL OR tu.move_out_date >= :today)
                            LIMIT 1
                        """), {
                            'tenant_id': tenant.id,
                            'property_id': self.property_id,
                            'today': today
                        }).first()
                        
                        if active_unit:
                            unit_name = active_unit[1] or f'Unit {active_unit[0]}'
                except Exception as e:
                    # Silently fail if tenant/unit info can't be retrieved
                    pass
            
            return {
                'id': self.id,
                'feedback_id': f'FB-{str(self.id).zfill(3)}',  # Generate feedback_id like FB-001
                'subject': self.subject,
                'message': self.message,
                'content': getattr(self, 'content', self.message),  # Compatibility alias
                'feedback_type': str(self.feedback_type) if self.feedback_type else 'other',
                'type': str(self.feedback_type) if self.feedback_type else 'other',  # Alias for frontend
                'rating': self.rating,
                'submitted_by': self.submitted_by,
                'submitter_name': submitter_name,
                'tenant_name': tenant_name,
                'tenant_email': tenant_email,
                'tenant_phone': tenant_phone,
                'property_id': self.property_id,
                'property_name': property_name,
                'unit': unit_name or 'N/A',
                'unit_name': unit_name,
                'status': str(self.status) if self.status else 'new',
                'tenant_id': getattr(self, 'tenant_id', self.submitted_by),  # Compatibility property
                'response': getattr(self, 'response', None),  # Compatibility property
                'responded_by': getattr(self, 'responded_by', None),  # Compatibility property
                'responded_at': None,  # Compatibility property
                'priority': 'medium',  # Default priority (not in DB)
                'category': 'General',  # Default category (not in DB)
                'created_at': self.created_at.isoformat() if self.created_at else None,
                'updated_at': getattr(self, 'updated_at', self.created_at).isoformat() if getattr(self, 'updated_at', self.created_at) else None
            }
        except Exception as e:
            # Fallback representation
            return {
                'id': self.id,
                'subject': getattr(self, 'subject', None),
                'message': getattr(self, 'message', ''),
                'content': getattr(self, 'message', ''),
                'feedback_type': str(self.feedback_type) if self.feedback_type else 'other',
                'rating': getattr(self, 'rating', None),
                'submitted_by': getattr(self, 'submitted_by', None),
                'submitter_name': None,
                'property_id': getattr(self, 'property_id', None),
                'property_name': None,
                'status': str(self.status) if self.status else 'new',
                'tenant_id': getattr(self, 'submitted_by', None),
                'response': None,
                'responded_by': None,
                'responded_at': None,
                'created_at': None,
                'updated_at': None
            }