from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timezone
from sqlalchemy import desc, and_, or_

from app import db
from models.notification import Notification, NotificationType, NotificationPriority
from models.user import User, UserRole
from models.tenant import Tenant

notification_bp = Blueprint('notifications', __name__)

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
        user_role_str = user_role.value.upper()
    elif isinstance(user_role, str):
        user_role_str = user_role.upper()
    else:
        user_role_str = str(user_role).upper() if user_role else 'TENANT'
    
    if user_role_str != 'TENANT':
        return None
    
    # Get tenant profile
    tenant = Tenant.query.filter_by(user_id=user.id).first()
    return tenant

@notification_bp.route('/', methods=['GET'])
@jwt_required()
def get_notifications():
    """
    Get notifications
    ---
    tags:
      - Notifications
    summary: Get notifications for the current user
    description: Retrieve notifications for the current user (tenant or property manager)
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
        name: unread_only
        type: boolean
    responses:
      200:
        description: Notifications retrieved successfully
        schema:
          type: object
          properties:
            notifications:
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
        is_read = request.args.get('is_read', type=str)  # 'true', 'false', or None for all
        notification_type = request.args.get('type', type=str)
        priority = request.args.get('priority', type=str)
        
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value.upper()
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        # Build query based on user role
        if user_role_str == 'TENANT':
            # Tenants see notifications for their tenant_id
            tenant = get_current_tenant()
            if not tenant:
                return jsonify({'error': 'Tenant profile not found'}), 404
            query = Notification.query.filter_by(tenant_id=tenant.id, user_id=current_user.id, recipient_type='tenant')
        elif user_role_str in ['MANAGER', 'PROPERTY_MANAGER']:
            query = Notification.query.filter_by(user_id=current_user.id)
        elif user_role_str == 'STAFF':
            # Staff see notifications for their user_id with recipient_type='staff'
            query = Notification.query.filter_by(
                user_id=current_user.id,
                recipient_type='staff'
            )
        else:
            # Other roles - see notifications for their user_id
            query = Notification.query.filter_by(user_id=current_user.id)
        
        # Filter by read status
        if is_read is not None:
            if is_read.lower() == 'true':
                query = query.filter_by(is_read=True)
            elif is_read.lower() == 'false':
                query = query.filter_by(is_read=False)
        
        # Filter by notification type
        if notification_type:
            query = query.filter(Notification.notification_type == notification_type.lower())
        
        # Filter by priority
        if priority:
            query = query.filter(Notification.priority == priority.lower())
        
        # Order by created_at descending (newest first)
        query = query.order_by(desc(Notification.created_at))
        
        # Paginate
        notifications = query.paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        # Serialize notifications
        notifications_list = []
        for notification in notifications.items:
            try:
                notifications_list.append(notification.to_dict(include_related=False))
            except Exception as notif_error:
                current_app.logger.warning(f"Error serializing notification {notification.id}: {str(notif_error)}")
                continue
        
        return jsonify({
            'notifications': notifications_list,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': notifications.total,
                'pages': notifications.pages,
                'has_next': notifications.has_next,
                'has_prev': notifications.has_prev
            }
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error in get_notifications: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@notification_bp.route('/unread-count', methods=['GET'])
@jwt_required()
def get_unread_count():
    """
    Get unread count
    ---
    tags:
      - Notifications
    summary: Get count of unread notifications
    description: Get count of unread notifications for the current user (tenant or property manager)
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
    # Default response - will be returned on any error
    default_response = {'unread_count': 0}
    
    try:
        current_user = get_current_user()
        if not current_user:
            current_app.logger.warning("get_unread_count: User not found")
            response = jsonify(default_response)
            response.status_code = 200
            return response
        
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value.upper()
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        # Build query based on user role
        count = 0
        try:
            if user_role_str == 'TENANT':
                tenant = get_current_tenant()
                if not tenant:
                    current_app.logger.warning(f"get_unread_count: Tenant profile not found for user {current_user.id}")
                    response = jsonify(default_response)
                    response.status_code = 200
                    return response
                try:
                    count = Notification.query.filter_by(
                        tenant_id=tenant.id,
                        user_id=current_user.id,
                        recipient_type='tenant',
                        is_read=False
                    ).count()
                except Exception as tenant_count_err:
                    current_app.logger.warning(f"Error counting tenant notifications: {str(tenant_count_err)}")
                    count = 0
            elif user_role_str in ['MANAGER', 'PROPERTY_MANAGER']:
                try:
                    count = Notification.query.filter_by(
                        user_id=current_user.id,
                        is_read=False
                    ).count()
                except Exception as manager_count_err:
                    current_app.logger.warning(f"Error counting manager notifications: {str(manager_count_err)}")
                    count = 0
            elif user_role_str == 'STAFF':
                try:
                    # Get property_id from request if available (for filtering)
                    from routes.auth_routes import get_property_id_from_request
                    property_id = get_property_id_from_request()
                    
                    # Build base query
                    query = Notification.query.filter_by(
                        user_id=current_user.id,
                        recipient_type='staff',
                        is_read=False
                    )
                    
                    # If property_id is available and Notification model has property_id field, filter by it
                    if property_id and hasattr(Notification, 'property_id'):
                        query = query.filter(Notification.property_id == property_id)
                    
                    count = query.count()
                except Exception as staff_notif_err:
                    current_app.logger.warning(f"Error getting unread count for staff: {str(staff_notif_err)}", exc_info=True)
                    # Fallback to simple count
                    try:
                        count = Notification.query.filter_by(
                            user_id=current_user.id,
                            recipient_type='staff',
                            is_read=False
                        ).count()
                    except Exception:
                        count = 0
            else:
                try:
                    count = Notification.query.filter_by(
                        user_id=current_user.id,
                        is_read=False
                    ).count()
                except Exception as other_count_err:
                    current_app.logger.warning(f"Error counting notifications for role {user_role_str}: {str(other_count_err)}")
                    count = 0
        except Exception as query_err:
            current_app.logger.error(f"Error building notification query: {str(query_err)}", exc_info=True)
            count = 0
        
        response = jsonify({'unread_count': count})
        response.status_code = 200
        return response
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        current_app.logger.error(f"Error in get_unread_count: {str(e)}\n{error_trace}", exc_info=True)
        
        # Return safe default response instead of crashing
        # This ensures CORS headers are sent even on error
        response = jsonify({
            'unread_count': 0,
            'error': 'Failed to load unread count',
            'error_details': str(e) if current_app.config.get('DEBUG', False) else None
        })
        response.status_code = 200  # Return 200 with error message instead of 500
        return response

