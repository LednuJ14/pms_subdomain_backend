from flask import Blueprint, jsonify, request, current_app, send_from_directory
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.utils import secure_filename
import os
from datetime import datetime, timezone
from app import db
from models.property import Property, Unit
from models.user import User

property_bp = Blueprint('properties', __name__)

from models.user import User

def is_super_admin(user_id):
    if not user_id: return False
    user = User.query.get(user_id)
    return user and getattr(user, 'role', '') == 'ADMIN'


def allowed_file(filename):
    """Check if file extension is allowed."""
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'svg'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@property_bp.route('/', methods=['GET'])
@jwt_required()
def get_properties():
    """
    Get property
    ---
    tags:
      - Properties
    summary: Get the current property for the subdomain context
    description: Retrieve the current property based on subdomain context
    security:
      - Bearer: []
    responses:
      200:
        description: Property retrieved successfully
        schema:
          type: object
          properties:
            property:
              type: object
      401:
        description: Unauthorized
      404:
        description: Property not found
      500:
        description: Server error
    """
    try:
        current_user_id = get_jwt_identity()
        
        # Convert string to int if needed
        if isinstance(current_user_id, str):
            try:
                current_user_id = int(current_user_id)
            except ValueError:
                return jsonify({'error': 'Invalid user ID'}), 400
        
        user = User.query.get(current_user_id)
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        # CRITICAL: Get property_id from subdomain context
        from routes.auth_routes import get_property_id_from_request
        property_id = get_property_id_from_request()
        
        if not property_id:
            # Try JWT claims
            from flask_jwt_extended import get_jwt
            try:
                claims = get_jwt()
                property_id = claims.get('property_id')
            except Exception:
                pass
        
        if property_id:
            # Return only the current property
            property_obj = Property.query.get(property_id)
            if not property_obj:
                return jsonify({'error': 'Property not found'}), 404
            
            # Verify ownership
            if property_obj.owner_id != user.id and not is_super_admin(user.id):
                return jsonify({
                    'error': 'Access denied. You do not own this property.',
                    'code': 'PROPERTY_ACCESS_DENIED'
                }), 403
            
            try:
                prop_dict = property_obj.to_dict()
                return jsonify([prop_dict]), 200
            except Exception as prop_error:
                current_app.logger.warning(f"Error converting property {property_obj.id} to dict: {str(prop_error)}", exc_info=True)
                # Return basic property info if to_dict fails
                try:
                    display_settings = getattr(property_obj, 'display_settings', None) or {}
                except Exception:
                    display_settings = {}
                
                return jsonify([{
                    'id': property_obj.id,
                    'name': getattr(property_obj, 'name', 'Unknown'),
                    'address': getattr(property_obj, 'address', ''),
                    'city': getattr(property_obj, 'city', ''),
                    'display_settings': display_settings
                }]), 200
        else:
            # No property context - return empty array (don't leak other properties)
            return jsonify([]), 200
    except Exception as e:
        current_app.logger.error(f"Get properties error: {str(e)}", exc_info=True)
        return jsonify({'error': 'Failed to get properties', 'details': str(e) if current_app.config.get('DEBUG') else None}), 500

