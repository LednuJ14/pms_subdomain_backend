from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from sqlalchemy import text, or_
from datetime import datetime

from app import db
from models.user import User, UserRole
from models.feedback import Feedback
from models.tenant import Tenant
from models.property import Property

feedback_bp = Blueprint('feedback', __name__)

def get_property_id_from_request(data=None):
    """
    Try to get property_id from request.
    Checks request body, query parameter, header, subdomain, or Origin header.
    Returns None if not found.
    """
    try:
        # Check query parameter first
        property_id = request.args.get('property_id', type=int)
        if property_id:
            return property_id
        
        # Check header
        property_id = request.headers.get('X-Property-ID', type=int)
        if property_id:
            return property_id
        
        # Check JWT claims
        try:
            claims = get_jwt()
            if claims:
                property_id = claims.get('property_id')
                if property_id:
                    return property_id
        except Exception:
            pass
        
        # Check request body if data is provided
        if data:
            property_id = data.get('property_id')
            if property_id:
                try:
                    return int(property_id)
                except (ValueError, TypeError):
                    pass
        
        # Try to extract from subdomain in Origin or Host header
        origin = request.headers.get('Origin', '')
        host = request.headers.get('Host', '')
        
        if origin or host:
            import re
            # Extract subdomain (e.g., "pat" from "pat.localhost:8080")
            subdomain_match = re.search(r'([a-zA-Z0-9-]+)\.localhost', origin or host)
            if subdomain_match:
                subdomain = subdomain_match.group(1).lower()
                
                # Try to find property by matching subdomain
                try:
                    # Try exact match on portal_subdomain, title, building_name, name
                    match_columns = ['portal_subdomain', 'title', 'building_name', 'name']
                    for col in match_columns:
                        try:
                            property_obj = db.session.execute(text(
                                f"SELECT id FROM properties WHERE LOWER(TRIM(COALESCE({col}, ''))) = :subdomain LIMIT 1"
                            ), {'subdomain': subdomain}).first()
                            
                            if property_obj:
                                return property_obj[0]
                        except Exception:
                            continue
                except Exception:
                    pass
        
        return None
    except Exception as e:
        current_app.logger.warning(f"Error in get_property_id_from_request: {str(e)}")
        return None

