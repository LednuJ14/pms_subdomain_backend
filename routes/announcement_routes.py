from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from datetime import datetime, timezone
from sqlalchemy import and_, or_

from app import db
from models.announcement import Announcement, AnnouncementType, AnnouncementPriority
from models.user import User, UserRole
from utils.error_responses import (
    property_context_required,
    property_access_denied,
    property_not_found
)
from utils.logging_helpers import log_property_access_attempt, log_property_operation

announcement_bp = Blueprint('announcements', __name__)

def get_current_user():
    """Helper function to get current user from JWT token."""
    current_user_id = get_jwt_identity()
    return User.query.get(current_user_id)

def can_manage_announcements(user):
    """Check if user can create/edit/delete announcements."""
    if not user:
        return False
    
    # Handle both enum and string values for role
    user_role = user.role
    if isinstance(user_role, UserRole):
        user_role_str = user_role.value
    elif isinstance(user_role, str):
        user_role_str = user_role.upper()
    else:
        user_role_str = str(user_role).upper() if user_role else ''
    
    # Check if user is a property manager or staff
    # PROPERTY_MANAGER is an alias for MANAGER
    allowed_roles = ['MANAGER', 'PROPERTY_MANAGER', 'STAFF']
    return user_role_str in allowed_roles

def can_view_announcement(user, announcement):
    """Check if user can view a specific announcement based on property_id."""
    if not user:
        return False
    
    # Handle both enum and string values for role
    user_role = user.role
    if isinstance(user_role, UserRole):
        user_role_str = user_role.value
    elif isinstance(user_role, str):
        user_role_str = user_role.upper()
    else:
        user_role_str = str(user_role).upper() if user_role else ''
    
    # Property managers and staff can see all announcements
    if user_role_str in ['MANAGER', 'PROPERTY_MANAGER', 'STAFF']:
        return True
    
    # For tenants: check if announcement is for their property or global (no property_id)
    if user_role_str == 'TENANT':
        # If announcement has no property_id, it's global and visible to all
        if announcement.property_id is None:
            return True
        
        # Check if tenant belongs to this property
        from models.tenant import Tenant, TenantUnit
        tenant = Tenant.query.filter_by(user_id=user.id).first()
        if tenant:
            tenant_unit = TenantUnit.query.filter_by(
                tenant_id=tenant.id,
                property_id=announcement.property_id
            ).first()
            if tenant_unit:
                return True
    
    return False

