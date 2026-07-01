from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from datetime import datetime, timezone
from sqlalchemy import desc, and_, or_

from app import db
from models.chat import Chat, Message, ChatStatus, SenderType
from models.user import User, UserRole
from models.tenant import Tenant
from models.property import Property

chat_bp = Blueprint('chats', __name__)

from models.user import User

def is_super_admin(user_id):
    if not user_id: return False
    user = User.query.get(user_id)
    return user and getattr(user, 'role', '') == 'ADMIN'


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
    
    user_role = user.role
    if isinstance(user_role, UserRole):
        user_role_str = user_role.value
    elif isinstance(user_role, str):
        user_role_str = user_role.upper()
    else:
        user_role_str = str(user_role).upper() if user_role else 'TENANT'
    
    if user_role_str != 'TENANT':
        return None
    
    tenant = Tenant.query.filter_by(user_id=user.id).first()
    return tenant

def get_property_id_from_request(data=None):
    """Get property_id from request."""
    try:
        # Check query parameter
        property_id = request.args.get('property_id', type=int)
        if property_id:
            return property_id
        
        # Check header
        property_id = request.headers.get('X-Property-ID', type=int)
        if property_id:
            return property_id
        
        # Check request body
        if data:
            property_id = data.get('property_id')
            if property_id:
                try:
                    return int(property_id)
                except (ValueError, TypeError):
                    pass
        
        # Try to get from JWT claims
        try:
            claims = get_jwt()
            if claims:
                property_id = claims.get('property_id')
                if property_id:
                    return int(property_id)
        except Exception:
            pass
        
        return None
    except Exception as e:
        current_app.logger.warning(f"Error getting property_id from request: {str(e)}")
        return None

def staff_belongs_to_property(user_id, property_id):
    """Check if a staff user belongs to a specific property."""
    try:
        if not user_id or not property_id:
            current_app.logger.warning(f"Invalid parameters for staff_belongs_to_property: user_id={user_id}, property_id={property_id}")
            return False
        
        # Convert to int safely
        try:
            user_id_int = int(user_id)
            property_id_int = int(property_id)
        except (ValueError, TypeError) as conv_err:
            current_app.logger.error(f"Error converting IDs to int: user_id={user_id}, property_id={property_id}, error={str(conv_err)}")
            return False
        
        from sqlalchemy import text
        try:
            staff = db.session.execute(text(
                "SELECT id, property_id FROM staff WHERE user_id = :user_id AND property_id = :property_id LIMIT 1"
            ), {
                'user_id': user_id_int,
                'property_id': property_id_int
            }).first()
            
            result = staff is not None
            if result:
                current_app.logger.info(f"Staff {user_id_int} verified for property {property_id_int}")
            else:
                current_app.logger.warning(f"Staff {user_id_int} NOT found for property {property_id_int}")
            return result
        except Exception as db_err:
            current_app.logger.error(f"Database error checking staff property: {str(db_err)}", exc_info=True)
            # Re-raise to be caught by outer handler
            raise
    except Exception as e:
        current_app.logger.error(f"Error checking staff property: {str(e)}", exc_info=True)
        # Return False on error to fail securely
        return False