@notification_bp.route('/<int:notification_id>', methods=['GET'])
@jwt_required()
def get_notification(notification_id):
    """
    Get notification by ID
    ---
    tags:
      - Notifications
    summary: Get a specific notification
    description: Retrieve a specific notification by ID
    security:
      - Bearer: []
    parameters:
      - in: path
        name: notification_id
        type: integer
        required: true
        description: The notification ID
    responses:
      200:
        description: Notification retrieved successfully
        schema:
          type: object
          properties:
            notification:
              type: object
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Notification not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        notification = Notification.query.filter_by(
            id=notification_id,
            user_id=current_user.id  # Ensure user owns this notification
        ).first()
        
        if not notification:
            return jsonify({'error': 'Notification not found'}), 404
        
        return jsonify(notification.to_dict(include_related=True)), 200
        
    except Exception as e:
        current_app.logger.error(f"Error in get_notification: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@notification_bp.route('/<int:notification_id>/read', methods=['PUT'])
@jwt_required()
def mark_as_read(notification_id):
    """
    Mark notification as read
    ---
    tags:
      - Notifications
    summary: Mark a notification as read
    description: Mark a notification as read
    security:
      - Bearer: []
    parameters:
      - in: path
        name: notification_id
        type: integer
        required: true
        description: The notification ID
    responses:
      200:
        description: Notification marked as read successfully
        schema:
          type: object
          properties:
            message:
              type: string
            notification:
              type: object
      401:
        description: Unauthorized
      404:
        description: Notification not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        notification = Notification.query.filter_by(
            id=notification_id,
            user_id=current_user.id  # Ensure user owns this notification
        ).first()
        
        if not notification:
            return jsonify({'error': 'Notification not found'}), 404
        
        notification.mark_as_read()
        
        return jsonify({
            'message': 'Notification marked as read',
            'notification': notification.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in mark_as_read: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@notification_bp.route('/<int:notification_id>/unread', methods=['PUT'])
@jwt_required()
def mark_as_unread(notification_id):
    """
    Mark notification as unread
    ---
    tags:
      - Notifications
    summary: Mark a notification as unread
    description: Mark a notification as unread
    security:
      - Bearer: []
    parameters:
      - in: path
        name: notification_id
        type: integer
        required: true
        description: The notification ID
    responses:
      200:
        description: Notification marked as unread successfully
        schema:
          type: object
          properties:
            message:
              type: string
            notification:
              type: object
      401:
        description: Unauthorized
      404:
        description: Notification not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        notification = Notification.query.filter_by(
            id=notification_id,
            user_id=current_user.id  # Ensure user owns this notification
        ).first()
        
        if not notification:
            return jsonify({'error': 'Notification not found'}), 404
        
        notification.mark_as_unread()
        
        return jsonify({
            'message': 'Notification marked as unread',
            'notification': notification.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in mark_as_unread: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@notification_bp.route('/mark-all-read', methods=['PUT'])
@jwt_required()
def mark_all_as_read():
    """
    Mark all notifications as read
    ---
    tags:
      - Notifications
    summary: Mark all notifications as read
    description: Mark all notifications as read for the current user
    security:
      - Bearer: []
    responses:
      200:
        description: All notifications marked as read successfully
        schema:
          type: object
          properties:
            message:
              type: string
            count:
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
        
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value.upper()
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        # Build query based on user role
        if user_role_str == 'TENANT':
            tenant = get_current_tenant()
            if not tenant:
                return jsonify({'error': 'Tenant profile not found'}), 404
            query = Notification.query.filter_by(
                tenant_id=tenant.id,
                user_id=current_user.id,
                recipient_type='tenant',
                is_read=False
            )
        elif user_role_str in ['MANAGER', 'PROPERTY_MANAGER']:
            query = Notification.query.filter_by(
                user_id=current_user.id,
                is_read=False
            )
        elif user_role_str == 'STAFF':
            query = Notification.query.filter_by(
                user_id=current_user.id,
                recipient_type='staff',
                is_read=False
            )
        else:
            query = Notification.query.filter_by(
                user_id=current_user.id,
                is_read=False
            )
        
        # Update all unread notifications
        updated = query.update({
            'is_read': True,
            'read_at': datetime.now(timezone.utc)
        })
        
        db.session.commit()
        
        return jsonify({
            'message': f'{updated} notifications marked as read',
            'count': updated
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in mark_all_as_read: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@notification_bp.route('/<int:notification_id>', methods=['DELETE'])
@jwt_required()
def delete_notification(notification_id):
    """
    Delete notification
    ---
    tags:
      - Notifications
    summary: Delete a notification
    description: Delete a notification
    security:
      - Bearer: []
    parameters:
      - in: path
        name: notification_id
        type: integer
        required: true
        description: The notification ID
    responses:
      200:
        description: Notification deleted successfully
        schema:
          type: object
          properties:
            message:
              type: string
      401:
        description: Unauthorized
      404:
        description: Notification not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        notification = Notification.query.filter_by(
            id=notification_id,
            user_id=current_user.id  # Ensure user owns this notification
        ).first()
        
        if not notification:
            return jsonify({'error': 'Notification not found'}), 404
        
        db.session.delete(notification)
        db.session.commit()
        
        return jsonify({'message': 'Notification deleted successfully'}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in delete_notification: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@notification_bp.route('/delete-all-read', methods=['DELETE'])
@jwt_required()
def delete_all_read():
    """
    Delete all read notifications
    ---
    tags:
      - Notifications
    summary: Delete all read notifications
    description: Delete all read notifications for the current user
    security:
      - Bearer: []
    responses:
      200:
        description: All read notifications deleted successfully
        schema:
          type: object
          properties:
            message:
              type: string
            count:
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
        
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value.upper()
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        # Build query based on user role
        if user_role_str == 'TENANT':
            tenant = get_current_tenant()
            if not tenant:
                return jsonify({'error': 'Tenant profile not found'}), 404
            query = Notification.query.filter_by(
                tenant_id=tenant.id,
                user_id=current_user.id,
                is_read=True
            )
        elif user_role_str in ['MANAGER', 'PROPERTY_MANAGER']:
            query = Notification.query.filter_by(
                user_id=current_user.id,
                is_read=True
            )
        else:
            query = Notification.query.filter_by(
                user_id=current_user.id,
                is_read=True
            )
        
        # Delete all read notifications
        deleted = query.delete()
        
        db.session.commit()
        
        return jsonify({
            'message': f'{deleted} read notifications deleted',
            'count': deleted
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error in delete_all_read: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@notification_bp.route('/send-reminders', methods=['POST'])
def send_reminders():
    """
    Send all reminders
    ---
    tags:
      - Notifications
    summary: Trigger reminder service to send all automated reminders
    description: |
      This endpoint triggers the reminder service to send all automated reminders.
      Can be called periodically via cron job or scheduler.
      
      **Reminder Types:**
      - Bill due reminders (7, 3, 1 days before due date)
      - Overdue bill reminders (daily)
      - Maintenance schedule reminders (3, 1 days before scheduled date)
      - Task deadline reminders (3, 1 days before deadline)
      - Lease expiring reminders (30, 14, 7, 3 days before expiration)
      
      **Security Note:** This endpoint should be protected with an API key or secret token
      in production to prevent unauthorized access.
    parameters:
      - in: query
        name: api_key
        type: string
        description: API key for authentication (optional, should be set in production)
    responses:
      200:
        description: Reminders sent successfully
        schema:
          type: object
          properties:
            message:
              type: string
            results:
              type: object
              properties:
                bill_due_reminders:
                  type: integer
                overdue_bill_reminders:
                  type: integer
                maintenance_reminders:
                  type: integer
                task_reminders:
                  type: integer
                lease_reminders:
                  type: integer
      401:
        description: Unauthorized (if API key is required)
      500:
        description: Server error
    """
    try:
        # Optional: Add API key authentication for production
        # api_key = request.args.get('api_key') or request.headers.get('X-API-Key')
        # if api_key != current_app.config.get('REMINDER_API_KEY'):
        #     return jsonify({'error': 'Unauthorized'}), 401
        
        from services.reminder_service import ReminderService
        
        results = ReminderService.send_all_reminders()
        
        total = sum(v for v in results.values() if isinstance(v, int))
        
        return jsonify({
            'message': f'Reminders sent successfully. Total: {total}',
            'results': results
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error in send_reminders: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

