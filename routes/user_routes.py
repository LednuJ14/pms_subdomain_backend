from flask import Blueprint, request, jsonify, current_app, send_from_directory
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from werkzeug.utils import secure_filename
from datetime import datetime, timezone
import os

from app import db
from models.user import User, UserRole

user_bp = Blueprint('users', __name__)

def allowed_file(filename):
    """Check if file extension is allowed."""
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@user_bp.route('/', methods=['GET'])
@jwt_required()
def get_users():
    """
    Get users
    ---
    tags:
      - Users
    summary: Get list of users for the current property
    description: Get list of users for the current property. Property manager only.
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
        name: role
        type: string
    responses:
      200:
        description: Users retrieved successfully
        schema:
          type: object
          properties:
            users:
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
        description: Forbidden - Property Manager access required
      500:
        description: Server error
    """
    try:
        claims = get_jwt()
        if claims.get('role') != 'property_manager':
            return jsonify({'error': 'Insufficient permissions'}), 403
        
        current_user_id = get_jwt_identity()
        if isinstance(current_user_id, str):
            try:
                current_user_id = int(current_user_id)
            except ValueError:
                return jsonify({'error': 'Invalid user ID'}), 400
        
        # CRITICAL: Get property_id from subdomain context
        from routes.auth_routes import get_property_id_from_request
        property_id = get_property_id_from_request()
        
        if not property_id:
            # Try JWT claims
            try:
                property_id = claims.get('property_id')
            except Exception:
                pass
        
        if not property_id:
            return jsonify({
                'error': 'Property context is required. Please access through a property subdomain.',
                'code': 'PROPERTY_CONTEXT_REQUIRED'
            }), 400
        
        # Verify ownership
        from models.property import Property
        property_obj = Property.query.get(property_id)
        if not property_obj:
            return jsonify({'error': 'Property not found'}), 404
        
        if property_obj.owner_id != current_user_id:
            return jsonify({
                'error': 'Access denied. You do not own this property.',
                'code': 'PROPERTY_ACCESS_DENIED'
            }), 403
        
        # Filter users by property
        # Get tenants for this property
        from models.tenant import Tenant
        tenant_ids = [t.user_id for t in Tenant.query.filter_by(property_id=property_id).all() if t.user_id]
        
        # Get staff for this property
        from models.staff import Staff
        staff_ids = [s.user_id for s in Staff.query.filter_by(property_id=property_id).all() if s.user_id]
        
        # Combine user IDs (include property manager)
        user_ids = list(set(tenant_ids + staff_ids + [current_user_id]))
        
        if not user_ids:
            return jsonify({'users': []}), 200
        
        # Base query - filter by property-related users
        query = User.query.filter(User.id.in_(user_ids))
        
        # Get query parameters
        role_filter = request.args.get('role')
        
        # Apply role filter
        if role_filter:
            try:
                # Use from_string to handle case-insensitive role values
                role_enum = UserRole.from_string(role_filter)
                # Compare using the enum value (string) since database stores it as string
                query = query.filter(User.role == role_enum.value)
            except (ValueError, KeyError, AttributeError) as e:
                current_app.logger.error(f"Invalid role filter '{role_filter}': {str(e)}")
                return jsonify({'error': f'Invalid role: {role_filter}'}), 400
        
        users = query.all()
        
        # Serialize users with error handling
        users_list = []
        for user in users:
            try:
                users_list.append(user.to_dict())
            except Exception as user_error:
                current_app.logger.warning(f"Error serializing user {user.id}: {str(user_error)}")
                # Skip users that can't be serialized
                continue
        
        return jsonify({
            'users': users_list
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Get users error: {str(e)}", exc_info=True)
        return jsonify({'error': 'Failed to retrieve users', 'details': str(e) if current_app.config.get('DEBUG') else None}), 500

@user_bp.route('/profile', methods=['GET'])
@jwt_required()
def get_profile():
    """
    Get profile
    ---
    tags:
      - Users
    summary: Get current user profile
    description: Retrieve the current authenticated user's profile
    security:
      - Bearer: []
    responses:
      200:
        description: Profile retrieved successfully
        schema:
          type: object
          properties:
            user:
              type: object
            profile:
              type: object
      401:
        description: Unauthorized
      404:
        description: User not found
      500:
        description: Server error
    """
    try:
        current_user_id = get_jwt_identity()
        user = User.query.get(current_user_id)
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        return jsonify({'user': user.to_dict()}), 200
        
    except Exception as e:
        current_app.logger.error(f"Get profile error: {str(e)}")
        return jsonify({'error': 'Failed to get profile'}), 500

@user_bp.route('/profile', methods=['PUT'])
@jwt_required()
def update_profile():
    """
    Update profile
    ---
    tags:
      - Users
    summary: Update current user profile
    description: Update the current authenticated user's profile
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            first_name:
              type: string
            last_name:
              type: string
            phone_number:
              type: string
            address:
              type: string
    responses:
      200:
        description: Profile updated successfully
        schema:
          type: object
          properties:
            message:
              type: string
            user:
              type: object
      400:
        description: Validation error
      401:
        description: Unauthorized
      404:
        description: User not found
      500:
        description: Server error
    """
    try:
        current_user_id = get_jwt_identity()
        user = User.query.get(current_user_id)
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Update allowed fields
        updatable_fields = ['first_name', 'last_name', 'phone_number', 'address', 
                           'emergency_contact_name', 'emergency_contact_phone']
        
        for field in updatable_fields:
            if field in data:
                value = data[field]
                # Convert empty strings to None for optional fields
                if value == '' or value is None:
                    value = None
                setattr(user, field, value)
                current_app.logger.info(f"Updated {field} for user {current_user_id}: {value}")
        
        db.session.commit()
        current_app.logger.info(f"Successfully committed profile update for user {current_user_id}")
        
        # Refresh the user object to ensure we have the latest data
        db.session.refresh(user)
        
        return jsonify({
            'message': 'Profile updated successfully',
            'user': user.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update profile error: {str(e)}")
        return jsonify({'error': 'Failed to update profile'}), 500

@user_bp.route('/profile/image', methods=['POST'])
@jwt_required()
def upload_profile_image():
    """
    Upload profile image
    ---
    tags:
      - Users
    summary: Upload profile image for current user
    description: Upload profile image for the current authenticated user
    security:
      - Bearer: []
    parameters:
      - in: formData
        name: image
        type: file
        required: true
        description: Profile image file
    responses:
      200:
        description: Profile image uploaded successfully
        schema:
          type: object
          properties:
            message:
              type: string
            image_url:
              type: string
      400:
        description: Validation error
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        current_user_id = get_jwt_identity()
        user = User.query.get(current_user_id)
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type. Allowed: PNG, JPG, JPEG, GIF, WEBP'}), 400
        
        # Check file size (2MB max)
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        
        if file_size > 2 * 1024 * 1024:  # 2MB
            return jsonify({'error': 'File size exceeds 2MB limit'}), 400
        
        # Create upload directory if it doesn't exist
        upload_dir = os.path.join(current_app.instance_path, 'uploads', 'profile_images')
        os.makedirs(upload_dir, exist_ok=True)
        
        # Generate secure filename
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{current_user_id}_{timestamp}_{filename}"
        filepath = os.path.join(upload_dir, filename)
        
        # Save file
        file.save(filepath)
        
        # Generate URL (relative path - will be served by Flask route)
        image_url = f"/api/users/profile/image/{filename}"
        
        # Update user profile image URL
        user.profile_image_url = image_url
        user.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        
        return jsonify({
            'message': 'Profile image uploaded successfully',
            'image_url': image_url,
            'user': user.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Upload profile image error: {str(e)}")
        return jsonify({'error': 'Failed to upload profile image'}), 500

@user_bp.route('/profile/image/<filename>', methods=['GET'])
def get_profile_image(filename):
    """
    Get profile image
    ---
    tags:
      - Users
    summary: Serve profile image file
    description: Serve profile image file (public endpoint)
    parameters:
      - in: path
        name: filename
        type: string
        required: true
        description: The image filename
    responses:
      200:
        description: Profile image file
        schema:
          type: file
      404:
        description: Image not found
      500:
        description: Server error
    """
    try:
        upload_dir = os.path.join(current_app.instance_path, 'uploads', 'profile_images')
        return send_from_directory(upload_dir, filename)
    except Exception as e:
        current_app.logger.error(f"Get profile image error: {str(e)}")
        return jsonify({'error': 'Image not found'}), 404