@chat_bp.route('/', methods=['GET'])
@jwt_required()
def get_chats():
    """
    Get chats
    ---
    tags:
      - Chat
    summary: Get all chats for the current user
    description: Retrieve all chats for the current user (tenant or property manager)
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
    responses:
      200:
        description: Chats retrieved successfully
        schema:
          type: object
          properties:
            chats:
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
        # Log the request for debugging
        current_app.logger.info(f"get_chats called - Origin: {request.headers.get('Origin')}, Method: {request.method}")
        
        current_user = get_current_user()
        if not current_user:
            current_app.logger.warning("get_chats: User not found")
            response = jsonify({'error': 'User not found'})
            response.status_code = 404
            return response
        
        current_app.logger.info(f"get_chats: User {current_user.id} (role: {current_user.role})")
        
        # Determine user role
        try:
            user_role = current_user.role
            if isinstance(user_role, UserRole):
                user_role_str = user_role.value
            elif isinstance(user_role, str):
                user_role_str = user_role.upper()
            else:
                user_role_str = str(user_role).upper() if user_role else 'TENANT'
            current_app.logger.info(f"get_chats: User role determined as: {user_role_str}")
        except Exception as role_err:
            current_app.logger.error(f"Error determining user role: {str(role_err)}", exc_info=True)
            response = jsonify({'error': 'Error determining user role'})
            response.status_code = 500
            return response
        
        # Get query parameters
        try:
            status = request.args.get('status', 'active')
            property_id = get_property_id_from_request()
            current_app.logger.info(f"get_chats: property_id={property_id}, status={status}")
        except Exception as param_err:
            current_app.logger.error(f"Error getting parameters: {str(param_err)}", exc_info=True)
            response = jsonify({'error': 'Error getting request parameters'})
            response.status_code = 500
            return response
        
        if user_role_str == 'TENANT':
            # Tenants see their own chats, but only for their property
            tenant = get_current_tenant()
            if not tenant:
                return jsonify({'error': 'Tenant profile not found'}), 404
            
            # CRITICAL: Only show chats for the tenant's property
            # This ensures tenants from different properties can't see each other's chats
            query = Chat.query.filter_by(
                tenant_id=tenant.id,
                property_id=tenant.property_id  # Enforce property isolation
            )
            if status:
                query = query.filter_by(status=status)
            
            chats = query.order_by(desc(Chat.last_message_at), desc(Chat.created_at)).all()
            
            chats_list = []
            for chat in chats:
                try:
                    # Update chat subject if it's still a default value or if it needs to be refreshed
                    should_update_subject = False
                    if chat.subject and chat.subject.lower() in ['new inquiry', 'new conversation']:
                        should_update_subject = True
                    else:
                        # Also update if subject seems incorrect (too short or doesn't match manager's name)
                        try:
                            property_obj = Property.query.get(chat.property_id)
                            if property_obj and property_obj.owner_id:
                                manager = User.query.get(property_obj.owner_id)
                                if manager:
                                    # Use User model's full_name property for consistent formatting
                                    manager_name = manager.full_name if hasattr(manager, 'full_name') else ''
                                    if not manager_name:
                                        first_name = getattr(manager, 'first_name', '') or ''
                                        last_name = getattr(manager, 'last_name', '') or ''
                                        manager_name = f"{first_name} {last_name}".strip()
                                    
                                    # If still no name, try email
                                    if not manager_name:
                                        email = getattr(manager, 'email', '')
                                        if email:
                                            manager_name = email.split('@')[0].replace('.', ' ').title()
                                        else:
                                            manager_name = f"Manager {manager.id}"
                                    
                                    # Check if current subject doesn't match manager's name
                                    if chat.subject != manager_name:
                                        should_update_subject = True
                        except Exception:
                            pass
                    
                    if should_update_subject:
                        try:
                            # Get property manager's name
                            property_obj = Property.query.get(chat.property_id)
                            if property_obj and property_obj.owner_id:
                                manager = User.query.get(property_obj.owner_id)
                                if manager:
                                    # Use User model's full_name property for consistent formatting
                                    manager_name = manager.full_name if hasattr(manager, 'full_name') else ''
                                    if not manager_name:
                                        first_name = getattr(manager, 'first_name', '') or ''
                                        last_name = getattr(manager, 'last_name', '') or ''
                                        manager_name = f"{first_name} {last_name}".strip()
                                    
                                    # If still no name, try email
                                    if not manager_name:
                                        email = getattr(manager, 'email', '')
                                        if email:
                                            manager_name = email.split('@')[0].replace('.', ' ').title()
                                        else:
                                            manager_name = f"Manager {manager.id}"
                                    
                                    # Update the chat subject
                                    chat.subject = manager_name
                                    db.session.commit()
                                    current_app.logger.info(f"Updated chat {chat.id} subject to {manager_name}")
                        except Exception as update_error:
                            current_app.logger.warning(f"Error updating chat {chat.id} subject: {str(update_error)}")
                            db.session.rollback()
                    
                    chat_dict = chat.to_dict(include_messages=False, include_property=True, include_last_message=True)
                    # Get unread count for tenant
                    chat_dict['unread_count'] = chat.get_unread_count(current_user.id, 'tenant')
                    chats_list.append(chat_dict)
                except Exception as e:
                    current_app.logger.warning(f"Error serializing chat {chat.id}: {str(e)}")
                    continue
            
            return jsonify({
                'chats': chats_list,
                'total': len(chats_list)
            }), 200
        
        elif user_role_str in ['MANAGER']:
            # Property managers see chats for their property
            # CRITICAL: Must use property_id from subdomain, not auto-detect from owned properties
            # This ensures they only see chats for the property subdomain they're currently accessing
            
            # If property_id not in request, try to get from JWT token
            if not property_id:
                from flask_jwt_extended import get_jwt
                try:
                    claims = get_jwt()
                    property_id = claims.get('property_id')
                except Exception:
                    pass
            
            if not property_id:
                return jsonify({
                    'error': 'Property context is required. Please access through a property subdomain.',
                    'code': 'PROPERTY_CONTEXT_REQUIRED'
                }), 400
            
            # Verify property exists and user is the manager
            property_obj = Property.query.get(property_id)
            if not property_obj:
                return jsonify({'error': 'Property not found'}), 404
            
            if property_obj.owner_id != current_user.id and not is_super_admin(current_user.id):
                return jsonify({
                    'error': 'Access denied. You do not own this property.',
                    'code': 'PROPERTY_ACCESS_DENIED'
                }), 403
            
            # Get both tenant and staff chats for this property
            from sqlalchemy import or_
            query = Chat.query.filter_by(property_id=property_id)
            if status:
                query = query.filter_by(status=status)
            
            chats = query.order_by(desc(Chat.last_message_at), desc(Chat.created_at)).all()
            
            # Also get all staff for this property to ensure they have chat entries
            from models.staff import Staff
            all_staff = Staff.query.filter_by(property_id=property_id).all()
            staff_with_chats = {chat.staff_id for chat in chats if chat.staff_id}
            
            # Create chat entries for staff that don't have one yet
            for staff in all_staff:
                if staff.id not in staff_with_chats:
                    try:
                        # Get staff name for subject
                        staff_name = 'Staff'
                        if staff.user:
                            first_name = getattr(staff.user, 'first_name', '') or ''
                            last_name = getattr(staff.user, 'last_name', '') or ''
                            if first_name or last_name:
                                staff_name = f"{first_name} {last_name}".strip()
                            else:
                                email = getattr(staff.user, 'email', '')
                                if email:
                                    staff_name = email.split('@')[0].replace('.', ' ').title()
                                else:
                                    staff_name = f"Staff {staff.id}"
                        
                        # Create new chat for this staff
                        new_chat = Chat(
                            property_id=property_id,
                            staff_id=staff.id,
                            subject=staff_name,
                            status='active'
                        )
                        db.session.add(new_chat)
                        db.session.commit()
                        chats.append(new_chat)
                        current_app.logger.info(f"Created chat entry for staff {staff.id} ({staff_name})")
                    except Exception as create_err:
                        current_app.logger.warning(f"Error creating chat for staff {staff.id}: {str(create_err)}")
                        db.session.rollback()
            
            # Re-query to get all chats including newly created ones
            chats = Chat.query.filter_by(property_id=property_id).order_by(desc(Chat.last_message_at), desc(Chat.created_at)).all()
            
            chats_list = []
            for chat in chats:
                try:
                    # Update chat subject if needed
                    should_update = False
                    if chat.subject:
                        subject_lower = chat.subject.lower()
                        if subject_lower in ['new inquiry', 'new conversation']:
                            should_update = True
                    
                    if should_update:
                        try:
                            if chat.tenant_id and chat.tenant and chat.tenant.user:
                                # Update for tenant chat
                                tenant_user = chat.tenant.user
                                first_name = getattr(tenant_user, 'first_name', '') or ''
                                last_name = getattr(tenant_user, 'last_name', '') or ''
                                if first_name or last_name:
                                    tenant_name = f"{first_name} {last_name}".strip()
                                else:
                                    email = getattr(tenant_user, 'email', '')
                                    if email:
                                        tenant_name = email.split('@')[0].replace('.', ' ').title()
                                    else:
                                        tenant_name = f"Tenant {chat.tenant.id}"
                                chat.subject = tenant_name
                            elif chat.staff_id and chat.staff and chat.staff.user:
                                # Update for staff chat
                                staff_user = chat.staff.user
                                first_name = getattr(staff_user, 'first_name', '') or ''
                                last_name = getattr(staff_user, 'last_name', '') or ''
                                if first_name or last_name:
                                    staff_name = f"{first_name} {last_name}".strip()
                                else:
                                    email = getattr(staff_user, 'email', '')
                                    if email:
                                        staff_name = email.split('@')[0].replace('.', ' ').title()
                                    else:
                                        staff_name = f"Staff {chat.staff.id}"
                                chat.subject = staff_name
                            
                            db.session.commit()
                        except Exception as update_error:
                            current_app.logger.warning(f"Error updating chat {chat.id} subject: {str(update_error)}")
                            db.session.rollback()
                    
                    # Include tenant or staff info based on chat type
                    if chat.tenant_id:
                        chat_dict = chat.to_dict(include_messages=False, include_tenant=True, include_last_message=True)
                    elif chat.staff_id:
                        chat_dict = chat.to_dict(include_messages=False, include_staff=True, include_last_message=True)
                        # Ensure staff data is properly structured
                        if chat.staff and chat.staff.user:
                            if 'staff' not in chat_dict or not chat_dict.get('staff'):
                                chat_dict['staff'] = {}
                            if not chat_dict['staff'].get('user'):
                                chat_dict['staff']['user'] = {
                                    'id': chat.staff.user.id,
                                    'first_name': getattr(chat.staff.user, 'first_name', '') or '',
                                    'last_name': getattr(chat.staff.user, 'last_name', '') or '',
                                    'email': getattr(chat.staff.user, 'email', '') or ''
                                }
                            # Ensure name field exists
                            if not chat_dict['staff'].get('name'):
                                first_name = chat_dict['staff']['user'].get('first_name', '') or ''
                                last_name = chat_dict['staff']['user'].get('last_name', '') or ''
                                if first_name or last_name:
                                    chat_dict['staff']['name'] = f"{first_name} {last_name}".strip()
                                else:
                                    email = chat_dict['staff']['user'].get('email', '')
                                    if email:
                                        chat_dict['staff']['name'] = email.split('@')[0].replace('.', ' ').title()
                                    else:
                                        chat_dict['staff']['name'] = f"Staff {chat.staff.id}"
                    else:
                        chat_dict = chat.to_dict(include_messages=False, include_last_message=True)
                    
                    # Get unread count for property manager
                    chat_dict['unread_count'] = chat.get_unread_count(current_user.id, 'property_manager')
                    chats_list.append(chat_dict)
                except Exception as e:
                    current_app.logger.warning(f"Error serializing chat {chat.id}: {str(e)}")
                    continue
            
            return jsonify({
                'chats': chats_list,
                'total': len(chats_list)
            }), 200
        
        elif user_role_str == 'STAFF':
            # Staff can only chat with property manager, not tenants
            # Create a single chat entry representing communication with the property manager
            current_app.logger.info(f"get_chats: Processing STAFF request - Staff to Property Manager only (user_id={current_user.id}, property_id={property_id})")
            
            # Initialize chats_list early to avoid undefined variable errors
            chats_list = []
            
            try:
                # If property_id not in request, try to get from JWT token
                if not property_id:
                    from flask_jwt_extended import get_jwt
                    try:
                        claims = get_jwt()
                        property_id = claims.get('property_id')
                        current_app.logger.info(f"get_chats: Got property_id from JWT: {property_id}")
                    except Exception as jwt_err:
                        current_app.logger.warning(f"Error getting property_id from JWT: {str(jwt_err)}")
                        pass
                
                if not property_id:
                    current_app.logger.warning("get_chats: No property_id found")
                    response = jsonify({
                        'error': 'Property context is required. Please access through a property subdomain.',
                        'code': 'PROPERTY_CONTEXT_REQUIRED'
                    })
                    response.status_code = 400
                    return response
                
                # Verify property exists and staff belongs to it
                try:
                    property_obj = Property.query.get(property_id)
                    if not property_obj:
                        current_app.logger.warning(f"Property {property_id} not found")
                        response = jsonify({'error': 'Property not found'})
                        response.status_code = 404
                        return response
                    current_app.logger.info(f"Property {property_id} found: {getattr(property_obj, 'name', 'N/A')}")
                except Exception as prop_err:
                    current_app.logger.error(f"Error querying property {property_id}: {str(prop_err)}", exc_info=True)
                    response = jsonify({'error': 'Error verifying property access'})
                    response.status_code = 500
                    return response
                
                # Get property manager
                manager = None
                try:
                    if property_obj.owner_id:
                        manager = User.query.get(property_obj.owner_id)
                        if manager:
                            current_app.logger.info(f"Property manager found: {manager.id} ({getattr(manager, 'email', 'N/A')})")
                        else:
                            current_app.logger.warning(f"Property manager with ID {property_obj.owner_id} not found")
                    else:
                        current_app.logger.warning(f"Property {property_id} has no owner_id")
                except Exception as manager_err:
                    current_app.logger.error(f"Error getting property manager: {str(manager_err)}", exc_info=True)
                
                if not manager:
                    current_app.logger.error(f"Cannot proceed: Property manager not found for property {property_id}")
                    response = jsonify({'error': 'Property manager not found'})
                    response.status_code = 404
                    return response
                
                # Check if staff belongs to this property (optional check - can be lenient for now)
                try:
                    staff_belongs = staff_belongs_to_property(current_user.id, property_id)
                    if not staff_belongs:
                        current_app.logger.warning(
                            f"Staff user {current_user.id} does not belong to property {property_id}, but allowing access"
                        )
                except Exception as staff_check_err:
                    current_app.logger.warning(f"Error checking staff property membership: {str(staff_check_err)}")
                    # Continue anyway
                
                # Create a virtual chat entry for staff-manager communication
                # Find or create a chat that represents staff-manager communication
                # We'll use the first tenant chat as a container, or create a special one
                # For now, we'll create a virtual chat entry
                
                # Get manager's name
                manager_first_name = getattr(manager, 'first_name', '') or ''
                manager_last_name = getattr(manager, 'last_name', '') or ''
                if manager_first_name or manager_last_name:
                    manager_name = f"{manager_first_name} {manager_last_name}".strip()
                else:
                    manager_email = getattr(manager, 'email', '')
                    if manager_email:
                        manager_name = manager_email.split('@')[0].replace('.', ' ').title()
                    else:
                        manager_name = "Property Manager"
                
                # Find the most recent chat with property_manager messages for this property
                # We'll use this as the staff-manager chat thread
                # Message is already imported from models.chat
                
                # Find chats that have property_manager messages from staff or manager (NOT from tenants)
                # Staff-manager chats should only include messages where sender is property_manager
                # AND the sender_id is either the property manager OR a staff member
                try:
                    # Get all staff user_ids for this property
                    from models.staff import Staff
                    staff_user_ids = []
                    try:
                        staff_members = Staff.query.filter_by(property_id=property_id).all()
                        staff_user_ids = [staff.user_id for staff in staff_members if staff.user_id]
                        current_app.logger.info(f"Found {len(staff_user_ids)} staff members for property {property_id}")
                    except Exception as staff_query_err:
                        current_app.logger.warning(f"Error querying staff: {str(staff_query_err)}")
                    
                    # Include property manager's user_id
                    manager_user_id = manager.id
                    allowed_sender_ids = [manager_user_id] + staff_user_ids
                    
                    # Find chats that have property_manager messages from staff/manager AND have NO tenant messages
                    # This ensures we only get staff-manager chats, not tenant-manager chats
                    recent_manager_chat = None
                    try:
                        # First, get all chat IDs that have tenant messages (we want to exclude these)
                        chats_with_tenant_messages = db.session.query(Message.chat_id).filter(
                            Message.sender_type == 'tenant'
                        ).distinct().subquery()
                        
                        # Query chats that:
                        # 1. Have messages from property_manager where sender is manager or staff
                        # 2. Do NOT have any tenant messages
                        chats_with_staff_messages = db.session.query(Chat).join(Message).filter(
                            and_(
                                Chat.property_id == property_id,
                                Message.sender_type == 'property_manager',
                                Message.sender_id.in_(allowed_sender_ids),
                                ~Chat.id.in_(db.session.query(chats_with_tenant_messages.c.chat_id))  # Exclude chats with tenant messages
                            )
                        ).distinct().order_by(desc(Message.created_at)).first()
                        
                        if chats_with_staff_messages:
                            recent_manager_chat = chats_with_staff_messages
                            current_app.logger.info(f"Found staff-manager chat {recent_manager_chat.id} for property {property_id} (no tenant messages)")
                        else:
                            # If no chat found without tenant messages, try finding chats with only staff/manager messages
                            # by checking if the chat has any tenant messages
                            all_chats_with_staff = db.session.query(Chat).join(Message).filter(
                                and_(
                                    Chat.property_id == property_id,
                                    Message.sender_type == 'property_manager',
                                    Message.sender_id.in_(allowed_sender_ids)
                                )
                            ).distinct().all()
                            
                            # Filter to only chats that have NO tenant messages
                            for candidate_chat in all_chats_with_staff:
                                tenant_message_count = Message.query.filter(
                                    and_(
                                        Message.chat_id == candidate_chat.id,
                                        Message.sender_type == 'tenant'
                                    )
                                ).count()
                                
                                if tenant_message_count == 0:
                                    recent_manager_chat = candidate_chat
                                    current_app.logger.info(f"Found staff-manager chat {recent_manager_chat.id} (verified no tenant messages)")
                                    break
                            
                    except Exception as chat_query_err:
                        current_app.logger.warning(f"Error querying staff-manager chats: {str(chat_query_err)}", exc_info=True)
                    
                    # If still no staff-manager chat found, don't use fallback - return virtual chat instead
                    # This prevents showing tenant-manager conversations to staff
                    if not recent_manager_chat:
                        current_app.logger.info(f"No staff-manager chat found (only tenant-manager chats exist), returning virtual chat")
                    
                    # If no chat exists, we'll create a virtual one (staff can't send until manager creates a chat)
                    if not recent_manager_chat:
                        # Create a virtual chat entry - staff will see this but can't send until manager creates a chat
                        virtual_chat = {
                            'id': None,  # No real chat ID - staff can't send until manager creates a chat
                            'property_id': property_id,
                            'subject': manager_name,
                            'status': 'active',
                            'last_message_at': None,
                            'created_at': datetime.now(timezone.utc).isoformat(),
                            'updated_at': datetime.now(timezone.utc).isoformat(),
                            'unread_count': 0,
                            'messages': [],
                            'property': {
                                'id': property_id,
                                'name': getattr(property_obj, 'name', 'Property'),
                                'manager': {
                                    'id': manager.id,
                                    'name': manager_name,
                                    'first_name': manager_first_name,
                                    'last_name': manager_last_name,
                                    'email': getattr(manager, 'email', '')
                                }
                            },
                            'is_staff_manager_chat': True  # Flag to indicate this is staff-manager chat
                        }
                        
                        chats_list = [virtual_chat]
                    else:
                        # Use the existing chat but filter to only show property_manager messages
                        # Get only property_manager messages from staff or manager (NOT from tenants)
                        manager_messages = []
                        try:
                            # Get staff user_ids for this property
                            staff_user_ids = []
                            try:
                                staff_members = Staff.query.filter_by(property_id=property_id).all()
                                staff_user_ids = [staff.user_id for staff in staff_members if staff.user_id]
                            except Exception:
                                pass
                            
                            # Include property manager's user_id
                            manager_user_id = manager.id
                            allowed_sender_ids = [manager_user_id] + staff_user_ids
                            
                            # Only get messages from property_manager type where sender is manager or staff
                            manager_messages = Message.query.filter(
                                and_(
                                    Message.chat_id == recent_manager_chat.id,
                                    Message.sender_type == 'property_manager',
                                    Message.sender_id.in_(allowed_sender_ids)
                                )
                            ).order_by(Message.created_at.asc()).all()
                            
                            current_app.logger.info(f"Found {len(manager_messages)} staff-manager messages in chat {recent_manager_chat.id}")
                        except Exception as msg_query_err:
                            current_app.logger.warning(f"Error querying messages for chat {recent_manager_chat.id}: {str(msg_query_err)}", exc_info=True)
                            manager_messages = []
                        
                        # Create chat dict with only manager messages
                        try:
                            chat_dict = {
                                'id': recent_manager_chat.id,
                                'property_id': property_id,
                                'subject': manager_name,
                                'status': recent_manager_chat.status,
                                'last_message_at': recent_manager_chat.last_message_at.isoformat() if recent_manager_chat.last_message_at else None,
                                'created_at': recent_manager_chat.created_at.isoformat() if recent_manager_chat.created_at else None,
                                'updated_at': recent_manager_chat.updated_at.isoformat() if recent_manager_chat.updated_at else None,
                                'unread_count': 0,  # Calculate if needed
                                'messages': [],
                                'property': {
                                    'id': property_id,
                                    'name': getattr(property_obj, 'name', 'Property'),
                                    'manager': {
                                        'id': manager.id,
                                        'name': manager_name,
                                        'first_name': manager_first_name,
                                        'last_name': manager_last_name,
                                        'email': getattr(manager, 'email', '')
                                    }
                                },
                                'is_staff_manager_chat': True
                            }
                            
                            # Add messages safely
                            try:
                                chat_dict['messages'] = [msg.to_dict(include_sender=True) for msg in manager_messages]
                            except Exception as msg_dict_err:
                                current_app.logger.warning(f"Error serializing messages: {str(msg_dict_err)}")
                                chat_dict['messages'] = []
                            
                            # Get last message if exists
                            if manager_messages:
                                try:
                                    chat_dict['last_message'] = manager_messages[-1].to_dict(include_sender=True)
                                except Exception:
                                    chat_dict['last_message'] = None
                            
                            chats_list = [chat_dict]
                        except Exception as dict_err:
                            current_app.logger.error(f"Error creating chat dict: {str(dict_err)}", exc_info=True)
                            # Return empty list if dict creation fails
                            chats_list = []
                        
                except Exception as chat_query_err:
                    current_app.logger.error(f"Error querying staff-manager chat: {str(chat_query_err)}", exc_info=True)
                    # Return empty list if query fails - chats_list already initialized
                    if 'chats_list' not in locals() or chats_list is None:
                        chats_list = []
                
                # Ensure chats_list is always a list
                if not isinstance(chats_list, list):
                    chats_list = []
                
                response = jsonify({
                    'chats': chats_list,
                    'total': len(chats_list)
                })
                response.status_code = 200
                current_app.logger.info(f"get_chats STAFF: Returning {len(chats_list)} chat(s) for property {property_id}")
                return response
                
            except Exception as staff_section_err:
                import traceback
                error_trace = traceback.format_exc()
                current_app.logger.error(f"Error in STAFF section of get_chats: {str(staff_section_err)}\n{error_trace}")
                # Return error with details in debug mode
                error_response = {'error': 'Error processing staff chat request'}
                if current_app.config.get('DEBUG', False):
                    error_response['details'] = str(staff_section_err)
                    error_response['traceback'] = error_trace
                response = jsonify(error_response)
                response.status_code = 500
                return response
        
        else:
            return jsonify({'error': 'Access denied'}), 403
        
    except Exception as e:
        current_app.logger.error(f"Error in get_chats: {str(e)}", exc_info=True)
        import traceback
        error_details = traceback.format_exc()
        current_app.logger.error(f"Full traceback: {error_details}")
        
        # Return error with more details in debug mode
        error_response = {'error': 'Failed to fetch chats'}
        if current_app.config.get('DEBUG', False):
            error_response['details'] = str(e)
            error_response['traceback'] = error_details
        response = jsonify(error_response)
        response.status_code = 500
        return response

@chat_bp.route('/', methods=['POST'])
@jwt_required()
def create_chat():
    """
    Create chat
    ---
    tags:
      - Chat
    summary: Create a new chat
    description: Create a new chat. Tenant only.
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
          properties:
            subject:
              type: string
            message:
              type: string
    responses:
      201:
        description: Chat created successfully
        schema:
          type: object
          properties:
            message:
              type: string
            chat:
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
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Only tenants can create chats
        tenant = get_current_tenant()
        if not tenant:
            return jsonify({'error': 'Only tenants can create chats'}), 403
        
        # Get and validate request data
        try:
            data = request.get_json() or {}
            # Ensure data is a dictionary, not a string
            if isinstance(data, str):
                import json
                data = json.loads(data) if data else {}
            if not isinstance(data, dict):
                return jsonify({'error': 'Invalid request data format'}), 400
        except Exception as json_error:
            current_app.logger.error(f"Error parsing JSON: {str(json_error)}")
            return jsonify({'error': 'Invalid JSON in request body'}), 400
        
        # CRITICAL: Always use tenant's property_id - never from request
        # This ensures tenants can only create chats for their own property
        property_id = tenant.property_id
        
        if not property_id:
            return jsonify({'error': 'Tenant does not have a property assigned'}), 400
        
        # Verify property exists and has a property manager (owner)
        property_obj = Property.query.get(property_id)
        if not property_obj:
            return jsonify({'error': 'Property not found'}), 404
        
        # Verify property has an owner (property manager)
        if not property_obj.owner_id:
            current_app.logger.error(f"Property {property_id} has no owner assigned")
            return jsonify({'error': 'Property does not have a manager assigned. Please contact support.'}), 400
        
        # Verify the owner exists
        property_manager = User.query.get(property_obj.owner_id)
        if not property_manager:
            current_app.logger.error(f"Property {property_id} owner_id {property_obj.owner_id} does not exist")
            return jsonify({'error': 'Property manager not found. Please contact support.'}), 400
        
        # Double-check: tenant must belong to this property (should always be true, but verify)
        if tenant.property_id != property_id:
            return jsonify({'error': 'Tenant does not belong to this property'}), 403
        
        # Generate chat subject from property manager's name
        # Use property manager's name as the conversation name
        manager_name = None
        try:
            # Use User model's full_name property for consistent formatting
            if hasattr(property_manager, 'full_name'):
                manager_name = property_manager.full_name
            
            # Fallback to manual construction if full_name is empty
            if not manager_name:
                first_name = getattr(property_manager, 'first_name', '') or ''
                last_name = getattr(property_manager, 'last_name', '') or ''
                if first_name or last_name:
                    manager_name = f"{first_name} {last_name}".strip()
            
            # Fallback to email username if no name
            if not manager_name:
                email = getattr(property_manager, 'email', '')
                if email:
                    manager_name = email.split('@')[0].replace('.', ' ').title()
            
            # Final fallback
            if not manager_name:
                manager_name = f"Manager {property_manager.id}"
        except Exception as name_error:
            current_app.logger.warning(f"Error getting manager name: {str(name_error)}")
            manager_name = "Property Manager"
        
        # Use provided subject if given, otherwise use property manager's name
        subject = None
        if isinstance(data, dict) and data.get('subject'):
            provided_subject = data.get('subject', '').strip()
            # Only use provided subject if it's not a default value
            if provided_subject and provided_subject.lower() not in ['new inquiry', 'new conversation', '']:
                subject = provided_subject
        
        # If no valid subject provided, use manager's name
        if not subject:
            subject = manager_name or "Property Manager"
        
        # Ensure subject is not empty
        if not subject or not subject.strip():
            subject = manager_name or "Property Manager"
        
        # Create chat with property manager's name as subject
        try:
            new_chat = Chat(
                tenant_id=tenant.id,
                property_id=property_id,
                subject=subject
            )
            current_app.logger.info(f"Chat object created: tenant_id={tenant.id}, property_id={property_id}, subject={new_chat.subject}")
        except Exception as init_error:
            current_app.logger.error(f"Error creating Chat object: {str(init_error)}", exc_info=True)
            db.session.rollback()
            return jsonify({
                'error': 'Failed to create chat',
                'details': f'Error initializing chat: {str(init_error)}',
                'type': type(init_error).__name__
            }), 500
        
        try:
            db.session.add(new_chat)
            current_app.logger.info(f"Chat added to session: {new_chat.id if hasattr(new_chat, 'id') else 'pending'}")
            db.session.commit()
            current_app.logger.info(f"Chat committed to database: {new_chat.id} by tenant {tenant.id}")
        except Exception as db_error:
            current_app.logger.error(f"Database error creating chat: {str(db_error)}", exc_info=True)
            db.session.rollback()
            return jsonify({
                'error': 'Failed to create chat',
                'details': f'Database error: {str(db_error)}',
                'type': type(db_error).__name__
            }), 500
        
        # Safely get chat dict with property info (including manager)
        try:
            chat_dict = new_chat.to_dict(include_property=True)
            # Ensure messages array exists
            if 'messages' not in chat_dict:
                chat_dict['messages'] = []
        except Exception as dict_error:
            current_app.logger.warning(f"Error serializing chat {new_chat.id}: {str(dict_error)}")
            # Return minimal chat data if serialization fails, but include manager info
            try:
                property_manager = User.query.get(property_obj.owner_id) if property_obj.owner_id else None
                manager_info = None
                if property_manager:
                    manager_info = {
                        'id': property_manager.id,
                        'name': f"{property_manager.first_name} {property_manager.last_name}".strip() or property_manager.email,
                        'email': property_manager.email
                    }
            except Exception:
                manager_info = None
            
            chat_dict = {
                'id': new_chat.id,
                'tenant_id': new_chat.tenant_id,
                'property_id': new_chat.property_id,
                'subject': new_chat.subject,
                'status': new_chat.status,
                'messages': [],
                'property': {
                    'id': property_obj.id,
                    'name': getattr(property_obj, 'name', 'Unknown'),
                    'manager': manager_info
                }
            }
        
        return jsonify({
            'message': 'Chat created successfully',
            'chat': chat_dict
        }), 201
        
    except Exception as e:
        db.session.rollback()
        error_msg = str(e)
        error_type = type(e).__name__
        current_app.logger.error(f"Error in create_chat: {error_type} - {error_msg}", exc_info=True)
        # Return more detailed error message for debugging
        return jsonify({
            'error': 'Failed to create chat',
            'details': error_msg,
            'type': error_type
        }), 500