@feedback_bp.route('/', methods=['GET'])
@jwt_required()
def get_feedback():
    """
    Get feedback
    ---
    tags:
      - Feedback
    summary: Get feedback for the current user
    description: Tenants can only see their own feedback. Property managers/staff can see all feedback for their property.
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
        description: Feedback retrieved successfully
        schema:
          type: object
          properties:
            feedback:
              type: array
              items:
                type: object
            total:
              type: integer
            pages:
              type: integer
      401:
        description: Unauthorized
      404:
        description: User not found
      500:
        description: Server error
    """
    try:
        user_id = get_jwt_identity()
        # Convert user_id to int if it's a string (JWT identity might be string)
        try:
            user_id_int = int(user_id) if user_id else None
        except (ValueError, TypeError):
            user_id_int = None
        
        if not user_id_int:
            return jsonify({'error': 'User not authenticated'}), 401
        
        current_user = User.query.get(user_id_int)
        
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Get property_id from request (subdomain, header, query param, etc.)
        property_id = get_property_id_from_request()
        
        # Get user role (handle both enum and string)
        if isinstance(current_user.role, UserRole):
            user_role = current_user.role.value.upper()
        else:
            user_role = str(current_user.role).upper()
        
        # Build query
        query = Feedback.query
        
        # Tenants can only see their own feedback
        if user_role == 'TENANT':
            # Use integer user_id to match database type (submitted_by is INT)
            query = query.filter(Feedback.submitted_by == user_id_int)
            
            # Also filter by property if available
            if property_id:
                query = query.filter(Feedback.property_id == property_id)
        
        # Property managers and staff can see all feedback for their property
        elif user_role in ['MANAGER', 'PROPERTY_MANAGER']:
            # CRITICAL: Property managers MUST provide property_id
            if not property_id:
                return jsonify({
                    'error': 'Property context is required. Please access through a property subdomain.',
                    'code': 'PROPERTY_CONTEXT_REQUIRED'
                }), 400
            
            # CRITICAL: Verify property exists and user owns it
            property_obj = Property.query.get(property_id)
            if not property_obj:
                return jsonify({'error': 'Property not found'}), 404
            
            if property_obj.owner_id != current_user.id:
                return jsonify({
                    'error': 'Access denied. You do not own this property.',
                    'code': 'PROPERTY_ACCESS_DENIED'
                }), 403
            
            # Filter by property_id
            query = query.filter(Feedback.property_id == property_id)
        
        elif user_role == 'STAFF':
            # Staff can see feedback for their property if property_id is provided
            if property_id:
                query = query.filter(Feedback.property_id == property_id)
            # If no property_id, staff see all (they work for a specific property via staff.property_id)
        
        else:
            return jsonify({'error': 'Access denied'}), 403
        
        # Apply filters from query parameters
        feedback_type = request.args.get('type')
        if feedback_type:
            query = query.filter(Feedback.feedback_type == feedback_type.lower())
        
        status = request.args.get('status')
        if status:
            query = query.filter(Feedback.status == status.lower())
        
        # Search filter (subject or message)
        search = request.args.get('search')
        if search:
            from sqlalchemy import func
            search_pattern = f'%{search}%'
            query = query.filter(
                or_(
                    func.lower(Feedback.subject).like(func.lower(search_pattern)),
                    func.lower(Feedback.message).like(func.lower(search_pattern))
                )
            )
        
        # Order by creation date (newest first)
        query = query.order_by(Feedback.created_at.desc())
        
        # Execute query
        feedback_list = query.all()
        
        # Serialize feedback
        feedback_data = []
        for feedback in feedback_list:
            try:
                feedback_dict = feedback.to_dict()
                feedback_data.append(feedback_dict)
            except Exception as e:
                current_app.logger.warning(f"Error serializing feedback {feedback.id}: {str(e)}")
                continue
        
        return jsonify({
            'feedback': feedback_data,
            'count': len(feedback_data)
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Get feedback error: {str(e)}", exc_info=True)
        error_message = str(e)
        if current_app.config.get('DEBUG'):
            return jsonify({
                'error': 'Failed to fetch feedback',
                'details': error_message,
                'type': type(e).__name__
            }), 500
        return jsonify({'error': 'Failed to fetch feedback'}), 500

@feedback_bp.route('/', methods=['POST'])
@jwt_required()
def create_feedback():
    """
    Create feedback
    ---
    tags:
      - Feedback
    summary: Create new feedback/complaint
    description: Create new feedback/complaint. Only tenants can create feedback.
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
            - message
          properties:
            subject:
              type: string
            message:
              type: string
            category:
              type: string
    responses:
      201:
        description: Feedback created successfully
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
        description: Forbidden - Tenant access required
      500:
        description: Server error
    """
    try:
        user_id = get_jwt_identity()
        # Convert user_id to int if it's a string (JWT identity might be string)
        try:
            user_id_int = int(user_id) if user_id else None
        except (ValueError, TypeError):
            user_id_int = None
        
        if not user_id_int:
            return jsonify({'error': 'User not authenticated'}), 401
        
        current_user = User.query.get(user_id_int)
        
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Get user role (handle both enum and string)
        if isinstance(current_user.role, UserRole):
            user_role = current_user.role.value.upper()
        else:
            user_role = str(current_user.role).upper()
        
        # Tenants and staff can create feedback
        if user_role not in ['TENANT', 'STAFF']:
            return jsonify({'error': 'Only tenants and staff can submit feedback'}), 403
        
        # Get request data and handle case where it might be a string
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
        
        # Validate required fields safely
        raw_message = data.get('message')
        if raw_message is None:
            return jsonify({'error': 'Message is required'}), 400
        try:
            message = str(raw_message).strip()
        except Exception:
            return jsonify({'error': 'Message is required'}), 400
        if not message:
            return jsonify({'error': 'Message is required'}), 400

        # Optional subject, safely normalized
        raw_subject = data.get('subject')
        subject = None
        if raw_subject is not None:
            try:
                subject = str(raw_subject).strip()
            except Exception:
                subject = None
        
        # Get property_id from request (subdomain, header, query param, etc.) or from body
        property_id = get_property_id_from_request(data=data)
        
        # If property_id is still None, try to get from user's property assignment
        if not property_id:
            try:
                if user_role == 'TENANT':
                    # Get tenant record (use integer user_id)
                    tenant = Tenant.query.filter_by(user_id=user_id_int).first()
                    if tenant and tenant.property_id:
                        property_id = tenant.property_id
                    else:
                        # Try to get from tenant_units (active lease)
                        from models.tenant import TenantUnit
                        from datetime import date
                        today = date.today()
                        
                        active_unit = db.session.execute(text("""
                            SELECT property_id 
                            FROM tenant_units 
                            WHERE tenant_id = :tenant_id 
                            AND (move_out_date IS NULL OR move_out_date >= :today)
                            LIMIT 1
                        """), {
                            'tenant_id': tenant.id if tenant else None,
                            'today': today
                        }).first()
                        
                        if active_unit:
                            property_id = active_unit[0]
                elif user_role == 'STAFF':
                    # Staff get property from subdomain or use first property
                    # Property ID should already be set from get_property_id_from_request
                    # If still None, staff can submit feedback without property_id (optional for staff)
                    pass
            except Exception as e:
                current_app.logger.warning(f"Error getting property_id: {str(e)}")
        
        # Property ID is required for tenants, optional for staff
        if user_role == 'TENANT' and not property_id:
            return jsonify({'error': 'Property context is required. Please ensure you are logged in through your property portal.'}), 400
        
        # Validate feedback_type (optional)
        raw_feedback_type = data.get('feedback_type', 'other')
        feedback_type = str(raw_feedback_type).lower() if raw_feedback_type is not None else 'other'
        valid_types = ['complaint', 'suggestion', 'compliment', 'other']
        if feedback_type not in valid_types:
            feedback_type = 'other'
        
        # Validate status (default to 'new')
        raw_status = data.get('status', 'new')
        status = str(raw_status).lower() if raw_status is not None else 'new'
        valid_statuses = ['new', 'reviewed', 'responded', 'resolved']
        if status not in valid_statuses:
            status = 'new'
        
        # Get optional fields safely
        raw_subject = data.get('subject')
        subject = None
        if raw_subject is not None:
            try:
                s = str(raw_subject).strip()
                if s:
                    subject = s
            except Exception:
                subject = None

        rating = data.get('rating')
        if rating is not None:
            try:
                rating = int(rating)
                if rating < 1 or rating > 5:
                    rating = None
            except (ValueError, TypeError):
                rating = None
        
        # Create feedback
        feedback = Feedback(
            subject=subject,
            message=message,
            feedback_type=feedback_type,
            rating=rating,
            submitted_by=user_id_int,  # Use integer user_id
            property_id=property_id,
            status=status
        )
        
        db.session.add(feedback)
        db.session.commit()
        
        # Create notification for property manager (only for tenant feedback)
        if user_role == 'TENANT' and property_id:
            try:
                from services.notification_service import NotificationService
                NotificationService.notify_pm_feedback_submitted(feedback)
            except Exception as notif_error:
                # Don't fail feedback submission if notification fails
                current_app.logger.warning(f"Failed to create PM notification for feedback {feedback.id}: {str(notif_error)}")
        
        # Return created feedback
        feedback_dict = feedback.to_dict()
        return jsonify({
            'message': 'Feedback submitted successfully',
            'feedback': feedback_dict
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Create feedback error: {str(e)}", exc_info=True)
        error_message = str(e)
        if current_app.config.get('DEBUG'):
            return jsonify({
                'error': 'Failed to create feedback',
                'details': error_message,
                'type': type(e).__name__
            }), 500
        return jsonify({'error': 'Failed to create feedback'}), 500

@feedback_bp.route('/<int:feedback_id>', methods=['GET'])
@jwt_required()
def get_feedback_detail(feedback_id):
    """
    Get feedback by ID
    ---
    tags:
      - Feedback
    summary: Get specific feedback by ID
    description: Tenants can only see their own feedback. Property managers/staff can see all feedback for their property.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: feedback_id
        type: integer
        required: true
        description: The feedback ID
    responses:
      200:
        description: Feedback retrieved successfully
        schema:
          type: object
          properties:
            feedback:
              type: object
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Feedback not found
      500:
        description: Server error
    """
    try:
        user_id = get_jwt_identity()
        # Convert user_id to int if it's a string (JWT identity might be string)
        try:
            user_id_int = int(user_id) if user_id else None
        except (ValueError, TypeError):
            user_id_int = None
        
        if not user_id_int:
            return jsonify({'error': 'User not authenticated'}), 401
        
        current_user = User.query.get(user_id_int)
        
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Get feedback
        feedback = Feedback.query.get(feedback_id)
        if not feedback:
            return jsonify({'error': 'Feedback not found'}), 404
        
        # Get user role (handle both enum and string)
        if isinstance(current_user.role, UserRole):
            user_role = current_user.role.value.upper()
        else:
            user_role = str(current_user.role).upper()
        
        # Tenants can only see their own feedback
        if user_role == 'TENANT':
            # Compare with integer to match database type (submitted_by is INT)
            if feedback.submitted_by != user_id_int:
                current_app.logger.warning(
                    f"Access denied: Tenant {user_id_int} tried to view feedback {feedback_id} "
                    f"submitted by {feedback.submitted_by}"
                )
                return jsonify({'error': 'Access denied. You can only view your own feedback.'}), 403
        
        # Property managers and staff can see all feedback for their property
        elif user_role in ['MANAGER', 'PROPERTY_MANAGER', 'STAFF']:
            # Get property_id from request
            property_id = get_property_id_from_request()
            
            # If property_id is provided and doesn't match feedback property, deny access
            if property_id and feedback.property_id != property_id:
                return jsonify({'error': 'Access denied'}), 403
        else:
            return jsonify({'error': 'Access denied'}), 403
        
        # Return feedback
        feedback_dict = feedback.to_dict()
        return jsonify({'feedback': feedback_dict}), 200
        
    except Exception as e:
        current_app.logger.error(f"Get feedback detail error: {str(e)}", exc_info=True)
        error_message = str(e)
        if current_app.config.get('DEBUG'):
            return jsonify({
                'error': 'Failed to fetch feedback',
                'details': error_message,
                'type': type(e).__name__
            }), 500
        return jsonify({'error': 'Failed to fetch feedback'}), 500

@feedback_bp.route('/<int:feedback_id>', methods=['PUT'])
@jwt_required()
def update_feedback(feedback_id):
    """
    Update feedback
    ---
    tags:
      - Feedback
    summary: Update feedback status
    description: Update feedback status. Only property managers can update feedback status (staff cannot respond to tenant feedback).
    security:
      - Bearer: []
    parameters:
      - in: path
        name: feedback_id
        type: integer
        required: true
        description: The feedback ID
      - in: body
        name: body
        schema:
          type: object
          properties:
            status:
              type: string
            response:
              type: string
    responses:
      200:
        description: Feedback updated successfully
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
        description: Forbidden - Property Manager access required
      404:
        description: Feedback not found
      500:
        description: Server error
    """
    try:
        user_id = get_jwt_identity()
        try:
            user_id_int = int(user_id) if user_id else None
        except (ValueError, TypeError):
            user_id_int = None
        
        if not user_id_int:
            return jsonify({'error': 'User not authenticated'}), 401
        
        current_user = User.query.get(user_id_int)
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Get user role
        if isinstance(current_user.role, UserRole):
            user_role = current_user.role.value.upper()
        else:
            user_role = str(current_user.role).upper()
        
        feedback = Feedback.query.get(feedback_id)
        if not feedback:
            return jsonify({'error': 'Feedback not found'}), 404
        
        # Get update data and handle case where it might be a string
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
        

        # Tenant can edit own feedback only while status is 'new'
        if user_role == 'TENANT':
            if feedback.submitted_by != user_id_int:
                return jsonify({'error': 'Access denied'}), 403
            if str(feedback.status).lower() != 'new':
                return jsonify({'error': 'Feedback can no longer be edited'}), 403

            # Tenant edit: allow subject/message/feedback_type/rating
            if 'message' in data:
                try:
                    msg = str(data.get('message', '')).strip()
                except Exception:
                    msg = ''
                if not msg:
                    return jsonify({'error': 'Message is required'}), 400
                feedback.message = msg

            if 'subject' in data:
                try:
                    subj = str(data.get('subject', '')).strip()
                    feedback.subject = subj if subj else None
                except Exception:
                    feedback.subject = None

            if 'feedback_type' in data:
                ftype = str(data.get('feedback_type', 'other')).lower()
                if ftype in ['complaint', 'suggestion', 'compliment', 'other']:
                    feedback.feedback_type = ftype

            if 'rating' in data:
                rating_val = data.get('rating')
                try:
                    rating_val = int(rating_val)
                    if 1 <= rating_val <= 5:
                        feedback.rating = rating_val
                    else:
                        feedback.rating = None
                except Exception:
                    feedback.rating = None

            db.session.commit()
            feedback_dict = feedback.to_dict()
            return jsonify({
                'message': 'Feedback updated successfully',
                'feedback': feedback_dict
            }), 200

        # Only property managers can update status/response
        if user_role not in ['MANAGER', 'PROPERTY_MANAGER']:
            return jsonify({'error': 'Access denied. Staff cannot update feedback status.'}), 403

        # Get property_id from request
        property_id = get_property_id_from_request()

        # Verify feedback belongs to property if property_id is provided
        if property_id and feedback.property_id != property_id:
            return jsonify({'error': 'Access denied'}), 403

        # Update status if provided
        if 'status' in data:
            new_status = data.get('status', '').lower()
            valid_statuses = ['new', 'reviewed', 'responded', 'resolved']
            if new_status in valid_statuses:
                feedback.status = new_status

        db.session.commit()

        # Return updated feedback
        feedback_dict = feedback.to_dict()
        return jsonify({
            'message': 'Feedback updated successfully',
            'feedback': feedback_dict
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update feedback error: {str(e)}", exc_info=True)
        if current_app.config.get('DEBUG'):
            return jsonify({
                'error': 'Failed to update feedback',
                'details': str(e),
                'type': type(e).__name__
            }), 500
        return jsonify({'error': 'Failed to update feedback'}), 500

@feedback_bp.route('/dashboard', methods=['GET'])
@jwt_required()
def get_feedback_dashboard():
    """
    Get feedback dashboard
    ---
    tags:
      - Feedback
    summary: Get feedback dashboard statistics
    description: Get feedback dashboard statistics for property managers/staff
    security:
      - Bearer: []
    responses:
      200:
        description: Dashboard statistics retrieved successfully
        schema:
          type: object
          properties:
            total:
              type: integer
            by_status:
              type: object
            by_category:
              type: object
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        user_id = get_jwt_identity()
        try:
            user_id_int = int(user_id) if user_id else None
        except (ValueError, TypeError):
            user_id_int = None
        
        if not user_id_int:
            return jsonify({'error': 'User not authenticated'}), 401
        
        current_user = User.query.get(user_id_int)
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Get user role
        if isinstance(current_user.role, UserRole):
            user_role = current_user.role.value.upper()
        else:
            user_role = str(current_user.role).upper()
        
        # Only property managers and staff can see dashboard
        if user_role not in ['MANAGER', 'PROPERTY_MANAGER', 'STAFF']:
            return jsonify({'error': 'Access denied'}), 403
        
        # Get property_id from request
        property_id = get_property_id_from_request()
        
        # Build query
        query = Feedback.query
        if property_id:
            query = query.filter(Feedback.property_id == property_id)
        
        # Get all feedback for statistics
        all_feedback = query.all()
        
        # Calculate statistics
        total = len(all_feedback)
        new_count = sum(1 for f in all_feedback if str(f.status).lower() == 'new')
        reviewed_count = sum(1 for f in all_feedback if str(f.status).lower() == 'reviewed')
        responded_count = sum(1 for f in all_feedback if str(f.status).lower() == 'responded')
        resolved_count = sum(1 for f in all_feedback if str(f.status).lower() == 'resolved')
        
        return jsonify({
            'total': total,
            'new': new_count,
            'in_review': reviewed_count,  # Map 'reviewed' to 'in_review' for frontend
            'reviewed': reviewed_count,
            'responded': responded_count,
            'resolved': resolved_count
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Get feedback dashboard error: {str(e)}", exc_info=True)
        if current_app.config.get('DEBUG'):
            return jsonify({
                'error': 'Failed to load feedback dashboard',
                'details': str(e),
                'type': type(e).__name__
            }), 500
        return jsonify({'error': 'Failed to load feedback dashboard'}), 500