@property_bp.route('/<int:property_id>/display-settings', methods=['GET'])
@jwt_required()
def get_display_settings(property_id):
    """
    Get display settings
    ---
    tags:
      - Properties
    summary: Get display settings for a property
    description: Retrieve display settings for a property
    security:
      - Bearer: []
    parameters:
      - in: path
        name: property_id
        type: integer
        required: true
        description: The property ID
    responses:
      200:
        description: Display settings retrieved successfully
        schema:
          type: object
          properties:
            display_settings:
              type: object
      401:
        description: Unauthorized
      404:
        description: Property not found
      500:
        description: Server error
    """
    try:
        current_user_id = get_jwt_identity()
        
        # Convert string to int if needed (JWT returns string)
        if isinstance(current_user_id, str):
            try:
                current_user_id = int(current_user_id)
            except ValueError:
                return jsonify({'error': 'Invalid user ID'}), 400
        
        property_obj = Property.query.get(property_id)
        
        if not property_obj:
            return jsonify({'error': 'Property not found'}), 404
        
        # Verify user is the owner/manager (use owner_id since manager_id doesn't exist)
        # Compare as integers to avoid type mismatch
        if int(property_obj.owner_id) != int(current_user_id) and not is_super_admin(current_user_id):
            current_app.logger.warning(f"Authorization failed for GET: property owner_id={property_obj.owner_id} (type: {type(property_obj.owner_id)}), user_id={current_user_id} (type: {type(current_user_id)})")
            return jsonify({'error': 'Unauthorized'}), 403
        
        # Get display settings or return defaults
        # display_settings is stored as JSON string in database, parse it
        import json
        display_settings = {}
        if property_obj.display_settings:
            try:
                if isinstance(property_obj.display_settings, str):
                    display_settings = json.loads(property_obj.display_settings)
                elif isinstance(property_obj.display_settings, dict):
                    display_settings = property_obj.display_settings
            except (json.JSONDecodeError, TypeError):
                display_settings = {}
        
        # If no display settings, use defaults
        if not display_settings:
            display_settings = {
            'companyName': property_obj.name or 'PMS',
            'propertyName': property_obj.name or 'PMS',
            'logoUrl': '',
            'primaryColor': '#000000',
            'secondaryColor': '#3B82F6',
            'accentColor': '#10B981',
            'backgroundImage': '',
            'loginLayout': 'modern',
            'websiteTheme': 'light',
            'headerStyle': 'fixed',
            'sidebarStyle': 'collapsible',
            'borderRadius': 'medium',
            'fontFamily': 'inter',
            'fontSize': 'medium',
            'staffManagementEnabled': True  # Default to enabled
            }
        
        return jsonify(display_settings), 200
    except Exception as e:
        current_app.logger.error(f"Get display settings error: {str(e)}")
        return jsonify({'error': 'Failed to get display settings'}), 500

@property_bp.route('/<int:property_id>/display-settings', methods=['PUT'])
@jwt_required()
def update_display_settings(property_id):
    """
    Update display settings
    ---
    tags:
      - Properties
    summary: Update display settings for a property
    description: Update display settings for a property. Property Manager only.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: property_id
        type: integer
        required: true
        description: The property ID
      - in: body
        name: body
        schema:
          type: object
          properties:
            primary_color:
              type: string
            secondary_color:
              type: string
            logo_url:
              type: string
    responses:
      200:
        description: Display settings updated successfully
        schema:
          type: object
          properties:
            message:
              type: string
            display_settings:
              type: object
      400:
        description: Validation error
      401:
        description: Unauthorized
      403:
        description: Forbidden - Property Manager access required
      404:
        description: Property not found
      500:
        description: Server error
    """
    try:
        current_user_id = get_jwt_identity()
        
        # Convert string to int if needed (JWT returns string)
        if isinstance(current_user_id, str):
            try:
                current_user_id = int(current_user_id)
            except ValueError:
                return jsonify({'error': 'Invalid user ID'}), 400
        
        property_obj = Property.query.get(property_id)
        
        if not property_obj:
            return jsonify({'error': 'Property not found'}), 404
        
        # Verify user is the owner/manager (use owner_id since manager_id doesn't exist)
        # Compare as integers to avoid type mismatch
        if int(property_obj.owner_id) != int(current_user_id) and not is_super_admin(current_user_id):
            current_app.logger.warning(f"Authorization failed for PUT: property owner_id={property_obj.owner_id} (type: {type(property_obj.owner_id)}), user_id={current_user_id} (type: {type(current_user_id)})")
            return jsonify({'error': 'Unauthorized'}), 403
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Get existing settings or create new
        # display_settings is stored as JSON string in database, parse it
        import json
        display_settings = {}
        if property_obj.display_settings:
            try:
                if isinstance(property_obj.display_settings, str):
                    display_settings = json.loads(property_obj.display_settings)
                elif isinstance(property_obj.display_settings, dict):
                    display_settings = property_obj.display_settings
                else:
                    display_settings = {}
            except (json.JSONDecodeError, TypeError):
                display_settings = {}
        
        # Update allowed fields (include staffManagementEnabled and propertyName)
        allowed_fields = [
            'companyName', 'propertyName', 'logoUrl', 'primaryColor', 
            'secondaryColor', 'accentColor', 'backgroundImage', 'loginLayout',
            'websiteTheme', 'headerStyle', 'sidebarStyle', 'borderRadius',
            'fontFamily', 'fontSize', 'staffManagementEnabled'
        ]
        
        for field in allowed_fields:
            if field in data:
                display_settings[field] = data[field]
        
        # Sync propertyName to companyName for backward compatibility
        if 'propertyName' in data and 'companyName' not in data:
            display_settings['companyName'] = data['propertyName']
        elif 'companyName' in data and 'propertyName' not in data:
            display_settings['propertyName'] = data['companyName']
        
        # Update property - store as JSON string
        property_obj.display_settings = json.dumps(display_settings) if display_settings else None
        property_obj.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        
        return jsonify({
            'message': 'Display settings updated successfully',
            'display_settings': display_settings
        }), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update display settings error: {str(e)}")
        return jsonify({'error': 'Failed to update display settings'}), 500

