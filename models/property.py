from datetime import datetime, timezone
from app import db
from sqlalchemy import Numeric
from sqlalchemy.orm import backref
import enum
import json

class PropertyType(enum.Enum):
    BED_SPACE = 'bed_space'
    DORMITORY = 'dormitory'
    BOARDING_HOUSE = 'boarding_house'
    STUDIO_APARTMENT = 'studio_apartment'
    ROOM_FOR_RENT = 'room_for_rent'

class PropertyStatus(enum.Enum):
    ACTIVE = 'active'
    INACTIVE = 'inactive'
    MAINTENANCE = 'maintenance'

class UnitStatus(enum.Enum):
    # Match database enum values exactly: 'vacant','available','occupied','rented','reserved','maintenance'
    VACANT = 'vacant'
    AVAILABLE = 'available'
    OCCUPIED = 'occupied'
    RENTED = 'rented'
    RESERVED = 'reserved'
    MAINTENANCE = 'maintenance'

class BathroomType(enum.Enum):
    OWN = 'own'
    SHARE = 'share'

class Property(db.Model):
    __tablename__ = 'properties'
    
    # Primary key
    id = db.Column(db.Integer, primary_key=True)
    
    # Basic Information - Map 'name' attribute to 'title' column
    name = db.Column('title', db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    
    # Property Details - Match actual database schema
    # Use String instead of Enum to avoid validation issues with database enum values
    property_type = db.Column(db.String(50), nullable=False)  # enum as string (bed_space, dormitory, boarding_house, etc.)
    furnishing = db.Column(db.String(50), nullable=True)  # enum as string
    management_status = db.Column(db.String(50), nullable=True, default='managed')
    status = db.Column(db.String(50), nullable=True)  # varchar(50), not enum
    total_units = db.Column(db.Integer, default=0, nullable=True)
    
    # Location Information - Match actual database schema
    address = db.Column(db.String(255), nullable=False)
    city = db.Column(db.String(100), nullable=False)
    province = db.Column(db.String(100), nullable=True, default='Cebu')
    building_name = db.Column(db.String(255), nullable=True)
    
    # Contact Information - Match actual database schema
    contact_person = db.Column(db.String(255), nullable=True)
    contact_phone = db.Column(db.String(20), nullable=True)
    contact_email = db.Column(db.String(120), nullable=True)
    
    # Owner - Match actual database schema (manager_id doesn't exist - only owner_id exists)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    # manager_id doesn't exist in actual database - use compatibility property below
    
    # Financial Information
    monthly_rent = db.Column(Numeric(10, 2), nullable=True)
    
    # Additional Information
    amenities = db.Column(db.Text, nullable=True)  # text, not JSON
    images = db.Column(db.Text, nullable=True)
    legal_documents = db.Column(db.Text, nullable=True)
    additional_notes = db.Column(db.Text, nullable=True)
    
    # Portal Settings
    portal_enabled = db.Column(db.Boolean, default=False, nullable=True)
    portal_subdomain = db.Column(db.String(100), unique=True, nullable=True)
    
    # Display & Branding Settings
    display_settings = db.Column(db.Text, nullable=True)  # longtext
    
    # Timestamps - Match actual database schema (nullable with defaults)
    created_at = db.Column(db.DateTime, nullable=True, server_default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, nullable=True, server_default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    
    # Relationships
    units = db.relationship('Unit', backref='property', cascade='all, delete-orphan', lazy='dynamic')
    # Use lazy='noload' for managed_properties to prevent automatic queries
    owner = db.relationship('User', foreign_keys=[owner_id], backref='owned_properties')
    # manager relationship removed - manager_id doesn't exist in actual database schema
    
    def __init__(self, name, address, city, property_type, owner_id, **kwargs):
        self.name = name.strip() if name else None
        self.address = address.strip() if address else None
        self.city = city.strip() if city else None
        self.property_type = property_type
        self.owner_id = owner_id
        
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
    
    @property
    def state(self):
        """Compatibility property - returns None since column doesn't exist in database."""
        return None
    
    @property
    def zip_code(self):
        """Compatibility property - returns None since column doesn't exist in database."""
        return None
    
    @property
    def country(self):
        """Compatibility property - returns default value since column doesn't exist in database."""
        return 'Philippines'
    
    @property
    def full_address(self):
        """Get complete address string."""
        parts = [self.address, self.city]
        # state and zip_code don't exist in database, so skip them
        return ', '.join(parts)
    
    @property
    def occupied_units(self):
        """Get count of occupied units."""
        try:
            # Use string values since status is now String type
            # Access Unit model via string reference to avoid circular import
            Unit = db.Model._decl_class_registry.get('Unit')
            if Unit:
                return self.units.filter(
                    db.or_(Unit.status == 'occupied', Unit.status == 'rented')
                ).count()
            else:
                # Fallback: iterate through units
                return sum(1 for u in self.units.all() if str(getattr(u, 'status', '')).lower() in ['occupied', 'rented'])
        except Exception:
            # Fallback: iterate through units
            try:
                return sum(1 for u in self.units.all() if str(getattr(u, 'status', '')).lower() in ['occupied', 'rented'])
            except:
                return 0
    
    @property
    def available_units(self):
        """Get count of available units."""
        try:
            # Use string values since status is now String type
            Unit = db.Model._decl_class_registry.get('Unit')
            if Unit:
                return self.units.filter(
                    db.or_(Unit.status == 'available', Unit.status == 'vacant')
                ).count()
            else:
                # Fallback: iterate through units
                return sum(1 for u in self.units.all() if str(getattr(u, 'status', '')).lower() in ['available', 'vacant'])
        except Exception:
            # Fallback: iterate through units
            try:
                return sum(1 for u in self.units.all() if str(getattr(u, 'status', '')).lower() in ['available', 'vacant'])
            except:
                return 0
    
    @property
    def occupancy_rate(self):
        """Calculate occupancy rate percentage."""
        if self.total_units == 0:
            return 0
        return round((self.occupied_units / self.total_units) * 100, 2)
    
    @property
    def manager_id(self):
        """Compatibility property - returns owner_id since manager_id doesn't exist in database."""
        return self.owner_id
    
    @property
    def manager_phone(self):
        """Compatibility property - returns contact_phone."""
        return self.contact_phone
    
    @property
    def manager_email(self):
        """Compatibility property - returns contact_email."""
        return self.contact_email
    
    def _parse_display_settings(self):
        """Parse display_settings from JSON string to dict."""
        display_settings = getattr(self, 'display_settings', None)
        if not display_settings:
            return {}
        
        # If it's already a dict, return it
        if isinstance(display_settings, dict):
            return display_settings
        
        # If it's a string, try to parse it as JSON
        if isinstance(display_settings, str):
            try:
                return json.loads(display_settings)
            except (json.JSONDecodeError, TypeError):
                return {}
        
        # Fallback to empty dict
        return {}
    
    def to_dict(self, include_units=False):
        """Convert property to dictionary."""
        try:
            # Safely get computed properties with error handling
            try:
                occupied_units = self.occupied_units
            except Exception:
                occupied_units = 0
            
            try:
                available_units = self.available_units
            except Exception:
                available_units = 0
            
            try:
                occupancy_rate = self.occupancy_rate
            except Exception:
                occupancy_rate = 0.0
            
            try:
                full_address = self.full_address
            except Exception:
                full_address = f"{getattr(self, 'address', '')}, {getattr(self, 'city', '')}"
            
            data = {
                'id': self.id,
                'name': getattr(self, 'name', None) or getattr(self, 'title', None),
                'description': getattr(self, 'description', None),
                'address': getattr(self, 'address', None),
                'city': getattr(self, 'city', None),
                'province': getattr(self, 'province', None),
                'building_name': getattr(self, 'building_name', None),
                'state': self.state,
                'zip_code': self.zip_code,
                'country': self.country,
                'full_address': full_address,
                'property_type': self.property_type if isinstance(self.property_type, str) else (self.property_type.value if hasattr(self.property_type, 'value') else str(self.property_type)),
                'status': self.status if isinstance(self.status, str) else (self.status.value if hasattr(self.status, 'value') else str(self.status)),
                'total_units': getattr(self, 'total_units', 0) or 0,
                'occupied_units': occupied_units,
                'available_units': available_units,
                'occupancy_rate': occupancy_rate,
                'year_built': getattr(self, 'year_built', None),
                'total_floors': getattr(self, 'total_floors', None),
                'property_value': float(getattr(self, 'property_value', None)) if hasattr(self, 'property_value') and getattr(self, 'property_value', None) else None,
                'monthly_maintenance_fee': float(getattr(self, 'monthly_maintenance_fee', 0)) if hasattr(self, 'monthly_maintenance_fee') and getattr(self, 'monthly_maintenance_fee', None) else 0.00,
                'manager_id': self.manager_id,
                'manager_phone': self.manager_phone,
                'manager_email': self.manager_email,
                'contact_person': getattr(self, 'contact_person', None),
                'contact_phone': getattr(self, 'contact_phone', None),
                'contact_email': getattr(self, 'contact_email', None),
                'owner_id': getattr(self, 'owner_id', None),
                'amenities': getattr(self, 'amenities', None) or '',
                'images': getattr(self, 'images', None),
                'portal_enabled': getattr(self, 'portal_enabled', False),
                'portal_subdomain': getattr(self, 'portal_subdomain', None),
                'parking_spaces': getattr(self, 'parking_spaces', 0),
                'has_elevator': getattr(self, 'has_elevator', False),
                'has_security': getattr(self, 'has_security', False),
                'has_gym': getattr(self, 'has_gym', False),
                'has_pool': getattr(self, 'has_pool', False),
                'has_laundry': getattr(self, 'has_laundry', False),
                'display_settings': self._parse_display_settings(),
                'created_at': self.created_at.isoformat() if self.created_at else None,
                'updated_at': self.updated_at.isoformat() if self.updated_at else None
            }
            
            if include_units:
                try:
                    data['units'] = [unit.to_dict() for unit in self.units.all()]
                except Exception as e:
                    data['units'] = []
            
            return data
        except Exception as e:
            # Return minimal data if full conversion fails
            import traceback
            traceback.print_exc()
            return {
                'id': self.id,
                'name': getattr(self, 'name', None) or getattr(self, 'title', None),
                'address': getattr(self, 'address', None),
                'city': getattr(self, 'city', None),
                'portal_subdomain': getattr(self, 'portal_subdomain', None),
                'portal_enabled': getattr(self, 'portal_enabled', False),
                'error': f'Error converting property: {str(e)}'
            }
    
    def __repr__(self):
        return f'<Property {self.name} in {self.city}>'

class Unit(db.Model):
    __tablename__ = 'units'
    
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=False)
    
    # Unit Details - Match ACTUAL database schema exactly
    # Database column is 'unit_name', but we use 'unit_number' as the model attribute
    unit_number = db.Column('unit_name', db.String(255), nullable=False)
    bedrooms = db.Column(db.Integer, default=0, nullable=True)
    bathrooms = db.Column(db.Enum(BathroomType), default=BathroomType.OWN, nullable=True)
    
    # Size - Database has 'size_sqm' (int) not 'square_feet' or 'square_meters'
    size_sqm = db.Column(db.Integer, nullable=True)
    
    # Financial Information
    monthly_rent = db.Column(Numeric(10, 2), nullable=True)
    security_deposit = db.Column(Numeric(10, 2), nullable=True)
    
    # Status - Database enum: 'vacant','available','occupied','rented','reserved','maintenance'
    # Use String type instead of Enum to avoid validation issues with database enum values
    # SQLAlchemy will store/retrieve as string, matching database enum
    status = db.Column(db.String(20), default='vacant', nullable=True)
    
    # Additional fields from actual database schema
    description = db.Column(db.Text, nullable=True)
    floor_number = db.Column(db.String(20), nullable=True)  # Database has 'floor_number' not 'floor'
    parking_spaces = db.Column(db.Integer, default=0, nullable=False)  # Database has 'parking_spaces'
    images = db.Column(db.Text, nullable=True)
    inquiries_count = db.Column(db.Integer, default=0, nullable=False)
    amenities = db.Column(db.Text, nullable=True)  # Database has 'amenities' (longtext) not 'utilities_included'
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False)
    
    # Relationships
    # Use back_populates instead of backref to avoid conflicts
    tenant_units = db.relationship('TenantUnit', back_populates='unit', cascade='all, delete-orphan')
    
    def __init__(self, property_id, unit_number, monthly_rent=None, **kwargs):
        self.property_id = property_id
        self.unit_number = unit_number.strip() if unit_number else ''
        if monthly_rent is not None:
            self.monthly_rent = monthly_rent
        
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
    
    @property
    def current_tenant(self):
        """Get current tenant for this unit."""
        try:
            # Use string reference to avoid circular import
            TenantUnit = db.Model._decl_class_registry.get('TenantUnit')
            if TenantUnit:
                active_tenant_unit = TenantUnit.query.filter_by(
                    unit_id=self.id,
                    is_active=True
                ).first()
                return active_tenant_unit.tenant if active_tenant_unit else None
        except Exception:
            pass
        return None
    
    @property
    def is_occupied(self):
        """Check if unit is currently occupied."""
        status_str = str(self.status).lower() if self.status else 'vacant'
        return status_str in ['occupied', 'rented']
    
    @property
    def is_available(self):
        """Check if unit is available for rent."""
        status_str = str(self.status).lower() if self.status else 'vacant'
        return status_str in ['available', 'vacant']
    
    def to_dict(self, include_tenant=False):
        """Convert unit to dictionary - matches actual database schema."""
        # Map bathrooms enum to value - handle enum properly
        # Database enum expects: OWN, SHARE (uppercase names)
        # Python enum has: OWN = 'own', SHARE = 'share' (name = uppercase, value = lowercase)
        bathrooms_value = 'own'  # default lowercase for frontend
        try:
            # Safely access bathrooms attribute - might fail if enum validation error
            bathrooms_attr = None
            try:
                bathrooms_attr = self.bathrooms
            except Exception as attr_error:
                # If accessing bathrooms fails due to enum validation, get raw value from database
                try:
                    from sqlalchemy import text
                    raw_value = db.session.execute(
                        text("SELECT bathrooms FROM units WHERE id = :unit_id"),
                        {'unit_id': self.id}
                    ).first()
                    if raw_value and raw_value[0]:
                        bathrooms_attr = raw_value[0]
                except:
                    pass
            
            if bathrooms_attr:
                if hasattr(bathrooms_attr, 'name'):
                    # Enum name (OWN, SHARE) - convert to lowercase for frontend
                    bathrooms_value = bathrooms_attr.name.lower()
                elif hasattr(bathrooms_attr, 'value'):
                    # Enum value ('own', 'share') - use as is
                    bathrooms_value = str(bathrooms_attr.value).lower()
                elif isinstance(bathrooms_attr, str):
                    # String value - normalize to lowercase
                    bathrooms_value = bathrooms_attr.lower()
                else:
                    bathrooms_value = str(bathrooms_attr).lower()
        except Exception as bathroom_error:
            # If bathroom enum conversion fails, use default
            try:
                from flask import current_app
                current_app.logger.warning(f"Error converting bathroom enum for unit {self.id}: {str(bathroom_error)}")
            except:
                pass
            bathrooms_value = 'own'
        
        # Map status enum to value - handle both enum and string (database returns string)
        if self.status is None:
            status_value = 'vacant'
        elif hasattr(self.status, 'value'):
            status_value = self.status.value
        elif isinstance(self.status, str):
            status_value = self.status.lower()
        else:
            status_value = str(self.status).lower()
        
        data = {
            'id': self.id,
            'property_id': self.property_id,
            'unit_number': self.unit_number,
            'unit_name': self.unit_number,  # Alias for compatibility
            'name': self.unit_number,  # Another alias for compatibility
            'bedrooms': self.bedrooms or 0,
            'bathrooms': bathrooms_value,  # Return lowercase for frontend compatibility
            'size_sqm': self.size_sqm,
            # Compatibility aliases for frontend
            'square_feet': None,  # Not in database
            'square_meters': self.size_sqm,  # Use size_sqm as alias
            'monthly_rent': float(self.monthly_rent) if self.monthly_rent else None,
            'security_deposit': float(self.security_deposit) if self.security_deposit else None,
            'status': status_value,
            'description': self.description,
            'floor_number': self.floor_number,
            # Compatibility: map floor_number to floor for backward compatibility
            'floor': self.floor_number,
            'parking_spaces': self.parking_spaces or 0,
            # Compatibility aliases
            'has_parking': (self.parking_spaces or 0) > 0,
            'parking_space_number': str(self.parking_spaces) if self.parking_spaces else None,
            'images': self.images,
            'inquiries_count': self.inquiries_count or 0,
            'amenities': self.amenities,
            # Compatibility alias
            'utilities_included': self.amenities if self.amenities else [],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
        
        if include_tenant:
            try:
                if hasattr(self, 'current_tenant') and self.current_tenant:
                    data['current_tenant'] = self.current_tenant.to_dict(include_user=True)
            except Exception:
                pass  # Silently fail if tenant relationship doesn't work
        
        return data
    
    def __repr__(self):
        return f'<Unit {self.unit_number} at Property {self.property_id}>'