@announcement_bp.route('/', methods=['GET'])
@jwt_required()
def get_announcements():
    """
    Get announcements
    ---
    tags:
      - Announcements
    summary: Get announcements filtered by user's role
    description: Retrieve announcements filtered by user's role and target audience with pagination
    security:
      - Bearer: []
    parameters:
      - in: query
        name: page
        type: integer
        default: 1
      - in: query
        name: per_page
        type: integer
        default: 20
      - in: query
        name: search
        type: string
      - in: query
        name: type
        type: string
      - in: query
        name: priority
        type: string
      - in: query
        name: active
        type: boolean
        default: true
      - in: query
        name: pinned
        type: boolean
    responses:
      200:
        description: Announcements retrieved successfully
        schema:
          type: object
          properties:
            announcements:
              type: array
              items:
                type: object
            total:
              type: integer
            pages:
              type: integer
            current_page:
              type: integer
            per_page:
              type: integer
            has_next:
              type: boolean
            has_prev:
              type: boolean
      401:
        description: Unauthorized
      404:
        description: User not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Get query parameters
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)  # Max 100 items per page
        search = request.args.get('search', '')
        announcement_type = request.args.get('type')
        priority = request.args.get('priority')
        is_active = request.args.get('active', 'true').lower() == 'true'
        is_pinned = request.args.get('pinned')
        
        # Base query for published announcements (using is_published from database)
        query = Announcement.query.filter(Announcement.is_published == is_active)
        
        # Filter by property_id if user is a tenant (property-specific announcements)
        # Note: target_audience doesn't exist in database, so we filter by property_id instead
        # Handle both enum and string values for role
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else ''
        
        if user_role_str == 'TENANT':
            # Get tenant's property_id from their tenant_units
            try:
                from models.tenant import Tenant, TenantUnit
                tenant = Tenant.query.filter_by(user_id=current_user.id).first()
                if tenant:
                    # Get property_id from tenant_units - check for active assignment
                    # For short-term rentals, check if move_out_date >= today (allows future rentals)
                    from datetime import date
                    from sqlalchemy import text
                    today = date.today()
                    tenant_unit = db.session.execute(
                        text("""
                            SELECT property_id 
                            FROM tenant_units 
                            WHERE tenant_id = :tenant_id 
                            AND (move_out_date IS NULL OR move_out_date >= :today)
                            ORDER BY move_in_date DESC 
                            LIMIT 1
                        """), {'tenant_id': tenant.id, 'today': today}
                    ).fetchone()
                    
                    if tenant_unit and tenant_unit[0]:
                        property_id = tenant_unit[0]
                        # Show announcements for this property OR announcements with no property_id (global)
                        query = query.filter(
                            or_(
                                Announcement.property_id == property_id,
                                Announcement.property_id.is_(None)
                            )
                        )
                    else:
                        # If tenant has no active assignment, show only global announcements
                        query = query.filter(Announcement.property_id.is_(None))
                else:
                    # If tenant profile doesn't exist, show only global announcements
                    query = query.filter(Announcement.property_id.is_(None))
            except Exception as tenant_error:
                current_app.logger.warning(f"Error filtering announcements for tenant: {str(tenant_error)}")
                # Fallback: show only global announcements if tenant filtering fails
                query = query.filter(Announcement.property_id.is_(None))
        elif user_role_str in ['MANAGER', 'PROPERTY_MANAGER']:
            # Property managers can only see announcements for their current property subdomain
            from routes.auth_routes import get_property_id_from_request
            property_id = get_property_id_from_request()
            
            # If property_id not in request, try to get from JWT token
            if not property_id:
                from flask_jwt_extended import get_jwt
                try:
                    claims = get_jwt()
                    property_id = claims.get('property_id')
                except Exception:
                    pass
            
            if not property_id:
                return property_context_required()
            
            # CRITICAL: Verify property exists and user owns it
            from models.property import Property
            property_obj = Property.query.get(property_id)
            if not property_obj:
                log_property_access_attempt(current_user.id, property_id, action='get_announcements', success=False)
                return property_not_found()
            
            if property_obj.owner_id != current_user.id:
                log_property_access_attempt(current_user.id, property_id, action='get_announcements', success=False)
                return property_access_denied()
            
            # Log successful property access
            log_property_access_attempt(current_user.id, property_id, action='get_announcements', success=True)
            
            # Filter announcements by property_id (include global announcements too)
            query = query.filter(
                or_(
                    Announcement.property_id == property_id,
                    Announcement.property_id.is_(None)  # Global announcements
                )
            )
        # Staff can see all announcements (they work for a specific property via staff.property_id)
        # Note: Staff filtering by property_id could be added if needed
        
        # Apply search filter (use LIKE instead of ILIKE for MySQL compatibility)
        if search:
            from sqlalchemy import func
            search_pattern = f'%{search}%'
            query = query.filter(
                or_(
                    func.lower(Announcement.title).like(func.lower(search_pattern)),
                    func.lower(Announcement.content).like(func.lower(search_pattern))
                )
            )
        
        # Apply type filter
        if announcement_type:
            # Use string comparison since announcement_type is now String type
            query = query.filter(Announcement.announcement_type == announcement_type.lower())
        
        # Apply priority filter
        if priority:
            # Use string comparison since priority is now String type
            query = query.filter(Announcement.priority == priority.lower())
        
        # Note: is_pinned doesn't exist in database, so we skip that filter
        # Order by creation date (newest first) - handle NULL created_at gracefully
        # Use simple ordering without nullslast to avoid SQL syntax errors in MySQL
        try:
            from sqlalchemy import desc
            query = query.order_by(desc(Announcement.created_at))
        except Exception:
            # Fallback: order by id descending if created_at ordering fails
            try:
                query = query.order_by(Announcement.id.desc())
            except Exception:
                # Last resort: no ordering
                pass
        
        # Paginate
        announcements = query.paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        # Safely serialize announcements with error handling
        announcements_list = []
        for ann in announcements.items:
            try:
                announcements_list.append(ann.to_dict(include_author_info=True))
            except Exception as ann_error:
                current_app.logger.warning(f"Error serializing announcement {ann.id}: {str(ann_error)}")
                # Include minimal announcement data if serialization fails
                try:
                    announcements_list.append({
                        'id': ann.id,
                        'title': ann.title,
                        'content': ann.content,
                        'announcement_type': str(ann.announcement_type) if ann.announcement_type else 'general',
                        'priority': str(ann.priority) if ann.priority else 'medium',
                        'property_id': ann.property_id,
                        'published_by': ann.published_by,
                        'is_published': ann.is_published if ann.is_published is not None else False,
                        'created_at': ann.created_at.isoformat() if ann.created_at else None
                    })
                except Exception:
                    # Skip this announcement if even minimal serialization fails
                    continue
        
        return jsonify({
            'announcements': announcements_list,
            'total': announcements.total,
            'pages': announcements.pages,
            'current_page': page,
            'per_page': per_page,
            'has_next': announcements.has_next,
            'has_prev': announcements.has_prev
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Get announcements error: {str(e)}", exc_info=True)
        if current_app.config.get('DEBUG', False):
            return jsonify({
                'error': 'Failed to fetch announcements',
                'details': str(e),
                'type': type(e).__name__
            }), 500
        return jsonify({'error': 'Failed to fetch announcements'}), 500

@announcement_bp.route('/<int:announcement_id>', methods=['GET'])
@jwt_required()
def get_announcement(announcement_id):
    """
    Get announcement by ID
    ---
    tags:
      - Announcements
    summary: Get a specific announcement
    description: Retrieve a specific announcement by ID
    security:
      - Bearer: []
    parameters:
      - in: path
        name: announcement_id
        type: integer
        required: true
        description: The announcement ID
    responses:
      200:
        description: Announcement retrieved successfully
        schema:
          type: object
          properties:
            announcement:
              type: object
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Announcement not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        announcement = Announcement.query.get(announcement_id)
        if not announcement:
            return jsonify({'error': 'Announcement not found'}), 404
        
        if not can_view_announcement(current_user, announcement):
            return jsonify({'error': 'Access denied'}), 403
        
        return jsonify({'announcement': announcement.to_dict(include_author_info=True)}), 200
        
    except Exception as e:
        current_app.logger.error(f"Get announcement error: {str(e)}")
        return jsonify({'error': 'Failed to fetch announcement'}), 500