@property_bp.route('/<int:property_id>/logo', methods=['POST'])
@jwt_required()
def upload_logo(property_id):
    """
    Upload logo
    ---
    tags:
      - Properties
    summary: Upload logo for a property
    description: Upload logo for a property. Property Manager only.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: property_id
        type: integer
        required: true
        description: The property ID
      - in: formData
        name: logo
        type: file
        required: true
        description: Logo image file
    responses:
      200:
        description: Logo uploaded successfully
        schema:
          type: object
          properties:
            message:
              type: string
            logo_url:
              type: string
      400:
        description: Validation error
      401:
        description: Unauthorized
      403:
        description: Forbidden - Property Manager access required
      404:
        description: Property not found
      500:
        description: Server error
    """
    try:
        current_user_id = get_jwt_identity()
        property_obj = Property.query.get(property_id)
        
        if not property_obj:
            return jsonify({'error': 'Property not found'}), 404
        
        # Verify user is the owner/manager (use owner_id since manager_id doesn't exist)
        # Convert string to int if needed
        if isinstance(current_user_id, str):
            try:
                current_user_id = int(current_user_id)
            except ValueError:
                return jsonify({'error': 'Invalid user ID'}), 400
        
        if int(property_obj.owner_id) != int(current_user_id) and not is_super_admin(current_user_id):
            return jsonify({'error': 'Unauthorized'}), 403
        
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type. Allowed: PNG, JPG, JPEG, GIF, SVG'}), 400
        
        # Check file size (2MB max)
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        
        if file_size > 2 * 1024 * 1024:  # 2MB
            return jsonify({'error': 'File size exceeds 2MB limit'}), 400
        
        # Check if Cloudinary is configured
        if current_app.config.get('CLOUDINARY_CLOUD_NAME'):
            from utils.cloudinary_helpers import upload_to_cloudinary
            success, logo_url, error = upload_to_cloudinary(
                file, 
                folder=f"jacs/subdomain/properties/{property_id}/logo"
            )
            if not success:
                return jsonify({'error': error}), 400
        else:
            # Create upload directory if it doesn't exist
            upload_dir = os.path.join(current_app.instance_path, 'uploads', 'logos')
            os.makedirs(upload_dir, exist_ok=True)
            
            # Generate secure filename
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{property_id}_{timestamp}_{filename}"
            filepath = os.path.join(upload_dir, filename)
            
            # Save file
            file.save(filepath)
            
            # Generate URL (relative path - will be served by Flask route)
            logo_url = f"/api/properties/{property_id}/logo/{filename}"
        
        # Update display settings - parse existing JSON string first
        import json
        display_settings = {}
        had_logo = False
        
        # Check if logo already existed before updating
        if property_obj.display_settings:
            try:
                if isinstance(property_obj.display_settings, str):
                    old_settings = json.loads(property_obj.display_settings)
                elif isinstance(property_obj.display_settings, dict):
                    old_settings = property_obj.display_settings
                else:
                    old_settings = {}
                
                # Check if logo existed
                had_logo = bool(old_settings.get('logoUrl'))
                # Copy existing settings
                display_settings = old_settings.copy()
            except (json.JSONDecodeError, TypeError):
                display_settings = {}
        
        # Update logo URL
        display_settings['logoUrl'] = logo_url
        
        # Save as JSON string
        property_obj.display_settings = json.dumps(display_settings) if display_settings else None
        property_obj.updated_at = datetime.now(timezone.utc)
        
        # Create notification for property manager about logo update
        try:
            from models.notification import Notification, NotificationType, NotificationPriority
            
            notification_title = 'Logo Updated' if had_logo else 'Logo Uploaded'
            notification_message = f'Your property logo has been {"updated" if had_logo else "uploaded"} successfully. The new logo will appear on the login page and header.'
            
            notification = Notification(
                user_id=current_user_id,
                notification_type=NotificationType.LOGO_UPDATED.value,
                title=notification_title,
                message=notification_message,
                recipient_type='property_manager',
                priority=NotificationPriority.MEDIUM.value,
                related_entity_type='property',
                related_entity_id=property_id,
                action_url=f'/dashboard'  # Link to dashboard where they can see the logo
            )
            db.session.add(notification)
        except Exception as notif_error:
            # Log error but don't fail the logo upload
            current_app.logger.warning(f"Failed to create logo update notification: {str(notif_error)}")
        
        db.session.commit()
        
        return jsonify({
            'message': 'Logo uploaded successfully',
            'logoUrl': logo_url
        }), 200
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Logo upload error: {str(e)}")
        return jsonify({'error': 'Failed to upload logo'}), 500

