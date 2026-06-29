from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timezone, date
from sqlalchemy import desc, and_, or_

from app import db
from models.request import MaintenanceRequest, RequestStatus, RequestPriority, RequestCategory
from models.user import User, UserRole
from models.tenant import Tenant
from models.property import Unit, Property

request_bp = Blueprint('requests', __name__)

def get_current_user():
    """Helper function to get current user from JWT token."""
    current_user_id = get_jwt_identity()
    if not current_user_id:
        return None
    return User.query.get(current_user_id)

def get_current_tenant():
    """Helper function to get current tenant from JWT token."""
    user = get_current_user()
    if not user:
        return None
    
    # Check if user is a tenant
    user_role = user.role
    if isinstance(user_role, UserRole):
        user_role_str = user_role.value
    elif isinstance(user_role, str):
        user_role_str = user_role.upper()
    else:
        user_role_str = str(user_role).upper() if user_role else 'TENANT'
    
    if user_role_str != 'TENANT':
        return None
    
    # Get tenant profile
    tenant = Tenant.query.filter_by(user_id=user.id).first()
    return tenant

def generate_request_number():
    """Generate unique request number."""
    today = date.today()
    last_request = MaintenanceRequest.query.order_by(MaintenanceRequest.id.desc()).first()
    sequence = (last_request.id + 1) if last_request else 1
    return f"REQ-{today.strftime('%Y%m%d')}-{sequence:04d}"

# =====================================================
# TENANT ROUTES (Tenant can create and view their own requests)
# =====================================================