@chat_bp.route('/<int:chat_id>', methods=['GET'])
@jwt_required()
def get_chat(chat_id):
    """
    Get chat by ID
    ---
    tags:
      - Chat
    summary: Get a specific chat with messages
    description: Retrieve a specific chat with its messages
    security:
      - Bearer: []
    parameters:
      - in: path
        name: chat_id
        type: integer
        required: true
        description: The chat ID
    responses:
      200:
        description: Chat retrieved successfully
        schema:
          type: object
          properties:
            chat:
              type: object
            messages:
              type: array
              items:
                type: object
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Chat not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        chat = Chat.query.get(chat_id)
        if not chat:
            return jsonify({'error': 'Chat not found'}), 404
        
        # Update chat subject if it's still a default value or needs refresh
        should_update_subject = False
        if chat.subject and chat.subject.lower() in ['new inquiry', 'new conversation']:
            should_update_subject = True
        else:
            # Also check if subject needs to be updated to match current manager name
            try:
                property_obj = Property.query.get(chat.property_id)
                if property_obj and property_obj.owner_id:
                    manager = User.query.get(property_obj.owner_id)
                    if manager:
                        # Use User model's full_name property
                        manager_name = manager.full_name if hasattr(manager, 'full_name') else ''
                        if not manager_name:
                            first_name = getattr(manager, 'first_name', '') or ''
                            last_name = getattr(manager, 'last_name', '') or ''
                            manager_name = f"{first_name} {last_name}".strip()
                        
                        if not manager_name:
                            email = getattr(manager, 'email', '')
                            if email:
                                manager_name = email.split('@')[0].replace('.', ' ').title()
                            else:
                                manager_name = f"Manager {manager.id}"
                        
                        # Check if current subject doesn't match manager's name
                        if chat.subject != manager_name:
                            should_update_subject = True
            except Exception:
                pass
        
        if should_update_subject:
            try:
                # Get property manager's name
                property_obj = Property.query.get(chat.property_id)
                if property_obj and property_obj.owner_id:
                    manager = User.query.get(property_obj.owner_id)
                    if manager:
                        # Use User model's full_name property for consistent formatting
                        manager_name = manager.full_name if hasattr(manager, 'full_name') else ''
                        if not manager_name:
                            first_name = getattr(manager, 'first_name', '') or ''
                            last_name = getattr(manager, 'last_name', '') or ''
                            manager_name = f"{first_name} {last_name}".strip()
                        
                        if not manager_name:
                            email = getattr(manager, 'email', '')
                            if email:
                                manager_name = email.split('@')[0].replace('.', ' ').title()
                            else:
                                manager_name = f"Manager {manager.id}"
                        
                        # Update the chat subject
                        chat.subject = manager_name
                        db.session.commit()
                        current_app.logger.info(f"Updated chat {chat.id} subject to {manager_name}")
            except Exception as update_error:
                current_app.logger.warning(f"Error updating chat {chat.id} subject: {str(update_error)}")
                db.session.rollback()
        
        # Determine user role
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        # Check access permissions
        if user_role_str == 'TENANT':
            tenant = get_current_tenant()
            # CRITICAL: Verify tenant owns the chat AND it's for their property
            if not tenant or chat.tenant_id != tenant.id or chat.property_id != tenant.property_id:
                return jsonify({'error': 'Access denied'}), 403
        elif user_role_str in ['MANAGER']:
            property_obj = Property.query.get(chat.property_id)
            if not property_obj or (property_obj.owner_id != current_user.id and not is_super_admin(current_user.id)):
                return jsonify({'error': 'Access denied'}), 403
            
            # Update chat subject to tenant's or staff's name if needed (for property manager view)
            if chat.staff_id and chat.staff and chat.staff.user:
                try:
                    staff_user = chat.staff.user
                    first_name = getattr(staff_user, 'first_name', '') or ''
                    last_name = getattr(staff_user, 'last_name', '') or ''
                    if first_name or last_name:
                        staff_name = f"{first_name} {last_name}".strip()
                    else:
                        email = getattr(staff_user, 'email', '')
                        if email:
                            staff_name = email.split('@')[0].replace('.', ' ').title()
                        else:
                            staff_name = f"Staff {chat.staff.id}"
                    
                    if chat.subject != staff_name:
                        chat.subject = staff_name
                        db.session.commit()
                except Exception:
                    db.session.rollback()
        elif user_role_str == 'STAFF':
            # Staff can view chats for their property
            property_obj = Property.query.get(chat.property_id)
            if not property_obj:
                return jsonify({'error': 'Property not found'}), 404
            if not staff_belongs_to_property(current_user.id, chat.property_id):
                return jsonify({'error': 'Access denied. You do not belong to this property.'}), 403
            
            # Update chat subject to tenant's name if needed (for staff view, same as property manager)
            # Check if subject is a default value or matches property manager's name
            should_update_subject = False
            if chat.subject:
                subject_lower = chat.subject.lower()
                if subject_lower in ['new inquiry', 'new conversation']:
                    should_update_subject = True
                else:
                    # Check if subject matches property manager's name (set from tenant side)
                    try:
                        manager = User.query.get(property_obj.owner_id) if property_obj.owner_id else None
                        if manager:
                            first_name = getattr(manager, 'first_name', '') or ''
                            last_name = getattr(manager, 'last_name', '') or ''
                            if first_name or last_name:
                                manager_name = f"{first_name} {last_name}".strip()
                                if chat.subject == manager_name:
                                    should_update_subject = True
                    except Exception:
                        pass
            
            if should_update_subject:
                try:
                    if chat.tenant and chat.tenant.user:
                        tenant_user = chat.tenant.user
                        first_name = getattr(tenant_user, 'first_name', '') or ''
                        last_name = getattr(tenant_user, 'last_name', '') or ''
                        if first_name or last_name:
                            tenant_name = f"{first_name} {last_name}".strip()
                        else:
                            email = getattr(tenant_user, 'email', '')
                            if email:
                                tenant_name = email.split('@')[0].replace('.', ' ').title()
                            else:
                                tenant_name = f"Tenant {chat.tenant.id}"
                        
                        chat.subject = tenant_name
                        db.session.commit()
                        current_app.logger.info(f"Updated chat {chat.id} subject to tenant name: {tenant_name}")
                except Exception as update_error:
                    current_app.logger.warning(f"Error updating chat {chat.id} subject: {str(update_error)}")
                    db.session.rollback()
        else:
            return jsonify({'error': 'Access denied'}), 403
        
        # Get messages - filter for staff to only show property_manager messages from staff or manager
        if user_role_str == 'STAFF':
            # Staff can only see property_manager messages from staff or manager (NOT from tenants)
            # Get staff user_ids for this property
            from models.staff import Staff
            staff_user_ids = []
            try:
                property_obj = Property.query.get(chat.property_id)
                if property_obj:
                    staff_members = Staff.query.filter_by(property_id=chat.property_id).all()
                    staff_user_ids = [staff.user_id for staff in staff_members if staff.user_id]
            except Exception:
                pass
            
            # Include property manager's user_id
            manager_user_id = None
            try:
                property_obj = Property.query.get(chat.property_id)
                if property_obj and property_obj.owner_id:
                    manager_user_id = property_obj.owner_id
            except Exception:
                pass
            
            allowed_sender_ids = []
            if manager_user_id:
                allowed_sender_ids.append(manager_user_id)
            allowed_sender_ids.extend(staff_user_ids)
            
            if allowed_sender_ids:
                # Only get messages from property_manager type where sender is manager or staff
                messages = Message.query.filter(
                    and_(
                        Message.chat_id == chat_id,
                        Message.sender_type == 'property_manager',
                        Message.sender_id.in_(allowed_sender_ids)
                    )
                ).order_by(Message.created_at.asc()).all()
                current_app.logger.info(f"Staff viewing chat {chat_id}: showing {len(messages)} staff-manager messages (from {len(allowed_sender_ids)} allowed senders)")
            else:
                # Fallback: just filter by sender_type if we can't get sender IDs
                messages = Message.query.filter(
                    and_(
                        Message.chat_id == chat_id,
                        Message.sender_type == 'property_manager'
                    )
                ).order_by(Message.created_at.asc()).all()
                current_app.logger.warning(f"Staff viewing chat {chat_id}: could not get sender IDs, showing all property_manager messages")
        else:
            # For tenants and managers, show all messages
            messages = Message.query.filter_by(chat_id=chat_id).order_by(Message.created_at.asc()).all()
        
        # Mark messages as read for the current user
        if user_role_str == 'STAFF':
            # Staff marks property_manager messages as read
            sender_type = 'property_manager'
            opposite_type = 'property_manager'  # Staff sees manager messages
        else:
            sender_type = 'tenant' if user_role_str == 'TENANT' else 'property_manager'
            opposite_type = 'property_manager' if sender_type == 'tenant' else 'tenant'
        
        unread_messages = Message.query.filter_by(
            chat_id=chat_id,
            sender_type=opposite_type,
            is_read=False
        ).all()
        
        for msg in unread_messages:
            msg.mark_as_read()
        
        db.session.commit()
        
        # Get chat dict with messages - include tenant or staff based on chat type
        if chat.tenant_id:
            chat_dict = chat.to_dict(
                include_messages=True,
                include_tenant=(user_role_str in ['MANAGER', 'STAFF']),
                include_property=(user_role_str == 'TENANT')
            )
        elif chat.staff_id:
            chat_dict = chat.to_dict(
                include_messages=True,
                include_staff=True,  # Include staff info for staff chats
                include_property=False
            )
            # Ensure staff data is properly structured
            if chat.staff and chat.staff.user:
                if 'staff' not in chat_dict or not chat_dict.get('staff'):
                    chat_dict['staff'] = {}
                if not chat_dict['staff'].get('user'):
                    chat_dict['staff']['user'] = {
                        'id': chat.staff.user.id,
                        'first_name': getattr(chat.staff.user, 'first_name', '') or '',
                        'last_name': getattr(chat.staff.user, 'last_name', '') or '',
                        'email': getattr(chat.staff.user, 'email', '') or ''
                    }
                # Ensure name field exists
                if not chat_dict['staff'].get('name'):
                    first_name = chat_dict['staff']['user'].get('first_name', '') or ''
                    last_name = chat_dict['staff']['user'].get('last_name', '') or ''
                    if first_name or last_name:
                        chat_dict['staff']['name'] = f"{first_name} {last_name}".strip()
                    else:
                        email = chat_dict['staff']['user'].get('email', '')
                        if email:
                            chat_dict['staff']['name'] = email.split('@')[0].replace('.', ' ').title()
                        else:
                            chat_dict['staff']['name'] = f"Staff {chat.staff.id}"
        else:
            chat_dict = chat.to_dict(
                include_messages=True,
                include_property=(user_role_str == 'TENANT')
            )
        
        # For staff, update the chat subject to show property manager name
        if user_role_str == 'STAFF':
            property_obj = Property.query.get(chat.property_id)
            if property_obj and property_obj.owner_id:
                manager = User.query.get(property_obj.owner_id)
                if manager:
                    manager_first_name = getattr(manager, 'first_name', '') or ''
                    manager_last_name = getattr(manager, 'last_name', '') or ''
                    if manager_first_name or manager_last_name:
                        manager_name = f"{manager_first_name} {manager_last_name}".strip()
                    else:
                        manager_email = getattr(manager, 'email', '')
                        if manager_email:
                            manager_name = manager_email.split('@')[0].replace('.', ' ').title()
                        else:
                            manager_name = "Property Manager"
                    chat_dict['subject'] = manager_name
                    chat_dict['property'] = {
                        'id': property_obj.id,
                        'name': getattr(property_obj, 'name', 'Property'),
                        'manager': {
                            'id': manager.id,
                            'name': manager_name,
                            'first_name': manager_first_name,
                            'last_name': manager_last_name,
                            'email': getattr(manager, 'email', '')
                        }
                    }
        
        # Always include messages from the separately queried list to ensure they're included
        # This ensures messages are always present even if the relationship wasn't loaded
        try:
            chat_dict['messages'] = [msg.to_dict(include_sender=True) for msg in messages]
            current_app.logger.info(f"Chat {chat_id} has {len(messages)} messages for {user_role_str}")
        except Exception as msg_error:
            current_app.logger.warning(f"Error serializing messages for chat {chat_id}: {str(msg_error)}")
            # Fallback to relationship if available
            if 'messages' not in chat_dict:
                chat_dict['messages'] = []
        
        return jsonify({
            'chat': chat_dict
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in get_chat: {str(e)}", exc_info=True)
        return jsonify({'error': 'Failed to fetch chat'}), 500

@chat_bp.route('/<int:chat_id>/messages', methods=['GET'])
@jwt_required()
def get_messages(chat_id):
    """
    Get chat messages
    ---
    tags:
      - Chat
    summary: Get messages for a specific chat
    description: Retrieve messages for a specific chat with pagination
    security:
      - Bearer: []
    parameters:
      - in: path
        name: chat_id
        type: integer
        required: true
        description: The chat ID
      - in: query
        name: page
        type: integer
        default: 1
      - in: query
        name: per_page
        type: integer
        default: 50
    responses:
      200:
        description: Messages retrieved successfully
        schema:
          type: object
          properties:
            messages:
              type: array
              items:
                type: object
            total:
              type: integer
            pages:
              type: integer
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Chat not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        chat = Chat.query.get(chat_id)
        if not chat:
            return jsonify({'error': 'Chat not found'}), 404
        
        # Check access permissions
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        if user_role_str == 'TENANT':
            tenant = get_current_tenant()
            # CRITICAL: Verify tenant owns the chat AND it's for their property
            if not tenant or chat.tenant_id != tenant.id or chat.property_id != tenant.property_id:
                return jsonify({'error': 'Access denied'}), 403
        elif user_role_str in ['MANAGER']:
            property_obj = Property.query.get(chat.property_id)
            if not property_obj or (property_obj.owner_id != current_user.id and not is_super_admin(current_user.id)):
                return jsonify({'error': 'Access denied'}), 403
        else:
            return jsonify({'error': 'Access denied'}), 403
        
        # Get query parameters
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 100)
        
        # Get messages
        messages_query = Message.query.filter_by(chat_id=chat_id).order_by(desc(Message.created_at))
        messages = messages_query.paginate(page=page, per_page=per_page, error_out=False)
        
        # Mark messages as read
        sender_type = 'tenant' if user_role_str == 'TENANT' else 'property_manager'
        opposite_type = 'property_manager' if sender_type == 'tenant' else 'tenant'
        
        unread_messages = Message.query.filter_by(
            chat_id=chat_id,
            sender_type=opposite_type,
            is_read=False
        ).all()
        
        for msg in unread_messages:
            msg.mark_as_read()
        
        # Update chat's last_message_at if needed
        if unread_messages:
            db.session.commit()
        
        return jsonify({
            'messages': [msg.to_dict(include_sender=True) for msg in messages.items],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': messages.total,
                'pages': messages.pages,
                'has_next': messages.has_next,
                'has_prev': messages.has_prev
            }
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in get_messages: {str(e)}", exc_info=True)
        return jsonify({'error': 'Failed to fetch messages'}), 500

@chat_bp.route('/<int:chat_id>/messages', methods=['POST'])
@jwt_required()
def send_message(chat_id):
    """
    Send message
    ---
    tags:
      - Chat
    summary: Send a message in a chat
    description: Send a message in a chat
    security:
      - Bearer: []
    parameters:
      - in: path
        name: chat_id
        type: integer
        required: true
        description: The chat ID
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - message
          properties:
            message:
              type: string
    responses:
      201:
        description: Message sent successfully
        schema:
          type: object
          properties:
            message:
              type: string
            chat_message:
              type: object
      400:
        description: Validation error
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Chat not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        chat = Chat.query.get(chat_id)
        if not chat:
            return jsonify({'error': 'Chat not found'}), 404
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        content = data.get('content', '').strip()
        if not content:
            return jsonify({'error': 'Message content is required'}), 400
        
        # Determine user role and sender type
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        # Determine sender type based on role
        if user_role_str == 'TENANT':
            sender_type = 'tenant'
        elif user_role_str in ['MANAGER', 'STAFF']:
            # Both managers and staff send as property_manager type
            # Staff communicates with property manager, so they send as property_manager
            sender_type = 'property_manager'
        else:
            return jsonify({'error': 'Invalid user role'}), 403
        
        # Check access permissions
        if user_role_str == 'TENANT':
            tenant = get_current_tenant()
            # CRITICAL: Verify tenant owns the chat AND it's for their property
            if not tenant or chat.tenant_id != tenant.id or chat.property_id != tenant.property_id:
                return jsonify({'error': 'Access denied'}), 403
        elif user_role_str in ['MANAGER']:
            property_obj = Property.query.get(chat.property_id)
            if not property_obj or (property_obj.owner_id != current_user.id and not is_super_admin(current_user.id)):
                return jsonify({'error': 'Access denied'}), 403
        elif user_role_str == 'STAFF':
            # Staff can only send messages in chats for their property
            # Staff communicates with property manager only (no direct tenant communication)
            property_obj = Property.query.get(chat.property_id)
            if not property_obj:
                return jsonify({'error': 'Property not found'}), 404
            
            # Verify staff belongs to property (lenient check for now)
            try:
                if not staff_belongs_to_property(current_user.id, chat.property_id):
                    current_app.logger.warning(f"Staff {current_user.id} sending message in chat {chat_id} for property {chat.property_id} (not verified in staff table)")
            except Exception as staff_check_err:
                current_app.logger.warning(f"Error checking staff membership: {str(staff_check_err)}")
                # Continue anyway for now
        else:
            return jsonify({'error': 'Access denied'}), 403
        
        # Create message
        new_message = Message(
            chat_id=chat_id,
            sender_id=current_user.id,
            sender_type=sender_type,
            content=content
        )
        
        db.session.add(new_message)
        
        # Update chat's last_message_at
        chat.last_message_at = datetime.now(timezone.utc)
        chat.updated_at = datetime.now(timezone.utc)
        
        db.session.commit()
        
        current_app.logger.info(f"Message sent: {new_message.id} in chat {chat_id}")
        
        return jsonify({
            'message': 'Message sent successfully',
            'message_data': new_message.to_dict(include_sender=True)
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in send_message: {str(e)}", exc_info=True)
        return jsonify({'error': 'Failed to send message'}), 500

@chat_bp.route('/<int:chat_id>/read', methods=['PUT'])
@jwt_required()
def mark_chat_as_read(chat_id):
    """
    Mark chat as read
    ---
    tags:
      - Chat
    summary: Mark all messages in a chat as read
    description: Mark all unread messages in a chat as read
    security:
      - Bearer: []
    parameters:
      - in: path
        name: chat_id
        type: integer
        required: true
        description: The chat ID
    responses:
      200:
        description: Chat marked as read successfully
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
        description: Chat not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        chat = Chat.query.get(chat_id)
        if not chat:
            return jsonify({'error': 'Chat not found'}), 404
        
        # Check access permissions
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        if user_role_str == 'TENANT':
            tenant = get_current_tenant()
            # CRITICAL: Verify tenant owns the chat AND it's for their property
            if not tenant or chat.tenant_id != tenant.id or chat.property_id != tenant.property_id:
                return jsonify({'error': 'Access denied'}), 403
        elif user_role_str in ['MANAGER']:
            property_obj = Property.query.get(chat.property_id)
            if not property_obj or (property_obj.owner_id != current_user.id and not is_super_admin(current_user.id)):
                return jsonify({'error': 'Access denied'}), 403
        else:
            return jsonify({'error': 'Access denied'}), 403
        
        # Mark all unread messages as read
        sender_type = 'tenant' if user_role_str == 'TENANT' else 'property_manager'
        opposite_type = 'property_manager' if sender_type == 'tenant' else 'tenant'
        
        unread_messages = Message.query.filter_by(
            chat_id=chat_id,
            sender_type=opposite_type,
            is_read=False
        ).all()
        
        for msg in unread_messages:
            msg.mark_as_read()
        
        db.session.commit()
        
        return jsonify({
            'message': 'Chat marked as read',
            'marked_count': len(unread_messages)
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in mark_chat_as_read: {str(e)}", exc_info=True)
        return jsonify({'error': 'Failed to mark chat as read'}), 500

@chat_bp.route('/<int:chat_id>', methods=['PUT'])
@jwt_required()
def update_chat(chat_id):
    """
    Update chat
    ---
    tags:
      - Chat
    summary: Update chat information
    description: Update chat status, subject, etc.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: chat_id
        type: integer
        required: true
        description: The chat ID
      - in: body
        name: body
        schema:
          type: object
          properties:
            subject:
              type: string
            status:
              type: string
    responses:
      200:
        description: Chat updated successfully
        schema:
          type: object
          properties:
            message:
              type: string
            chat:
              type: object
      400:
        description: Validation error
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Chat not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        chat = Chat.query.get(chat_id)
        if not chat:
            return jsonify({'error': 'Chat not found'}), 404
        
        # Check access permissions
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        if user_role_str == 'TENANT':
            tenant = get_current_tenant()
            # CRITICAL: Verify tenant owns the chat AND it's for their property
            if not tenant or chat.tenant_id != tenant.id or chat.property_id != tenant.property_id:
                return jsonify({'error': 'Access denied'}), 403
        elif user_role_str in ['MANAGER']:
            property_obj = Property.query.get(chat.property_id)
            if not property_obj or (property_obj.owner_id != current_user.id and not is_super_admin(current_user.id)):
                return jsonify({'error': 'Access denied'}), 403
        else:
            return jsonify({'error': 'Access denied'}), 403
        
        data = request.get_json() or {}
        
        # Update allowed fields
        if 'subject' in data:
            chat.subject = data['subject'].strip() if data['subject'] else 'New Conversation'
        
        if 'status' in data:
            status = data['status'].lower()
            if status in ['active', 'archived', 'closed']:
                chat.status = status
        
        db.session.commit()
        
        return jsonify({
            'message': 'Chat updated successfully',
            'chat': chat.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in update_chat: {str(e)}", exc_info=True)
        return jsonify({'error': 'Failed to update chat'}), 500

@chat_bp.route('/unread-count', methods=['GET'])
@jwt_required()
def get_unread_count():
    """
    Get unread count
    ---
    tags:
      - Chat
    summary: Get total unread message count
    description: Get total unread message count for current user
    security:
      - Bearer: []
    responses:
      200:
        description: Unread count retrieved successfully
        schema:
          type: object
          properties:
            unread_count:
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
        
        # Determine user role
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        property_id = get_property_id_from_request()
        
        if user_role_str == 'TENANT':
            tenant = get_current_tenant()
            if not tenant:
                return jsonify({'unread_count': 0}), 200
            
            # Count unread messages for tenant (messages from property manager)
            count = db.session.query(Message).join(Chat).filter(
                Chat.tenant_id == tenant.id,
                Message.sender_type == 'property_manager',
                Message.is_read == False
            ).count()
            
            return jsonify({'unread_count': count}), 200
        
        elif user_role_str in ['MANAGER']:
            if not property_id:
                return jsonify({'unread_count': 0}), 200
            
            # Verify property
            property_obj = Property.query.get(property_id)
            if not property_obj or (property_obj.owner_id != current_user.id and not is_super_admin(current_user.id)):
                return jsonify({'unread_count': 0}), 200
            
            # Count unread messages for property manager (messages from tenants)
            count = db.session.query(Message).join(Chat).filter(
                Chat.property_id == property_id,
                Message.sender_type == 'tenant',
                Message.is_read == False
            ).count()
            
            return jsonify({'unread_count': count}), 200
        
        else:
            return jsonify({'unread_count': 0}), 200
        
    except Exception as e:
        current_app.logger.error(f"Error in get_unread_count: {str(e)}", exc_info=True)
        return jsonify({'unread_count': 0}), 200  # Return 0 on error to prevent UI issues