@property_bp.route('/<int:property_id>/logo/<filename>', methods=['GET'])
def get_logo(property_id, filename):
    """
    Get logo
    ---
    tags:
      - Properties
    summary: Serve logo file
    description: Serve logo file for a property (public endpoint)
    parameters:
      - in: path
        name: property_id
        type: integer
        required: true
        description: The property ID
      - in: path
        name: filename
        type: string
        required: true
        description: The logo filename
    responses:
      200:
        description: Logo file
        schema:
          type: file
      404:
        description: Logo not found
      500:
        description: Server error
    """
    try:
        property_obj = Property.query.get(property_id)
        if not property_obj:
            return jsonify({'error': 'Property not found'}), 404
        
        upload_dir = os.path.join(current_app.instance_path, 'uploads', 'logos')
        return send_from_directory(upload_dir, filename)
    except Exception as e:
        current_app.logger.error(f"Get logo error: {str(e)}")
        return jsonify({'error': 'Failed to get logo'}), 500

@property_bp.route('/<int:property_id>/units', methods=['GET'])
@jwt_required()
def get_property_units(property_id):
    """
    Get property units
    ---
    tags:
      - Properties
    summary: Get all units for a property
    description: Retrieve all units for a property
    security:
      - Bearer: []
    parameters:
      - in: path
        name: property_id
        type: integer
        required: true
        description: The property ID
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
        description: Units retrieved successfully
        schema:
          type: object
          properties:
            units:
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
        description: Property not found
      500:
        description: Server error
    """
    try:
        current_user_id = get_jwt_identity()
        
        # Convert string to int if needed
        if isinstance(current_user_id, str):
            try:
                current_user_id = int(current_user_id)
            except ValueError:
                return jsonify({'error': 'Invalid user ID'}), 400
        
        property_obj = Property.query.get(property_id)
        
        if not property_obj:
            return jsonify({'error': 'Property not found'}), 404
        
        # For subdomain access, allow any authenticated user to view units for the property
        # This is needed because in subdomain context, the user might be a property manager
        # who manages the property but isn't the owner
        # We only verify that the property exists and user is authenticated
        current_app.logger.debug(f"Allowing unit access for authenticated user {current_user_id} to property {property_id} (subdomain context)")
        
        # Get all units for this property - use raw SQL to avoid enum validation issues
        try:
            from sqlalchemy import text
            # Query units directly from database and check for active tenants
            # This ensures status reflects actual occupancy, not just database status
            units_data = db.session.execute(
                text("""
                    SELECT u.id, u.property_id, u.unit_name, u.bedrooms, u.bathrooms, u.size_sqm, 
                           u.monthly_rent, u.security_deposit, u.status, u.description, u.floor_number,
                           CASE 
                               WHEN EXISTS (
                                   SELECT 1 FROM tenant_units tu 
                                   WHERE tu.unit_id = u.id 
                                   AND (tu.move_out_date IS NULL OR tu.move_out_date > CURDATE())
                               ) THEN 1
                               ELSE 0
                           END AS has_active_tenant
                    FROM units u
                    WHERE u.property_id = :property_id
                """),
                {'property_id': property_id}
            ).fetchall()
            
            units_list = []
            for row in units_data:
                try:
                    # Get stored status from database
                    raw_status = row[8]
                    stored_status = str(raw_status).lower().strip() if raw_status else None
                    
                    # Check if unit has an active tenant assignment
                    has_active_tenant = bool(row[11]) if len(row) > 11 else False
                    
                    # Determine final status:
                    # - If unit has active tenant, it's always 'occupied' (regardless of stored status)
                    # - Otherwise, use the stored status from database (vacant, draft, etc.)
                    if has_active_tenant:
                        final_status = 'occupied'
                    elif stored_status:
                        final_status = stored_status
                    else:
                        final_status = 'vacant'  # Default if no status is set
                    
                    # Convert row to dict
                    unit_dict = {
                        'id': row[0],
                        'property_id': row[1],
                        'unit_name': row[2] or f'Unit {row[0]}',
                        'unit_number': row[2] or f'Unit {row[0]}',
                        'name': row[2] or f'Unit {row[0]}',
                        'bedrooms': row[3] or 0,
                        'bathrooms': str(row[4]).lower() if row[4] else 'own',  # Normalize to lowercase
                        'size_sqm': row[5],
                        'monthly_rent': float(row[6]) if row[6] else None,
                        'security_deposit': float(row[7]) if row[7] else None,
                        'status': final_status,  # Use the determined status
                        'description': row[9],
                        'floor_number': row[10]
                    }
                    units_list.append(unit_dict)
                except Exception as row_error:
                    current_app.logger.warning(f"Error processing unit row: {str(row_error)}")
                    # Add minimal data
                    units_list.append({
                        'id': row[0],
                        'property_id': row[1],
                        'unit_name': row[2] or f'Unit {row[0]}',
                        'unit_number': row[2] or f'Unit {row[0]}',
                        'name': row[2] or f'Unit {row[0]}'
                    })
            
            return jsonify(units_list), 200
        except Exception as units_error:
            import traceback
            error_trace = traceback.format_exc()
            current_app.logger.error(f"Error fetching units: {str(units_error)}\n{error_trace}")
            return jsonify({'error': 'Failed to fetch units', 'details': str(units_error) if current_app.config.get('DEBUG') else None}), 500
            
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        current_app.logger.error(f"Get property units error: {str(e)}\n{error_trace}")
        return jsonify({'error': 'Failed to get units', 'details': str(e) if current_app.config.get('DEBUG') else None}), 500