@announcement_bp.route('/', methods=['POST'])
@jwt_required()
def create_announcement():
    """
    Create announcement
    ---
    tags:
      - Announcements
    summary: Create a new announcement
    description: Create a new announcement. Only property managers and staff can create announcements.
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - title
            - content
          properties:
            title:
              type: string
            content:
              type: string
            announcement_type:
              type: string
              enum: [general, maintenance, emergency, event]
              default: general
            priority:
              type: string
              enum: [low, medium, high, urgent]
              default: medium
            property_id:
              type: integer
            is_published:
              type: boolean
              default: true
            is_pinned:
              type: boolean
              default: false
    responses:
      201:
        description: Announcement created successfully
        schema:
          type: object
          properties:
            message:
              type: string
            announcement:
              type: object
      400:
        description: Validation error
      401:
        description: Unauthorized
      403:
        description: Access denied
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user or not can_manage_announcements(current_user):
            return jsonify({'error': 'Access denied. Only property managers and staff can create announcements.'}), 403
        
        # Get JSON data and handle case where it might be a string
        data = request.get_json()
        
        # If data is a string, try to parse it as JSON
        if isinstance(data, str):
            import json
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return jsonify({'error': 'Invalid JSON format'}), 400
        
        # Ensure data is a dictionary
        if not isinstance(data, dict):
            return jsonify({'error': 'Request body must be a JSON object'}), 400
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Validate required fields
        required_fields = ['title', 'content']
        for field in required_fields:
            if not data.get(field) or not data.get(field).strip():
                return jsonify({'error': f'{field.capitalize()} is required'}), 400
        
        # Validate enums - use string values to avoid enum validation issues
        announcement_type_str = data.get('announcement_type', 'general').lower()
        priority_str = data.get('priority', 'medium').lower()
        
        # Validate enum values
        valid_types = ['general', 'maintenance', 'emergency', 'event']
        valid_priorities = ['low', 'medium', 'high', 'urgent']
        
        if announcement_type_str not in valid_types:
            return jsonify({'error': f'Invalid announcement_type. Must be one of: {", ".join(valid_types)}'}), 400
        
        if priority_str not in valid_priorities:
            return jsonify({'error': f'Invalid priority. Must be one of: {", ".join(valid_priorities)}'}), 400
        
        # Use string values directly (model now uses String instead of Enum)
        # No need to convert to enum objects
        
        # Get property_id from request (for property-specific announcements)
        property_id = data.get('property_id')
        
        # If property_id not provided, try to get from subdomain context
        if not property_id:
            from routes.auth_routes import get_property_id_from_request
            property_id = get_property_id_from_request()
            
            # If still not found, try JWT claims
            if not property_id:
                from flask_jwt_extended import get_jwt
                try:
                    claims = get_jwt()
                    property_id = claims.get('property_id')
                except Exception:
                    pass
        
        if property_id:
            try:
                property_id = int(property_id)
            except (ValueError, TypeError):
                property_id = None
            
            # CRITICAL: Verify property exists and user owns it (for property managers)
            if current_user.is_property_manager():
                from models.property import Property
                property_obj = Property.query.get(property_id)
                if not property_obj:
                    return jsonify({'error': 'Property not found'}), 404
                
                if property_obj.owner_id != current_user.id:
                    return jsonify({
                        'error': 'Access denied. You do not own this property.',
                        'code': 'PROPERTY_ACCESS_DENIED'
                    }), 403
        
        # Get is_published value (default to True)
        is_published = data.get('is_published', True)
        if isinstance(is_published, str):
            is_published = is_published.lower() in ['true', '1', 'yes']
        is_published = bool(is_published) if is_published is not None else True
        
        # Create announcement using published_by (database column name)
        try:
            # Create the announcement object
            announcement = Announcement()
            announcement.title = data['title'].strip()
            announcement.content = data['content'].strip()
            announcement.announcement_type = announcement_type_str  # Use string value directly
            announcement.priority = priority_str  # Use string value directly
            announcement.property_id = property_id  # Can be None for global announcements
            announcement.published_by = current_user.id  # Use published_by (database column)
            announcement.is_published = is_published  # Use is_published (database column)
            # created_at will be set automatically by default
            
            db.session.add(announcement)
            db.session.commit()
        except Exception as create_error:
            db.session.rollback()
            current_app.logger.error(f"Error creating announcement object: {str(create_error)}", exc_info=True)
            if current_app.config.get('DEBUG', False):
                return jsonify({
                    'error': 'Failed to create announcement',
                    'details': str(create_error),
                    'type': type(create_error).__name__
                }), 500
            return jsonify({'error': 'Failed to create announcement. Please check the data and try again.'}), 500
        
        current_app.logger.info(f"Announcement created: {announcement.id} by user {current_user.id}")
        
        # Create notifications for all tenants in the property when announcement is published
        if is_published and property_id:
            try:
                from services.notification_service import NotificationService
                from models.tenant import Tenant
                # Get all tenants for this property
                tenants = Tenant.query.filter_by(property_id=property_id).all()
                for tenant in tenants:
                    try:
                        NotificationService.notify_announcement(announcement, tenant.id)
                    except Exception as tenant_notif_error:
                        current_app.logger.warning(f"Failed to create notification for tenant {tenant.id}: {str(tenant_notif_error)}")
            except Exception as notif_error:
                # Don't fail announcement creation if notification fails
                current_app.logger.warning(f"Failed to create notifications for announcement {announcement.id}: {str(notif_error)}")
        
        return jsonify({
            'message': 'Announcement created successfully',
            'announcement': announcement.to_dict(include_author_info=True)
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Create announcement error: {str(e)}", exc_info=True)
        if current_app.config.get('DEBUG', False):
            return jsonify({
                'error': 'Failed to create announcement',
                'details': str(e),
                'type': type(e).__name__
            }), 500
        return jsonify({'error': 'Failed to create announcement'}), 500

@announcement_bp.route('/<int:announcement_id>', methods=['PUT'])
@jwt_required()
def update_announcement(announcement_id):
    """
    Update announcement
    ---
    tags:
      - Announcements
    summary: Update an existing announcement
    description: Update announcement information. Only property managers and staff can update announcements.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: announcement_id
        type: integer
        required: true
        description: The announcement ID
      - in: body
        name: body
        schema:
          type: object
          properties:
            title:
              type: string
            content:
              type: string
            announcement_type:
              type: string
              enum: [general, maintenance, emergency, event]
            priority:
              type: string
              enum: [low, medium, high, urgent]
            is_published:
              type: boolean
            is_pinned:
              type: boolean
    responses:
      200:
        description: Announcement updated successfully
        schema:
          type: object
          properties:
            message:
              type: string
            announcement:
              type: object
      400:
        description: Validation error
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Announcement not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user or not can_manage_announcements(current_user):
            return jsonify({'error': 'Access denied'}), 403
        
        announcement = Announcement.query.get(announcement_id)
        if not announcement:
            return jsonify({'error': 'Announcement not found'}), 404
        
        # Only the creator or property managers can edit
        # Handle both enum and string values for role
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else ''
        
        if user_role_str not in ['MANAGER', 'PROPERTY_MANAGER'] and announcement.published_by != current_user.id:
            return jsonify({'error': 'You can only edit announcements you created'}), 403
        
        # CRITICAL: For property managers, verify property ownership
        if user_role_str in ['MANAGER', 'PROPERTY_MANAGER']:
            if announcement.property_id:
                from models.property import Property
                property_obj = Property.query.get(announcement.property_id)
                if not property_obj:
                    return jsonify({'error': 'Property not found'}), 404
                
                if property_obj.owner_id != current_user.id:
                    return jsonify({
                        'error': 'Access denied. You do not own this property.',
                        'code': 'PROPERTY_ACCESS_DENIED'
                    }), 403
            else:
                # For global announcements, verify from subdomain context
                from routes.auth_routes import get_property_id_from_request
                property_id = get_property_id_from_request()
                if property_id:
                    from models.property import Property
                    property_obj = Property.query.get(property_id)
                    if property_obj and property_obj.owner_id != current_user.id:
                        return jsonify({
                            'error': 'Access denied. You do not own this property.',
                            'code': 'PROPERTY_ACCESS_DENIED'
                        }), 403
        
        # Get JSON data and handle case where it might be a string
        data = request.get_json()
        
        # If data is a string, try to parse it as JSON
        if isinstance(data, str):
            import json
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return jsonify({'error': 'Invalid JSON format'}), 400
        
        # Ensure data is a dictionary
        if not isinstance(data, dict):
            return jsonify({'error': 'Request body must be a JSON object'}), 400
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Update fields if provided
        if 'title' in data:
            if not data['title'].strip():
                return jsonify({'error': 'Title cannot be empty'}), 400
            announcement.title = data['title'].strip()
        
        if 'content' in data:
            if not data['content'].strip():
                return jsonify({'error': 'Content cannot be empty'}), 400
            announcement.content = data['content'].strip()
        
        if 'announcement_type' in data:
            # Validate and use string value directly
            type_str = str(data['announcement_type']).lower()
            valid_types = ['general', 'maintenance', 'emergency', 'event']
            if type_str not in valid_types:
                return jsonify({'error': f'Invalid announcement type: {data["announcement_type"]}'}), 400
            announcement.announcement_type = type_str
        
        if 'priority' in data:
            # Validate and use string value directly
            priority_str = str(data['priority']).lower()
            valid_priorities = ['low', 'medium', 'high', 'urgent']
            if priority_str not in valid_priorities:
                return jsonify({'error': f'Invalid priority: {data["priority"]}'}), 400
            announcement.priority = priority_str
        
        if 'property_id' in data:
            announcement.property_id = data['property_id'] if data['property_id'] else None
        
        if 'is_published' in data:
            announcement.is_published = bool(data['is_published'])
        
        # Note: is_pinned, send_notification, target_audience, updated_at don't exist in database
        # They are handled as properties in the model for backward compatibility
        db.session.commit()
        
        current_app.logger.info(f"Announcement updated: {announcement_id} by user {current_user.id}")
        
        return jsonify({
            'message': 'Announcement updated successfully',
            'announcement': announcement.to_dict(include_author_info=True)
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update announcement error: {str(e)}")
        return jsonify({'error': 'Failed to update announcement'}), 500

@announcement_bp.route('/<int:announcement_id>', methods=['DELETE'])
@jwt_required()
def delete_announcement(announcement_id):
    """
    Delete announcement
    ---
    tags:
      - Announcements
    summary: Delete an announcement
    description: Delete an announcement (soft delete by setting is_published to False). Only property managers and staff can delete announcements.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: announcement_id
        type: integer
        required: true
        description: The announcement ID
    responses:
      200:
        description: Announcement deleted successfully
        schema:
          type: object
          properties:
            message:
              type: string
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Announcement not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user or not can_manage_announcements(current_user):
            return jsonify({'error': 'Access denied'}), 403
        
        announcement = Announcement.query.get(announcement_id)
        if not announcement:
            return jsonify({'error': 'Announcement not found'}), 404
        
        # Only property managers can delete announcements
        # Staff cannot delete announcements, even their own
        # Handle both enum and string values for role
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else ''
        
        # Only property managers can delete
        if user_role_str not in ['MANAGER', 'PROPERTY_MANAGER']:
            return jsonify({'error': 'Only property managers can delete announcements'}), 403
        
        # CRITICAL: Verify property ownership
        if announcement.property_id:
            from models.property import Property
            property_obj = Property.query.get(announcement.property_id)
            if not property_obj:
                return jsonify({'error': 'Property not found'}), 404
            
            if property_obj.owner_id != current_user.id:
                return jsonify({
                    'error': 'Access denied. You do not own this property.',
                    'code': 'PROPERTY_ACCESS_DENIED'
                }), 403
        else:
            # For global announcements, verify from subdomain context
            from routes.auth_routes import get_property_id_from_request
            property_id = get_property_id_from_request()
            if property_id:
                from models.property import Property
                property_obj = Property.query.get(property_id)
                if property_obj and property_obj.owner_id != current_user.id:
                    return jsonify({
                        'error': 'Access denied. You do not own this property.',
                        'code': 'PROPERTY_ACCESS_DENIED'
                    }), 403
        
        # Soft delete (set is_published to False)
        announcement.is_published = False
        db.session.commit()
        
        current_app.logger.info(f"Announcement deleted: {announcement_id} by user {current_user.id}")
        
        return jsonify({'message': 'Announcement deleted successfully'}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete announcement error: {str(e)}")
        return jsonify({'error': 'Failed to delete announcement'}), 500

@announcement_bp.route('/stats', methods=['GET'])
@jwt_required()
def get_announcement_stats():
    """
    Get announcement statistics
    ---
    tags:
      - Announcements
    summary: Get announcement statistics for dashboards
    description: Retrieve announcement statistics filtered by user's role
    security:
      - Bearer: []
    responses:
      200:
        description: Statistics retrieved successfully
        schema:
          type: object
          properties:
            total:
              type: integer
            published:
              type: integer
            unpublished:
              type: integer
            by_type:
              type: object
            by_priority:
              type: object
      401:
        description: Unauthorized
      404:
        description: User not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Base query based on user role
        base_query = Announcement.query
        
        # Filter by property_id for tenants
        if current_user.role == UserRole.TENANT:
            from models.tenant import Tenant, TenantUnit
            tenant = Tenant.query.filter_by(user_id=current_user.id).first()
            if tenant:
                tenant_unit = TenantUnit.query.filter_by(tenant_id=tenant.id).first()
                if tenant_unit:
                    base_query = base_query.filter(
                        or_(
                            Announcement.property_id == tenant_unit.property_id,
                            Announcement.property_id.is_(None)
                        )
                    )
        
        # Get statistics (using is_published from database)
        total_active = base_query.filter(Announcement.is_published == True).count()
        # Note: is_pinned doesn't exist in database, so total_pinned is always 0
        total_pinned = 0
        
        # This week
        from datetime import timedelta
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        this_week = base_query.filter(
            and_(
                Announcement.is_published == True,
                Announcement.created_at >= week_ago
            )
        ).count()
        
        # By type
        by_type = {}
        # Use string values directly for filtering
        for type_val in ['general', 'maintenance', 'emergency', 'event']:
            count = base_query.filter(
                and_(
                    Announcement.is_published == True,
                    Announcement.announcement_type == type_val
                )
            ).count()
            by_type[type_val] = count
        
        # By priority
        by_priority = {}
        # Use string values directly for filtering
        for priority_val in ['low', 'medium', 'high', 'urgent']:
            count = base_query.filter(
                and_(
                    Announcement.is_published == True,
                    Announcement.priority == priority_val
                )
            ).count()
            by_priority[priority_val] = count
        
        return jsonify({
            'total_active': total_active,
            'total_pinned': total_pinned,
            'this_week': this_week,
            'by_type': by_type,
            'by_priority': by_priority
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Get announcement stats error: {str(e)}")
        return jsonify({'error': 'Failed to fetch statistics'}), 500
