from datetime import datetime, timezone, date, timedelta
from app import db
from sqlalchemy import Numeric, text
import enum
import uuid
import os
import io
from flask import current_app
from xml.sax.saxutils import escape
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False


class ContractType(enum.Enum):
    """Contract duration types."""
    QUARTERLY = 'quarterly'  # 3 months
    YEARLY = 'yearly'  # 12 months


class ContractStatus(enum.Enum):
    """Contract status types."""
    DRAFT = 'draft'
    ACTIVE = 'active'
    EXPIRED = 'expired'
    RENEWED = 'renewed'  # Contract was renewed, replaced by new contract
    TERMINATED = 'terminated'
    CANCELLED = 'cancelled'


class RentalContract(db.Model):
    """Rental contract model for managing tenant rental agreements."""
    
    __tablename__ = 'rental_contracts'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Contract Identification
    contract_number = db.Column(db.String(50), unique=True, nullable=False, index=True)
    
    # Relationships
    tenant_unit_id = db.Column(db.Integer, db.ForeignKey('tenant_units.id'), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    unit_id = db.Column(db.Integer, db.ForeignKey('units.id'), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=False)
    inquiry_id = db.Column(db.Integer, db.ForeignKey('inquiries.id'), nullable=True)
    
    # Contract Type and Duration
    contract_type = db.Column(db.String(20), nullable=False)  # 'quarterly' or 'yearly'
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    
    # Financial Terms
    monthly_rent = db.Column(Numeric(10, 2), nullable=False)
    security_deposit = db.Column(Numeric(10, 2), nullable=True)
    total_contract_value = db.Column(Numeric(10, 2), nullable=True)  # Total rent for contract period
    
    # Contract Status
    status = db.Column(db.String(20), default='draft', nullable=False)  # draft, active, expired, renewed, terminated, cancelled
    
    # Contract Terms and Conditions
    terms_and_conditions = db.Column(db.Text, nullable=True)
    special_conditions = db.Column(db.Text, nullable=True)
    
    # Renewal Information
    is_renewal = db.Column(db.Boolean, default=False, nullable=False)
    parent_contract_id = db.Column(db.Integer, db.ForeignKey('rental_contracts.id'), nullable=True)  # Link to previous contract if renewed
    renewal_count = db.Column(db.Integer, default=0, nullable=False)  # Number of times this contract has been renewed
    
    # Signatures and Approval
    tenant_signed = db.Column(db.Boolean, default=False, nullable=False)
    tenant_signed_date = db.Column(db.DateTime, nullable=True)
    landlord_signed = db.Column(db.Boolean, default=False, nullable=False)
    landlord_signed_date = db.Column(db.DateTime, nullable=True)
    landlord_signed_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # User ID who signed as landlord
    
    # E-Signature Audit Trail
    tenant_signature_url = db.Column(db.String(500), nullable=True)
    tenant_ip = db.Column(db.String(45), nullable=True)
    tenant_user_agent = db.Column(db.String(255), nullable=True)
    
    landlord_signature_url = db.Column(db.String(500), nullable=True)
    landlord_ip = db.Column(db.String(45), nullable=True)
    landlord_user_agent = db.Column(db.String(255), nullable=True)
    
    document_hash = db.Column(db.String(255), nullable=True)
    
    # Termination Information
    termination_date = db.Column(db.Date, nullable=True)
    termination_reason = db.Column(db.Text, nullable=True)
    terminated_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # User ID who terminated
    
    # Document References
    contract_document_path = db.Column(db.String(500), nullable=True)  # Path to signed contract PDF
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False)
    
    # Relationships
    tenant_unit = db.relationship('TenantUnit', foreign_keys=[tenant_unit_id], backref='rental_contracts')
    tenant = db.relationship('Tenant', foreign_keys=[tenant_id], backref='rental_contracts')
    parent_contract = db.relationship('RentalContract', remote_side=[id], backref='renewal_contracts')
    
    def __init__(self, tenant_unit_id, tenant_id, unit_id, property_id, contract_type, start_date, 
                 monthly_rent, security_deposit=None, end_date=None, **kwargs):
        """Initialize a rental contract."""
        self.tenant_unit_id = tenant_unit_id
        self.tenant_id = tenant_id
        self.unit_id = unit_id
        self.property_id = property_id
        self.contract_type = contract_type
        self.start_date = start_date
        self.monthly_rent = monthly_rent
        self.security_deposit = security_deposit
        
        # Generate contract number if not provided
        if 'contract_number' not in kwargs:
            self.contract_number = self._generate_contract_number()
        
        # Calculate end_date if not provided
        if end_date is None:
            self.end_date = self._calculate_end_date(start_date, contract_type)
        else:
            self.end_date = end_date
        
        # Calculate total contract value
        self.total_contract_value = self._calculate_total_value()
        
        # Set default status
        if 'status' not in kwargs:
            self.status = 'draft'
        
        # Set other kwargs
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
    
    def _generate_contract_number(self):
        """Generate a unique contract number."""
        # Format: CONTRACT-YYYYMMDD-XXXX (where XXXX is a short UUID)
        date_str = date.today().strftime('%Y%m%d')
        unique_id = str(uuid.uuid4())[:8].upper()
        return f"CONTRACT-{date_str}-{unique_id}"
    
    def _calculate_end_date(self, start_date, contract_type):
        """Calculate end date based on contract type."""
        if contract_type == 'quarterly':
            # 3 months from start date
            return start_date + timedelta(days=90)
        elif contract_type == 'yearly':
            # 12 months from start date
            return start_date + timedelta(days=365)
        else:
            # Default to 30 days if unknown type
            return start_date + timedelta(days=30)
    
    def _calculate_total_value(self):
        """Calculate total contract value based on duration and monthly rent."""
        if not self.start_date or not self.end_date or not self.monthly_rent:
            return None
        
        # Calculate number of months
        delta = self.end_date - self.start_date
        months = delta.days / 30.44  # Average days per month
        
        return float(self.monthly_rent) * months
    
    @property
    def is_expired(self):
        """Check if contract is expired."""
        if self.end_date:
            return self.end_date < date.today()
        return False
    
    @property
    def days_until_expiry(self):
        """Get days until contract expiry."""
        if self.end_date:
            delta = self.end_date - date.today()
            return delta.days
        return None
    
    @property
    def is_active(self):
        """Check if contract is currently active."""
        return (self.status == 'active' and 
                self.start_date <= date.today() <= self.end_date and
                not self.is_expired)
    
    @property
    def duration_months(self):
        """Get contract duration in months."""
        if self.start_date and self.end_date:
            delta = self.end_date - self.start_date
            return round(delta.days / 30.44, 1)
        return None
    
    def activate(self):
        """Activate the contract."""
        self.status = 'active'
        self.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        try:
            self._generate_pdf_and_upload_document()
        except Exception:
            pass
    
    def renew(self, new_contract_type=None, new_monthly_rent=None, start_date=None):
        """
        Create a renewal contract.
        
        Args:
            new_contract_type: New contract type (quarterly/yearly). If None, uses same as current.
            new_monthly_rent: New monthly rent. If None, uses same as current.
            start_date: Start date for new contract. If None, starts after current contract ends.
        
        Returns:
            New RentalContract object
        """
        # Determine new contract type
        contract_type = new_contract_type or self.contract_type
        
        # Determine new monthly rent
        monthly_rent = new_monthly_rent or self.monthly_rent
        
        # Determine start date (default to day after current contract ends)
        if start_date is None:
            start_date = self.end_date + timedelta(days=1)
        
        # Create new contract
        new_contract = RentalContract(
            tenant_unit_id=self.tenant_unit_id,
            tenant_id=self.tenant_id,
            unit_id=self.unit_id,
            property_id=self.property_id,
            contract_type=contract_type,
            start_date=start_date,
            monthly_rent=monthly_rent,
            security_deposit=self.security_deposit,
            is_renewal=True,
            parent_contract_id=self.id,
            renewal_count=self.renewal_count + 1,
            status='draft'
        )
        
        # Mark current contract as renewed
        self.status = 'renewed'
        self.updated_at = datetime.now(timezone.utc)
        
        db.session.add(new_contract)
        db.session.commit()
        
        return new_contract
    
    def terminate(self, termination_reason=None, terminated_by=None):
        """Terminate the contract."""
        self.status = 'terminated'
        self.termination_date = date.today()
        self.termination_reason = termination_reason
        self.terminated_by = terminated_by
        self.updated_at = datetime.now(timezone.utc)
        db.session.commit()
    
    def sign_by_tenant(self, signature_url=None, ip=None, user_agent=None):
        self.tenant_signed = True
        self.tenant_signed_date = datetime.now(timezone.utc)
        self.tenant_signature_url = signature_url
        self.tenant_ip = ip
        self.tenant_user_agent = user_agent
        self.updated_at = datetime.now(timezone.utc)
        if self.landlord_signed and self.tenant_signed:
            self.activate()
        db.session.commit()
    
    def sign_by_landlord(self, landlord_user_id, signature_url=None, ip=None, user_agent=None):
        self.landlord_signed = True
        self.landlord_signed_by = landlord_user_id
        self.landlord_signed_date = datetime.now(timezone.utc)
        self.landlord_signature_url = signature_url
        self.landlord_ip = ip
        self.landlord_user_agent = user_agent
        self.updated_at = datetime.now(timezone.utc)
        
        if self.tenant_signed and self.landlord_signed:
            self.activate()
        
        db.session.commit()
    
    def to_dict(self, include_tenant=False, include_unit=False):
        """Convert contract to dictionary."""
        return {
            'id': self.id,
            'contract_number': self.contract_number,
            'tenant_unit_id': self.tenant_unit_id,
            'tenant_id': self.tenant_id,
            'unit_id': self.unit_id,
            'property_id': self.property_id,
            'contract_type': self.contract_type,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'monthly_rent': float(self.monthly_rent) if self.monthly_rent else None,
            'security_deposit': float(self.security_deposit) if self.security_deposit else None,
            'total_contract_value': float(self.total_contract_value) if self.total_contract_value else None,
            'status': self.status,
            'terms_and_conditions': self.terms_and_conditions,
            'special_conditions': self.special_conditions,
            'is_renewal': self.is_renewal,
            'parent_contract_id': self.parent_contract_id,
            'renewal_count': self.renewal_count,
            'tenant_signed': self.tenant_signed,
            'tenant_signed_date': self.tenant_signed_date.isoformat() if self.tenant_signed_date else None,
            'landlord_signed': self.landlord_signed,
            'landlord_signed_date': self.landlord_signed_date.isoformat() if self.landlord_signed_date else None,
            'landlord_signed_by': self.landlord_signed_by,
            'termination_date': self.termination_date.isoformat() if self.termination_date else None,
            'termination_reason': self.termination_reason,
            'terminated_by': self.terminated_by,
            'contract_document_path': self.contract_document_path,
            'is_expired': self.is_expired,
            'days_until_expiry': self.days_until_expiry,
            'is_active': self.is_active,
            'duration_months': self.duration_months,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            **({'tenant': self.tenant.to_dict() if include_tenant and self.tenant else None} if include_tenant else {}),
            **({'unit': self.tenant_unit.unit.to_dict() if include_unit and self.tenant_unit and self.tenant_unit.unit else None} if include_unit else {})
        }
    
    def _generate_pdf_and_upload_document(self):
        from models.document import Document
        from models.property import Property
        from models.tenant import Tenant
        import io
        import requests
        import cloudinary.uploader
        from docxtpl import DocxTemplate

        prop = Property.query.get(self.property_id)
        tenant = Tenant.query.get(self.tenant_id)
        
        unit_number = None
        try:
            unit_number = getattr(self.tenant_unit.unit, 'unit_number', None)
        except Exception:
            pass

        tenant_name = None
        tenant_email = ''
        try:
            if tenant and tenant.user:
                fn = getattr(tenant.user, 'first_name', '') or ''
                ln = getattr(tenant.user, 'last_name', '') or ''
                tenant_name = f"{fn} {ln}".strip() or getattr(tenant.user, 'email', None)
                tenant_email = getattr(tenant.user, 'email', '')
        except Exception:
            pass

        context = {
            'contract_number': self.contract_number,
            'date_generated': date.today().strftime('%Y-%m-%d'),
            'property_name': getattr(prop, 'title', '') or getattr(prop, 'name', ''),
            'tenant_name': tenant_name or str(self.tenant_id),
            'tenant_email': tenant_email,
            'property_address': getattr(prop, 'address', ''),
            'unit_number': unit_number or self.unit_id,
            'start_date': self.start_date.isoformat(),
            'end_date': self.end_date.isoformat(),
            'contract_type': self.contract_type,
            'monthly_rent': float(self.monthly_rent) if self.monthly_rent else 0,
            'security_deposit': float(self.security_deposit) if self.security_deposit else 0,
            'total_contract_value': float(self.total_contract_value) if self.total_contract_value else 0,
            'terms_and_conditions': self.terms_and_conditions or "",
            'special_conditions': self.special_conditions or "",
            'landlord_signed': self.landlord_signed,
            'landlord_signed_date': self.landlord_signed_date.isoformat() if self.landlord_signed_date else "",
            'tenant_signed': self.tenant_signed,
            'tenant_signed_date': self.tenant_signed_date.isoformat() if self.tenant_signed_date else ""
        }

        try:
            template_url = getattr(prop, 'contract_template_url', None)
            if template_url:
                response = requests.get(template_url)
                doc = DocxTemplate(io.BytesIO(response.content))
            else:
                doc = DocxTemplate('templates/default_contract_template.docx')
                
            from docxtpl import InlineImage
            from docx.shared import Mm
            
            # E-Signature Additions
            if self.tenant_signature_url:
                try:
                    resp = requests.get(self.tenant_signature_url)
                    context['tenant_signature_image'] = InlineImage(doc, io.BytesIO(resp.content), height=Mm(15))
                except Exception as e:
                    current_app.logger.error(f"Failed to load tenant signature image: {e}")
            else:
                context['tenant_signature_image'] = ""
                
            if self.landlord_signature_url:
                try:
                    resp = requests.get(self.landlord_signature_url)
                    context['landlord_signature_image'] = InlineImage(doc, io.BytesIO(resp.content), height=Mm(15))
                except Exception as e:
                    current_app.logger.error(f"Failed to load landlord signature image: {e}")
            else:
                context['landlord_signature_image'] = ""
                
            # Create a document hash if both signed and hash doesn't exist
            if self.tenant_signed and self.landlord_signed and not self.document_hash:
                import hashlib
                import time
                hash_input = f"{self.contract_number}-{self.tenant_signature_url}-{self.landlord_signature_url}-{time.time()}"
                self.document_hash = hashlib.sha256(hash_input.encode()).hexdigest()
                
            context['tenant_ip'] = self.tenant_ip or 'N/A'
            context['tenant_user_agent'] = self.tenant_user_agent or 'N/A'
            context['landlord_ip'] = self.landlord_ip or 'N/A'
            context['landlord_user_agent'] = self.landlord_user_agent or 'N/A'
            context['document_hash'] = self.document_hash or 'Pending Signatures'
            
            
            doc.render(context)
            buffer = io.BytesIO()
            doc.save(buffer)
            buffer.seek(0)
            
            filename = f"rental_contract_{self.contract_number}.docx"
            
            # Use streaming upload to cloudinary
            upload_result = cloudinary.uploader.upload(
                buffer, 
                resource_type="raw", 
                folder="PMS/contracts",
                public_id=filename
            )
            self.contract_document_path = upload_result.get('secure_url')
            
            existing_doc = Document.query.filter_by(
                filename=filename,
                property_id=self.property_id,
                tenant_id=self.tenant_id,
                document_type='lease'
            ).first()
            
            if existing_doc:
                existing_doc.file_path = self.contract_document_path
                existing_doc.name = f"Rental Contract {self.contract_number}"
                doc_rec = existing_doc
            else:
                doc_rec = Document(
                    name=f"Rental Contract {self.contract_number}",
                    filename=filename,
                    file_path=self.contract_document_path,
                    document_type='lease',
                    uploaded_by=getattr(prop, 'owner_id', None),
                    property_id=self.property_id,
                    tenant_id=self.tenant_id,
                    visibility='private'
                )
                db.session.add(doc_rec)
                
            db.session.add(self)
            db.session.commit()
            
        except Exception as e:
            current_app.logger.error(f"Docxtpl/Cloudinary error: {e}")
    
    def __repr__(self):
        return f'<RentalContract {self.contract_number} - {self.contract_type} - {self.status}>'