@property_bp.route('/public/by-subdomain', methods=['GET'])
def get_property_by_subdomain():
    """
    Get property by subdomain
    ---
    tags:
      - Properties
    summary: Get property data by subdomain
    description: Get property data by subdomain (public endpoint for login page)
    parameters:
      - in: query
        name: subdomain
        type: string
        description: Property subdomain
    responses:
      200:
        description: Property retrieved successfully
        schema:
          type: object
          properties:
            property:
              type: object
      404:
        description: Property not found
      500:
        description: Server error
    """
    try:
        # Get subdomain from request headers or query params
        subdomain = request.headers.get('X-Subdomain') or request.args.get('subdomain')
        
        if not subdomain:
            # Try to extract from hostname
            hostname = request.headers.get('Host', '')
            if hostname:
                parts = hostname.split('.')
                if parts:
                    subdomain = parts[0].replace('-', '_').lower()
        
        if not subdomain or subdomain.lower() == 'localhost':
            return jsonify({'error': 'Subdomain not provided'}), 400
            
        if subdomain.lower() == 'admin':
            return jsonify({'id': -1, 'name': 'PMS', 'portal_subdomain': 'admin', 'display_settings': {}}), 200
        
        # Normalize subdomain (remove numeric suffixes like -11)
        import re
        normalized_subdomain = re.sub(r'-\d+$', '', subdomain).replace('-', '_').replace(' ', '_').lower()
        original_subdomain = subdomain.lower()
        
        # Try to find property by matching subdomain pattern in name
        # First try exact or close match
        properties = Property.query.filter(
            db.or_(
                db.func.lower(db.func.replace(db.func.replace(Property.name, ' ', '_'), '-', '_')).like(f'%{normalized_subdomain}%'),
                Property.name.ilike(f'%{normalized_subdomain}%'),
                Property.name.ilike(f'%{original_subdomain}%')
            )
        ).all()
        
        if not properties:
            # If no matches, try to get all properties and let frontend handle it
            # Or return first property if only one exists (for development)
            all_properties = Property.query.limit(1).all()
            if all_properties:
                property_obj = all_properties[0]
            else:
                return jsonify({'error': 'Property not found'}), 404
        else:
            # If multiple matches, prefer exact match or first one
            property_obj = properties[0]
            if len(properties) > 1:
                # Try to find exact match
                for p in properties:
                    name_normalized = p.name.lower().replace(' ', '_').replace('-', '_')
                    if name_normalized == normalized_subdomain or name_normalized.startswith(normalized_subdomain):
                        property_obj = p
                        break
        
        # Get property data with display settings
        try:
            prop_dict = property_obj.to_dict()
        except Exception as dict_error:
            current_app.logger.error(f"Error converting property to dict: {str(dict_error)}", exc_info=True)
            # Return basic property info if to_dict fails
            prop_dict = {
                'id': property_obj.id,
                'name': getattr(property_obj, 'name', None) or getattr(property_obj, 'title', None),
                'address': getattr(property_obj, 'address', None),
                'city': getattr(property_obj, 'city', None),
                'portal_subdomain': getattr(property_obj, 'portal_subdomain', None),
                'portal_enabled': getattr(property_obj, 'portal_enabled', False)
            }
        
        # Load display settings
        import json
        display_settings = {}
        try:
            if hasattr(property_obj, 'display_settings') and property_obj.display_settings:
                if isinstance(property_obj.display_settings, str):
                    display_settings = json.loads(property_obj.display_settings)
                elif isinstance(property_obj.display_settings, dict):
                    display_settings = property_obj.display_settings
        except (json.JSONDecodeError, TypeError, AttributeError) as settings_error:
            current_app.logger.warning(f"Error parsing display_settings: {str(settings_error)}")
            display_settings = {}
        
        prop_dict['display_settings'] = display_settings
        
        return jsonify(prop_dict), 200
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        current_app.logger.error(f"Get property by subdomain error: {str(e)}\n{error_trace}", exc_info=True)
        return jsonify({'error': 'Failed to get property', 'details': str(e) if current_app.config.get('DEBUG', False) else None}), 500

@property_bp.route('/admin/all', methods=['GET'])
@jwt_required()
def get_all_properties_admin():
    """Get all properties for System Admin"""
    try:
        current_user_id = get_jwt_identity()
        if not is_super_admin(current_user_id):
            return jsonify({'error': 'Unauthorized. Super Admin access required.'}), 403
            
        properties = Property.query.order_by(Property.created_at.desc()).all()
        return jsonify([p.to_dict() for p in properties]), 200
    except Exception as e:
        current_app.logger.error(f"Get all properties admin error: {str(e)}")
        return jsonify({'error': 'Failed to get properties'}), 500