@request_bp.route('/', methods=['GET'])
@jwt_required()
def get_requests():
    """
    Get maintenance requests
    ---
    tags:
      - Requests
    summary: Get maintenance requests
    description: Retrieve maintenance requests filtered by user role
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
        name: status
        type: string
      - in: query
        name: priority
        type: string
    responses:
      200:
        description: Requests retrieved successfully
        schema:
          type: object
          properties:
            requests:
              type: array
              items:
                type: object
            total:
              type: integer
            pages:
              type: integer
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Get query parameters
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        status = request.args.get('status', type=str)
        category = request.args.get('category', type=str)
        priority = request.args.get('priority', type=str)
        tenant_id = request.args.get('tenant_id', type=int)
        
        # Check user role
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        # CRITICAL: Get property_id from request (subdomain, header, query param, or JWT)
        # This ensures we only return requests for the current property subdomain
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
        
        # Build query based on role
        if user_role_str == 'TENANT':
            # Tenants can only see their own requests for their property
            tenant = get_current_tenant()
            if not tenant:
                return jsonify({'error': 'Tenant profile not found'}), 404
            
            # Filter by tenant_id and property_id (if available)
            query = MaintenanceRequest.query.filter_by(tenant_id=tenant.id)
            if property_id:
                query = query.filter_by(property_id=property_id)
        elif user_role_str in ['MANAGER', 'PROPERTY_MANAGER']:
            # Property managers can see all requests for their property
            if not property_id:
                return jsonify({
                    'error': 'Property context is required. Please access through a property subdomain.',
                    'code': 'PROPERTY_CONTEXT_REQUIRED'
                }), 400
            
            # CRITICAL: Verify property exists and user owns it
            from models.property import Property
            property_obj = Property.query.get(property_id)
            if not property_obj:
                return jsonify({'error': 'Property not found'}), 404
            
            if property_obj.owner_id != current_user.id:
                return jsonify({
                    'error': 'Access denied. You do not own this property.',
                    'code': 'PROPERTY_ACCESS_DENIED'
                }), 403
            
            # Filter by property_id
            query = MaintenanceRequest.query.filter_by(property_id=property_id)
            if tenant_id:
                query = query.filter_by(tenant_id=tenant_id)
        elif user_role_str == 'STAFF':
            # Staff can see requests for their property
            if property_id:
                query = MaintenanceRequest.query.filter_by(property_id=property_id)
                if tenant_id:
                    query = query.filter_by(tenant_id=tenant_id)
            else:
                # If no property_id, staff can see all (fallback for backward compatibility)
                query = MaintenanceRequest.query
                if tenant_id:
                    query = query.filter_by(tenant_id=tenant_id)
        else:
            return jsonify({'error': 'Access denied'}), 403
        
        # Apply filters
        if status:
            query = query.filter(MaintenanceRequest.status == status.lower())
        if category:
            query = query.filter(MaintenanceRequest.category == category.lower())
        if priority:
            query = query.filter(MaintenanceRequest.priority == priority.lower())
        
        # Order by created_at descending (newest first)
        query = query.order_by(desc(MaintenanceRequest.created_at))
        
        # Paginate
        requests = query.paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        # Serialize requests
        requests_list = []
        for req in requests.items:
            try:
                requests_list.append(req.to_dict(
                    include_tenant=(user_role_str != 'TENANT'),
                    include_unit=True,
                    include_assigned_staff=True
                ))
            except Exception as req_error:
                current_app.logger.warning(f"Error serializing request {req.id}: {str(req_error)}")
                continue
        
        return jsonify({
            'requests': requests_list,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': requests.total,
                'pages': requests.pages,
                'has_next': requests.has_next,
                'has_prev': requests.has_prev
            }
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error in get_requests: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@request_bp.route('/', methods=['POST'])
@jwt_required()
def create_request():
    """
    Create maintenance request
    ---
    tags:
      - Requests
    summary: Create a new maintenance request
    description: Create a new maintenance request. Tenant only.
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - subject
            - description
          properties:
            subject:
              type: string
            description:
              type: string
            priority:
              type: string
              enum: [low, medium, high, urgent]
            unit_id:
              type: integer
    responses:
      201:
        description: Request created successfully
        schema:
          type: object
          properties:
            message:
              type: string
            request:
              type: object
      400:
        description: Validation error
      401:
        description: Unauthorized
      403:
        description: Forbidden - Tenant access required
      500:
        description: Server error
    """
    try:
        tenant = get_current_tenant()
        if not tenant:
            return jsonify({'error': 'Tenant profile not found'}), 404
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Validate required fields
        required_fields = ['title', 'description', 'category']
        for field in required_fields:
            if not data.get(field) or not str(data[field]).strip():
                return jsonify({'error': f'{field} is required'}), 400
        
        # Get tenant's current unit
        unit_id = data.get('unit_id')

        if not unit_id:
            # Try to get from tenant's active unit (ORM property)
            if tenant.current_unit:
                unit_id = tenant.current_unit.id

        if not unit_id:
            # Fallback: latest tenant_unit record by created_at
            try:
                from sqlalchemy import text
                latest_tu = db.session.execute(text(
                    """
                    SELECT unit_id 
                    FROM tenant_units 
                    WHERE tenant_id = :tid
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ), {'tid': tenant.id}).first()
                if latest_tu and latest_tu[0]:
                    unit_id = latest_tu[0]
            except Exception as tu_err:
                current_app.logger.warning(f"Fallback tenant_units lookup failed for tenant {tenant.id}: {str(tu_err)}")

        if not unit_id:
            # Last resort fallback: order by id DESC in case created_at is null/absent
            try:
                from sqlalchemy import text
                latest_tu_id = db.session.execute(text(
                    """
                    SELECT unit_id 
                    FROM tenant_units 
                    WHERE tenant_id = :tid
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ), {'tid': tenant.id}).first()
                if latest_tu_id and latest_tu_id[0]:
                    unit_id = latest_tu_id[0]
            except Exception as tu_err:
                current_app.logger.warning(f"ID-based tenant_units lookup failed for tenant {tenant.id}: {str(tu_err)}")

        if not unit_id:
            return jsonify({'error': 'Unit is required. Please specify unit_id or ensure tenant has an active unit.'}), 400
        
        # Verify unit exists (use raw SQL as safety) and fetch property_id
        property_id = None
        try:
            from sqlalchemy import text
            unit_row = db.session.execute(text(
                "SELECT id, property_id FROM units WHERE id = :uid"
            ), {'uid': unit_id}).first()
            if unit_row:
                property_id = unit_row[1]
            else:
                return jsonify({'error': 'Unit not found'}), 404
        except Exception:
            # Fallback to ORM
            unit = Unit.query.get(unit_id)
            if not unit:
                return jsonify({'error': 'Unit not found'}), 404
            property_id = unit.property_id
        
        if not property_id:
            return jsonify({'error': 'Property not found for this unit'}), 404
        
        # Validate category
        category = str(data['category']).lower()
        valid_categories = ['plumbing', 'electrical', 'hvac', 'appliance', 'carpentry', 
                          'painting', 'cleaning', 'pest_control', 'security', 'other']
        if category not in valid_categories:
            return jsonify({'error': f'Invalid category. Must be one of: {", ".join(valid_categories)}'}), 400
        
        # Validate priority
        priority = str(data.get('priority', 'medium')).lower()
        valid_priorities = ['low', 'medium', 'high', 'urgent']
        if priority not in valid_priorities:
            priority = 'medium'
        
        # Generate request number
        request_number = generate_request_number()
        
        # Handle images/attachments (can be JSON string or base64)
        images = None
        if data.get('images'):
            if isinstance(data['images'], list):
                import json
                images = json.dumps(data['images'])
            else:
                images = str(data['images'])
        
        attachments = None
        if data.get('attachments'):
            if isinstance(data['attachments'], list):
                import json
                attachments = json.dumps(data['attachments'])
            else:
                attachments = str(data['attachments'])
        
        # Create maintenance request
        maintenance_request = MaintenanceRequest(
            request_number=request_number,
            tenant_id=tenant.id,
            unit_id=unit_id,
            property_id=property_id,
            title=str(data['title']).strip(),
            description=str(data['description']).strip(),
            category=category,
            priority=priority,
            status='pending',
            images=images,
            attachments=attachments
        )
        
        db.session.add(maintenance_request)
        db.session.commit()
        
        # ============================================================================
        # NOTIFICATION SYSTEM: Send notifications when request is created
        # ============================================================================
        # 1. Notify tenant that their request was successfully created
        # 2. Notify property manager about the new maintenance request
        # ============================================================================
        
        # Create notification for tenant - confirms request submission
        try:
            from services.notification_service import NotificationService
            NotificationService.notify_request_created(maintenance_request)
            current_app.logger.info(f"Created tenant notification for request {maintenance_request.id} (tenant {tenant.id})")
        except Exception as notif_error:
            # Don't fail request creation if notification fails
            current_app.logger.warning(f"Failed to create tenant notification for request {maintenance_request.id}: {str(notif_error)}")
        
        # Create notification for property manager - alerts about new request
        try:
            from services.notification_service import NotificationService
            NotificationService.notify_pm_new_request(maintenance_request)
            current_app.logger.info(f"Created PM notification for request {maintenance_request.id} (property {property_id})")
        except Exception as notif_error:
            # Don't fail request creation if notification fails
            current_app.logger.warning(f"Failed to create PM notification for request {maintenance_request.id}: {str(notif_error)}")
        
        return jsonify({
            'message': 'Maintenance request created successfully',
            'request': maintenance_request.to_dict(include_unit=True)
        }), 201
        
    except ValueError as ve:
        db.session.rollback()
        current_app.logger.error(f"Validation error in create_request: {str(ve)}", exc_info=True)
        return jsonify({'error': str(ve)}), 400
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in create_request: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@request_bp.route('/<int:request_id>', methods=['GET'])
@jwt_required()
def get_request(request_id):
    """
    Get request by ID
    ---
    tags:
      - Requests
    summary: Get a specific maintenance request
    description: Retrieve a specific maintenance request by ID
    security:
      - Bearer: []
    parameters:
      - in: path
        name: request_id
        type: integer
        required: true
        description: The request ID
    responses:
      200:
        description: Request retrieved successfully
        schema:
          type: object
          properties:
            request:
              type: object
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Request not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        maintenance_request = MaintenanceRequest.query.get(request_id)
        if not maintenance_request:
            return jsonify({'error': 'Maintenance request not found'}), 404
        
        # Check access permissions
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        if user_role_str == 'TENANT':
            # Tenants can only see their own requests
            tenant = get_current_tenant()
            if not tenant or maintenance_request.tenant_id != tenant.id:
                return jsonify({'error': 'Access denied'}), 403
        
        return jsonify({
            'request': maintenance_request.to_dict(
                include_tenant=True,
                include_unit=True,
                include_assigned_staff=True
            )
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error in get_request: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@request_bp.route('/<int:request_id>', methods=['PUT'])
@jwt_required()
def update_request(request_id):
    """
    Update maintenance request
    ---
    tags:
      - Requests
    summary: Update a maintenance request
    description: Update a maintenance request. Tenant can update their own, Managers can update any.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: request_id
        type: integer
        required: true
        description: The request ID
      - in: body
        name: body
        schema:
          type: object
          properties:
            subject:
              type: string
            description:
              type: string
            priority:
              type: string
              enum: [low, medium, high, urgent]
            status:
              type: string
            assigned_to:
              type: integer
    responses:
      200:
        description: Request updated successfully
        schema:
          type: object
          properties:
            message:
              type: string
            request:
              type: object
      400:
        description: Validation error
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Request not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        maintenance_request = MaintenanceRequest.query.get(request_id)
        if not maintenance_request:
            return jsonify({'error': 'Maintenance request not found'}), 404
        
        # Check access permissions
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        is_manager = user_role_str in ['MANAGER']
        is_tenant = user_role_str == 'TENANT'
        
        if is_tenant:
            # Tenants can only update their own requests
            tenant = get_current_tenant()
            if not tenant or maintenance_request.tenant_id != tenant.id:
                return jsonify({'error': 'Access denied'}), 403
            
            # Tenants can only update certain fields (title, description, priority if pending)
            if maintenance_request.status != 'pending':
                return jsonify({'error': 'You can only update pending requests'}), 400
        elif not is_manager:
            return jsonify({'error': 'Access denied'}), 403
        
        # CRITICAL: For property managers, verify property ownership
        if is_manager and maintenance_request.property_id:
            from models.property import Property
            property_obj = Property.query.get(maintenance_request.property_id)
            if not property_obj:
                return jsonify({'error': 'Property not found'}), 404
            
            if property_obj.owner_id != current_user.id:
                return jsonify({
                    'error': 'Access denied. You do not own this property.',
                    'code': 'PROPERTY_ACCESS_DENIED'
                }), 403
        
        data = request.get_json() or {}
        
        # Update fields based on role
        if is_tenant:
            # Tenants can update: title, description, priority, images, attachments
            if 'title' in data:
                maintenance_request.title = str(data['title']).strip()
            if 'description' in data:
                maintenance_request.description = str(data['description']).strip()
            if 'priority' in data:
                priority = str(data['priority']).lower()
                if priority in ['low', 'medium', 'high', 'urgent']:
                    maintenance_request.priority = priority
            if 'images' in data:
                if isinstance(data['images'], list):
                    import json
                    maintenance_request.images = json.dumps(data['images'])
                else:
                    maintenance_request.images = str(data['images']) if data['images'] else None
            if 'attachments' in data:
                if isinstance(data['attachments'], list):
                    import json
                    maintenance_request.attachments = json.dumps(data['attachments'])
                else:
                    maintenance_request.attachments = str(data['attachments']) if data['attachments'] else None
        else:
            # Managers can update: status, assigned_to, scheduled_date, work_notes, resolution_notes
            old_status = None
            if 'status' in data:
                status = str(data['status']).lower()
                if status in ['pending', 'in_progress', 'completed', 'cancelled', 'on_hold']:
                    old_status = maintenance_request.status
                    maintenance_request.status = status
                    
                    # Handle status-specific updates
                    if status == 'completed':
                        maintenance_request.actual_completion = datetime.now(timezone.utc)
                        if not maintenance_request.resolution_notes and data.get('resolution_notes'):
                            maintenance_request.resolution_notes = str(data['resolution_notes']).strip()
                    elif status == 'in_progress' and not maintenance_request.assigned_to:
                        # Auto-assign if not already assigned
                        if data.get('assigned_to'):
                            maintenance_request.assigned_to = data['assigned_to']
            
            if 'assigned_to' in data:
                old_assigned_to = maintenance_request.assigned_to
                maintenance_request.assigned_to = data['assigned_to'] if data['assigned_to'] else None
                if maintenance_request.assigned_to and maintenance_request.status == 'pending':
                    maintenance_request.status = 'in_progress'
                
                # Notify newly assigned staff member
                if maintenance_request.assigned_to and maintenance_request.assigned_to != old_assigned_to:
                    try:
                        from services.notification_service import NotificationService
                        from models.staff import Staff
                        staff = Staff.query.get(maintenance_request.assigned_to)
                        if staff and staff.user_id:
                            NotificationService.notify_staff_request_assigned(maintenance_request, staff.user_id)
                    except Exception as notif_error:
                        current_app.logger.warning(f"Failed to create notification for request {maintenance_request.id}: {str(notif_error)}")
            
            if 'scheduled_date' in data:
                if data['scheduled_date']:
                    try:
                        maintenance_request.scheduled_date = datetime.fromisoformat(
                            data['scheduled_date'].replace('Z', '+00:00')
                        )
                    except (ValueError, TypeError):
                        pass
                else:
                    maintenance_request.scheduled_date = None
            
            if 'estimated_completion' in data:
                if data['estimated_completion']:
                    try:
                        maintenance_request.estimated_completion = datetime.fromisoformat(
                            data['estimated_completion'].replace('Z', '+00:00')
                        )
                    except (ValueError, TypeError):
                        pass
                else:
                    maintenance_request.estimated_completion = None
            
            if 'work_notes' in data:
                maintenance_request.work_notes = str(data['work_notes']).strip() if data['work_notes'] else None
            
            if 'resolution_notes' in data:
                maintenance_request.resolution_notes = str(data['resolution_notes']).strip() if data['resolution_notes'] else None
        
        db.session.commit()
        
        # Create notifications based on status changes
        try:
            from services.notification_service import NotificationService
            from models.staff import Staff
            if 'status' in data:
                new_status = str(data['status']).lower()
                # old_status was already captured earlier in the code when status was changed
                
                if new_status == 'completed':
                    # Notify tenant that request is completed
                    NotificationService.notify_request_completed(maintenance_request)
                    # Also notify assigned staff
                    if maintenance_request.assigned_to:
                        staff = Staff.query.get(maintenance_request.assigned_to)
                        if staff and staff.user_id:
                            NotificationService.notify_staff_request_updated(maintenance_request, staff.user_id)
                
                elif new_status == 'cancelled':
                    # Notify tenant that request is cancelled/rejected
                    rejection_reason = None
                    if maintenance_request.work_notes:
                        # Extract rejection reason from work_notes if it starts with "Rejected:"
                        if 'Rejected:' in maintenance_request.work_notes:
                            parts = maintenance_request.work_notes.split('Rejected:')
                            if len(parts) > 1:
                                rejection_reason = parts[1].strip()
                    NotificationService.notify_request_cancelled(maintenance_request, reason=rejection_reason)
                    # Also notify assigned staff if there was one
                    if maintenance_request.assigned_to:
                        staff = Staff.query.get(maintenance_request.assigned_to)
                        if staff and staff.user_id:
                            NotificationService.notify_staff_request_updated(maintenance_request, staff.user_id)
                
                elif new_status == 'in_progress':
                    # Notify tenant that request is approved/in progress
                    NotificationService.notify_request_approved(maintenance_request)
                    # If staff is assigned, also send assignment notification
                    if maintenance_request.assigned_to:
                        NotificationService.notify_request_assigned(maintenance_request)
                        # Notify assigned staff
                        staff = Staff.query.get(maintenance_request.assigned_to)
                        if staff and staff.user_id:
                            NotificationService.notify_staff_request_assigned(maintenance_request, staff.user_id)
                else:
                    # Other status changes (pending, on_hold, etc.)
                    NotificationService.notify_request_updated(maintenance_request)
                    # Also notify assigned staff if status changed
                    if maintenance_request.assigned_to:
                        staff = Staff.query.get(maintenance_request.assigned_to)
                        if staff and staff.user_id:
                            NotificationService.notify_staff_request_updated(maintenance_request, staff.user_id)
            
            elif 'assigned_to' in data and maintenance_request.assigned_to:
                # Staff assignment (without status change)
                NotificationService.notify_request_assigned(maintenance_request)
                # Notify assigned staff
                staff = Staff.query.get(maintenance_request.assigned_to)
                if staff and staff.user_id:
                    NotificationService.notify_staff_request_assigned(maintenance_request, staff.user_id)
            else:
                # Other field updates (work_notes, scheduled_date, etc.)
                NotificationService.notify_request_updated(maintenance_request)
                # Also notify assigned staff if other fields were updated
                if maintenance_request.assigned_to:
                    staff = Staff.query.get(maintenance_request.assigned_to)
                    if staff and staff.user_id:
                        NotificationService.notify_staff_request_updated(maintenance_request, staff.user_id)
        except Exception as notif_error:
            current_app.logger.warning(f"Failed to create notification for request {maintenance_request.id}: {str(notif_error)}")
        
        return jsonify({
            'message': 'Maintenance request updated successfully',
            'request': maintenance_request.to_dict(
                include_tenant=True,
                include_unit=True,
                include_assigned_staff=True
            )
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in update_request: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@request_bp.route('/<int:request_id>', methods=['DELETE'])
@jwt_required()
def delete_request(request_id):
    """
    Delete maintenance request
    ---
    tags:
      - Requests
    summary: Delete a maintenance request
    description: Delete a maintenance request. Tenant can delete their own pending requests only.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: request_id
        type: integer
        required: true
        description: The request ID
    responses:
      200:
        description: Request deleted successfully
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
        description: Request not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        maintenance_request = MaintenanceRequest.query.get(request_id)
        if not maintenance_request:
            return jsonify({'error': 'Maintenance request not found'}), 404
        
        # Check access permissions
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        is_manager = user_role_str in ['MANAGER']
        is_tenant = user_role_str == 'TENANT'
        
        if is_tenant:
            # Tenants can only delete their own pending requests
            tenant = get_current_tenant()
            if not tenant or maintenance_request.tenant_id != tenant.id:
                return jsonify({'error': 'Access denied'}), 403
            
            if maintenance_request.status != 'pending':
                return jsonify({'error': 'You can only delete pending requests'}), 400
        elif not is_manager:
            return jsonify({'error': 'Access denied'}), 403
        
        # CRITICAL: For property managers, verify property ownership
        if is_manager and maintenance_request.property_id:
            from models.property import Property
            property_obj = Property.query.get(maintenance_request.property_id)
            if not property_obj:
                return jsonify({'error': 'Property not found'}), 404
            
            if property_obj.owner_id != current_user.id:
                return jsonify({
                    'error': 'Access denied. You do not own this property.',
                    'code': 'PROPERTY_ACCESS_DENIED'
                }), 403
        
        db.session.delete(maintenance_request)
        db.session.commit()
        
        return jsonify({'message': 'Maintenance request deleted successfully'}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in delete_request: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@request_bp.route('/<int:request_id>/feedback', methods=['POST'])
@jwt_required()
def add_feedback(request_id):
    """
    Add request feedback
    ---
    tags:
      - Requests
    summary: Add tenant feedback to a completed request
    description: Add tenant feedback to a completed request
    security:
      - Bearer: []
    parameters:
      - in: path
        name: request_id
        type: integer
        required: true
        description: The request ID
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - rating
            - comment
          properties:
            rating:
              type: integer
              minimum: 1
              maximum: 5
            comment:
              type: string
    responses:
      201:
        description: Feedback added successfully
        schema:
          type: object
          properties:
            message:
              type: string
            feedback:
              type: object
      400:
        description: Validation error
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Request not found
      500:
        description: Server error
    """
    try:
        tenant = get_current_tenant()
        if not tenant:
            return jsonify({'error': 'Tenant profile not found'}), 404
        
        maintenance_request = MaintenanceRequest.query.get(request_id)
        if not maintenance_request:
            return jsonify({'error': 'Maintenance request not found'}), 404
        
        # Verify tenant owns this request
        if maintenance_request.tenant_id != tenant.id:
            return jsonify({'error': 'Access denied'}), 403
        
        # Only allow feedback on completed requests
        if maintenance_request.status != 'completed':
            return jsonify({'error': 'You can only provide feedback on completed requests'}), 400
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        rating = data.get('rating')
        if rating is None:
            return jsonify({'error': 'Rating is required'}), 400
        
        # Validate rating (1-5)
        try:
            rating = int(rating)
            if rating < 1 or rating > 5:
                return jsonify({'error': 'Rating must be between 1 and 5'}), 400
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid rating format'}), 400
        
        feedback_text = data.get('feedback', '').strip() if data.get('feedback') else None
        
        maintenance_request.add_tenant_feedback(rating, feedback_text)
        
        return jsonify({
            'message': 'Feedback submitted successfully',
            'request': maintenance_request.to_dict(include_unit=True)
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in add_feedback: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500
