from datetime import datetime, timezone
from app import db
import enum

class TaskStatus(enum.Enum):
    OPEN = 'open'
    IN_PROGRESS = 'in_progress'
    COMPLETED = 'completed'
    CANCELLED = 'cancelled'

class TaskPriority(enum.Enum):
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'
    URGENT = 'urgent'

class Task(db.Model):
    __tablename__ = 'tasks'
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    # Use String type instead of Enum to avoid validation issues with database enum values
    # Database enum: 'low', 'medium', 'high', 'urgent'
    priority = db.Column(db.String(20), default='medium', nullable=False)
    # Database enum: 'open', 'in_progress', 'completed', 'cancelled'
    status = db.Column(db.String(20), default='open', nullable=False)
    assigned_to = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenants.id'))  # Optional: if task is tenant-specific
    unit_id = db.Column(db.Integer, db.ForeignKey('units.id'))  # Optional: if task is unit-specific
    due_date = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc), nullable=False)
    
    creator = db.relationship('User', foreign_keys=[created_by], backref='created_tasks')
    assignee = db.relationship('User', foreign_keys=[assigned_to], backref='assigned_tasks')
    tenant = db.relationship('Tenant', backref='tasks')
    unit = db.relationship('Unit', backref='tasks')
    
    def to_dict(self):
        try:
            # Safely get assignee name
            assigned_to_name = None
            if self.assigned_to:
                try:
                    # Try to use relationship first (if already loaded)
                    assignee = None
                    if hasattr(self, 'assignee') and self.assignee is not None:
                        try:
                            # Access the relationship - this might trigger a lazy load
                            assignee = self.assignee
                        except:
                            assignee = None
                    
                    # If relationship didn't work, query directly
                    if not assignee:
                        try:
                            from models.user import User
                            assignee = User.query.get(self.assigned_to)
                        except:
                            assignee = None
                    
                    if assignee:
                        # full_name is a method, call it
                        try:
                            if hasattr(assignee, 'full_name'):
                                if callable(assignee.full_name):
                                    assigned_to_name = assignee.full_name()
                                else:
                                    assigned_to_name = assignee.full_name
                            else:
                                # Fallback to manual construction
                                first = getattr(assignee, 'first_name', '') or ''
                                last = getattr(assignee, 'last_name', '') or ''
                                assigned_to_name = f"{first} {last}".strip() or None
                        except:
                            # If full_name fails, try manual construction
                            first = getattr(assignee, 'first_name', '') or ''
                            last = getattr(assignee, 'last_name', '') or ''
                            assigned_to_name = f"{first} {last}".strip() or None
                except Exception as e:
                    # Log error but don't crash - assignee name will remain None
                    try:
                        from flask import current_app
                        current_app.logger.warning(f"Error getting assignee name for task {self.id}: {str(e)}")
                    except:
                        pass
                    assigned_to_name = None
            
            # Safely get creator name
            creator_name = None
            if self.created_by:
                try:
                    # Try to use relationship first (if already loaded)
                    creator = None
                    if hasattr(self, 'creator') and self.creator is not None:
                        try:
                            # Access the relationship - this might trigger a lazy load
                            creator = self.creator
                        except:
                            creator = None
                    
                    # If relationship didn't work, query directly
                    if not creator:
                        try:
                            from models.user import User
                            creator = User.query.get(self.created_by)
                        except:
                            creator = None
                    
                    if creator:
                        # full_name is a method, call it
                        try:
                            if hasattr(creator, 'full_name'):
                                if callable(creator.full_name):
                                    creator_name = creator.full_name()
                                else:
                                    creator_name = creator.full_name
                            else:
                                # Fallback to manual construction
                                first = getattr(creator, 'first_name', '') or ''
                                last = getattr(creator, 'last_name', '') or ''
                                creator_name = f"{first} {last}".strip() or None
                        except:
                            # If full_name fails, try manual construction
                            first = getattr(creator, 'first_name', '') or ''
                            last = getattr(creator, 'last_name', '') or ''
                            creator_name = f"{first} {last}".strip() or None
                except Exception as e:
                    # Log error but don't crash - creator name will remain None
                    try:
                        from flask import current_app
                        current_app.logger.warning(f"Error getting creator name for task {self.id}: {str(e)}")
                    except:
                        pass
                    creator_name = None
            
            # Safely get tenant name
            tenant_name = None
            if self.tenant_id:
                try:
                    from models.tenant import Tenant
                    tenant = Tenant.query.get(self.tenant_id)
                    if tenant:
                        # Tenant name comes from user relationship
                        if tenant.user:
                            if hasattr(tenant.user, 'full_name'):
                                if callable(tenant.user.full_name):
                                    tenant_name = tenant.user.full_name()
                                else:
                                    tenant_name = tenant.user.full_name
                            else:
                                # Fallback to manual construction
                                first = getattr(tenant.user, 'first_name', '') or ''
                                last = getattr(tenant.user, 'last_name', '') or ''
                                tenant_name = f"{first} {last}".strip() or None
                        elif hasattr(tenant, 'name'):
                            tenant_name = tenant.name
                except Exception as e:
                    # Silently fail and continue
                    pass
            
            # Safely get unit name - handle enum validation errors
            unit_name = None
            if self.unit_id:
                try:
                    from sqlalchemy import text
                    
                    # Use raw SQL query to avoid enum validation issues
                    # This bypasses SQLAlchemy's enum validation which can fail
                    result = db.session.execute(
                        text("SELECT unit_name, property_id FROM units WHERE id = :unit_id"),
                        {'unit_id': self.unit_id}
                    ).first()
                    
                    if result:
                        unit_number = result[0]
                        property_id = result[1]
                        
                        # Get property name
                        if property_id:
                            try:
                                from models.property import Property
                                property_obj = Property.query.get(property_id)
                                if property_obj and hasattr(property_obj, 'name'):
                                    unit_name = f"{property_obj.name} - Unit {unit_number}"
                                else:
                                    unit_name = f"Unit {unit_number}"
                            except:
                                unit_name = f"Unit {unit_number}"
                        else:
                            unit_name = f"Unit {unit_number}"
                except Exception as e:
                    # Log error but don't crash
                    try:
                        from flask import current_app
                        current_app.logger.warning(f"Error getting unit name for task {self.id}: {str(e)}")
                    except:
                        pass
                    pass
            
            return {
                'id': self.id,
                'title': self.title,
                'description': self.description,
                'priority': self.priority.value if hasattr(self.priority, 'value') else (str(self.priority) if self.priority else 'medium'),
                'status': self.status.value if hasattr(self.status, 'value') else (str(self.status) if self.status else 'open'),
                'assigned_to': self.assigned_to,
                'assigned_to_name': assigned_to_name,
                'created_by': self.created_by,
                'creator_name': creator_name,
                'tenant_id': self.tenant_id,
                'tenant_name': tenant_name,
                'unit_id': self.unit_id,
                'unit_name': unit_name,
                'due_date': self.due_date.isoformat() if self.due_date else None,
                'completed_at': self.completed_at.isoformat() if self.completed_at else None,
                'notes': self.notes,
                'created_at': self.created_at.isoformat() if self.created_at else None,
                'updated_at': self.updated_at.isoformat() if self.updated_at else None
            }
        except Exception as e:
            # Fallback minimal representation
            return {
                'id': self.id,
                'title': self.title or 'Untitled Task',
                'description': self.description or '',
                'priority': 'medium',
                'status': 'open',
                'assigned_to': self.assigned_to,
                'assigned_to_name': None,
                'created_by': self.created_by,
                'creator_name': None,
                'tenant_id': self.tenant_id,
                'tenant_name': None,
                'unit_id': self.unit_id,
                'unit_name': None,
                'due_date': None,
                'completed_at': None,
                'notes': self.notes,
                'created_at': None,
                'updated_at': None
            }
