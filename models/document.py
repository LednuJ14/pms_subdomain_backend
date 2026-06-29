from datetime import datetime, timezone
from app import db
from sqlalchemy.orm import backref
import enum

class DocumentType(enum.Enum):
    LEASE = 'lease'
    INVOICE = 'invoice'
    RECEIPT = 'receipt'
    POLICY = 'policy'
    MAINTENANCE = 'maintenance'
    OTHER = 'other'

class Document(db.Model):
    __tablename__ = 'documents'
    
    # Primary key
    id = db.Column(db.Integer, primary_key=True)
    
    # Basic Information - Match actual database schema exactly
    # Map 'name' attribute to 'title' column (database has 'title' not 'name')
    name = db.Column('title', db.String(255), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    
    # Document Type - Match actual database enum: 'lease','policy','form','notice','other'
    document_type = db.Column(db.String(50), nullable=True, default='other')  # enum as string
    
    # Relationships
    uploaded_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=True)  # For tenant-specific documents
    
    # Visibility - Match actual database enum: 'public','tenants_only','staff_only','private'
    visibility = db.Column(db.String(50), nullable=True, default='private')  # enum as string
    
    # Timestamps
    created_at = db.Column(db.DateTime, nullable=True, server_default=db.func.current_timestamp())
    
    # Relationships - Use lazy='noload' to prevent automatic queries that might fail
    uploader = db.relationship('User', backref=backref('uploaded_documents', lazy='noload'))
    property_obj = db.relationship('Property', backref='documents')
    
    # Compatibility properties for fields that don't exist in database
    @property
    def description(self):
        """Compatibility property - returns None since column doesn't exist."""
        return None
    
    @property
    def original_filename(self):
        """Compatibility property - returns filename as fallback."""
        return self.filename
    
    @property
    def file_size(self):
        """Compatibility property - returns None since column doesn't exist."""
        return None
    
    @property
    def mime_type(self):
        """Compatibility property - returns None since column doesn't exist."""
        return None
    
    @property
    def category(self):
        """Compatibility property - returns None since column doesn't exist."""
        return None
    
    @property
    def unit_id(self):
        """Compatibility property - returns None since column doesn't exist."""
        return None
    
    # tenant_id is now a real column, so we don't need the compatibility property
    
    @property
    def tenancy_id(self):
        """Compatibility property - returns None since column doesn't exist."""
        return None
    
    @property
    def is_active(self):
        """Compatibility property - returns True by default."""
        return True
    
    @property
    def requires_signature(self):
        """Compatibility property - returns False by default."""
        return False
    
    @property
    def updated_at(self):
        """Compatibility property - returns created_at as fallback."""
        return self.created_at
    
    @property
    def is_public(self):
        """Compatibility property - maps visibility to boolean."""
        return self.visibility == 'public' if self.visibility else False
    
    def to_dict(self):
        """Convert document to dictionary - matches actual database schema."""
        try:
            # Safely get uploader name
            uploader_name = None
            if self.uploaded_by:
                try:
                    from models.user import User
                    uploader = User.query.get(self.uploaded_by)
                    if uploader:
                        uploader_name = f"{getattr(uploader, 'first_name', '')} {getattr(uploader, 'last_name', '')}".strip() or None
                except:
                    pass
            
            # Safely get property name
            property_name = None
            if self.property_id:
                try:
                    from models.property import Property
                    property_obj = Property.query.get(self.property_id)
                    if property_obj:
                        property_name = getattr(property_obj, 'name', None)
                except:
                    pass
            
            # Safely get tenant name if tenant_id is set
            tenant_name = None
            if self.tenant_id:
                try:
                    from models.tenant import Tenant
                    tenant_obj = Tenant.query.get(self.tenant_id)
                    if tenant_obj and tenant_obj.user:
                        first_name = getattr(tenant_obj.user, 'first_name', '') or ''
                        last_name = getattr(tenant_obj.user, 'last_name', '') or ''
                        tenant_name = f"{first_name} {last_name}".strip() or getattr(tenant_obj.user, 'email', None) or f"Tenant {self.tenant_id}"
                except:
                    pass
            
            return {
                'id': self.id,
                'name': self.name,
                'title': self.name,  # Alias for compatibility
                'filename': self.filename,
                'description': getattr(self, 'description', None),  # Compatibility property
                'document_type': str(self.document_type) if self.document_type else 'other',
                'file_path': self.file_path,
                'file_size': getattr(self, 'file_size', None),  # Compatibility property
                'mime_type': getattr(self, 'mime_type', None),  # Compatibility property
                'uploaded_by': self.uploaded_by,
                'uploader_name': uploader_name,
                'property_id': self.property_id,
                'property_name': property_name,
                'tenant_id': self.tenant_id,  # Real column now
                'tenant_name': tenant_name,  # Tenant name for display
                'unit_id': getattr(self, 'unit_id', None),  # Compatibility property
                'visibility': self.visibility or 'private',
                'is_public': self.is_public,  # Compatibility property (derived from visibility)
                'created_at': self.created_at.isoformat() if self.created_at else None,
                'updated_at': getattr(self, 'updated_at', None)  # Compatibility property
            }
        except Exception as e:
            # Fallback representation
            return {
                'id': self.id,
                'name': self.name or 'Untitled Document',
                'title': self.name or 'Untitled Document',
                'filename': getattr(self, 'filename', ''),
                'description': None,
                'document_type': str(self.document_type) if self.document_type else 'other',
                'file_path': getattr(self, 'file_path', ''),
                'file_size': None,
                'mime_type': None,
                'uploaded_by': getattr(self, 'uploaded_by', None),
                'uploader_name': None,
                'property_id': getattr(self, 'property_id', None),
                'property_name': None,
                'tenant_id': getattr(self, 'tenant_id', None),
                'tenant_name': None,
                'unit_id': None,
                'visibility': getattr(self, 'visibility', 'private'),
                'is_public': False,
                'created_at': None,
                'updated_at': None
            }
