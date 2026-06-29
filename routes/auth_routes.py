from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import create_access_token, create_refresh_token, jwt_required, get_jwt_identity, get_jwt
from werkzeug.security import check_password_hash
from datetime import datetime, timezone, timedelta
import random
import secrets
import re

# TOTP removed - using email-based 2FA like main domain

from app import db
from models.user import User, UserRole
from models.tenant import Tenant

def staff_belongs_to_property(user_id, property_id):
    """
    Check if a staff user belongs to a specific property.
    Returns True if staff has property_id matching the requested property.
    """
    try:
        from models.staff import Staff
        from sqlalchemy import text
        
        # Get staff profile and check if it belongs to this property
        staff = db.session.execute(text(
            "SELECT id, property_id FROM staff WHERE user_id = :user_id AND property_id = :property_id"
        ), {
            'user_id': user_id,
            'property_id': property_id
        }).first()
        
        if staff:
            current_app.logger.info(f"Staff {user_id} belongs to property {property_id}")
            return True
        
        # Debug: Check if staff exists for any property
        try:
            debug_staff = db.session.execute(text(
                "SELECT id, property_id FROM staff WHERE user_id = :user_id"
            ), {'user_id': user_id}).first()
            if debug_staff:
                current_app.logger.warning(f"Staff {user_id} exists but for property {debug_staff[1]}, not {property_id}")
            else:
                current_app.logger.warning(f"Staff {user_id} does not exist in staff table at all")
        except Exception as debug_error:
            current_app.logger.warning(f"Error checking staff debug info: {str(debug_error)}")
        
        return False
                
    except Exception as e:
        current_app.logger.error(f"Error checking staff property membership: {str(e)}", exc_info=True)
        return False

def tenant_belongs_to_property(user_id, property_id):
    """
    Check if a tenant user belongs to a specific property.
    Returns True if tenant has an active rental in a unit that belongs to the property.
    Uses new simplified structure: property_id directly in tenant_units table.
    """
    try:
        from models.tenant import Tenant
        from sqlalchemy import text
        from datetime import date
        
        # Get tenant profile - check if tenant exists for this property
        tenant = db.session.execute(text(
            "SELECT id FROM tenants WHERE user_id = :user_id AND property_id = :property_id"
        ), {
            'user_id': user_id,
            'property_id': property_id
        }).first()
        
        if not tenant:
            current_app.logger.warning(f"Tenant {user_id} not found in tenants table for property {property_id}")
            # Debug: Check if tenant exists for any property
            try:
                debug_tenant = db.session.execute(text(
                    "SELECT id, property_id FROM tenants WHERE user_id = :user_id"
                ), {'user_id': user_id}).first()
                if debug_tenant:
                    current_app.logger.info(f"Tenant {user_id} exists but for property {debug_tenant[1]}, not {property_id}")
                else:
                    current_app.logger.warning(f"Tenant {user_id} does not exist in tenants table at all")
            except Exception as debug_error:
                current_app.logger.warning(f"Error checking tenant debug info: {str(debug_error)}")
            return False
        
        tenant_id = tenant[0]
        
        # Check if tenant has active tenant_units record for this specific property
        # New structure: property_id is directly in tenant_units table
        # For short-term rentals: allow login if rental exists and hasn't ended yet
        # (move_in_date can be today or in the future, move_out_date must be today or in the future)
        try:
            active_tenant_unit = db.session.execute(text(
                """
                SELECT tu.id FROM tenant_units tu
                WHERE tu.tenant_id = :tenant_id 
                  AND tu.property_id = :property_id
                  AND tu.move_in_date IS NOT NULL 
                  AND tu.move_out_date IS NOT NULL 
                  AND tu.move_out_date >= CURDATE()
                LIMIT 1
                """
            ), {
                'tenant_id': tenant_id,
                'property_id': property_id
            }).first()
            
            if active_tenant_unit:
                current_app.logger.info(f"Tenant {user_id} has rental in property {property_id} (move_out_date >= today)")
                return True
            
            # Also verify the unit belongs to this property (double check for security)
            # This ensures data integrity even if property_id in tenant_units is wrong
            unit_check = db.session.execute(text(
                """
                SELECT tu.id FROM tenant_units tu
                INNER JOIN units u ON tu.unit_id = u.id
                WHERE tu.tenant_id = :tenant_id 
                  AND tu.property_id = :property_id
                  AND u.property_id = :property_id
                  AND tu.move_in_date IS NOT NULL 
                  AND tu.move_out_date IS NOT NULL 
                  AND tu.move_out_date >= CURDATE()
                LIMIT 1
                """
            ), {
                'tenant_id': tenant_id,
                'property_id': property_id
            }).first()
            
            if unit_check:
                current_app.logger.info(f"Tenant {user_id} verified for property {property_id} (with unit check)")
                return True
            
            # Debug: Check what tenant_units records exist for this tenant
            debug_units = db.session.execute(text(
                "SELECT id, property_id, unit_id, move_in_date, move_out_date FROM tenant_units WHERE tenant_id = :tenant_id"
            ), {'tenant_id': tenant_id}).all()
            if debug_units:
                current_app.logger.warning(f"Tenant {user_id} has {len(debug_units)} tenant_units records but none match property {property_id} with active dates")
                for tu in debug_units:
                    current_app.logger.info(f"  - tenant_units id={tu[0]}, property_id={tu[1]}, unit_id={tu[2]}, move_in={tu[3]}, move_out={tu[4]}")
            else:
                current_app.logger.warning(f"Tenant {user_id} (tenant_id={tenant_id}) has no tenant_units records at all")
            return False
            
        except Exception as query_error:
            current_app.logger.error(f"Error querying tenant_units: {str(query_error)}", exc_info=True)
            # Fallback: Try simpler query without date checks
            try:
                simple_check = db.session.execute(text(
                    """
                    SELECT tu.id FROM tenant_units tu
                    WHERE tu.tenant_id = :tenant_id 
                      AND tu.property_id = :property_id
                    LIMIT 1
                    """
                ), {
                    'tenant_id': tenant_id,
                    'property_id': property_id
                }).first()
                return simple_check is not None
            except Exception:
                return False
                
    except Exception as e:
        current_app.logger.error(f"Error checking tenant property membership: {str(e)}", exc_info=True)
        return False

def get_property_id_from_request(data=None):
    """
    Try to get property_id from request.
    Checks query parameter, header, subdomain, or Origin header.
    If data is provided (from already-parsed request body), use that instead of re-parsing.
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
        
        # Try to get from request body (for POST requests)
        # Use provided data if available, otherwise parse request
        request_data = data
        if request_data is None:
            # Check both is_json and Content-Type header
            content_type = request.headers.get('Content-Type', '')
            is_json_request = request.is_json or 'application/json' in content_type
            
            if is_json_request:
                try:
                    request_data = request.get_json(force=True)  # force=True to parse even if Content-Type is missing
                except Exception as parse_error:
                    current_app.logger.warning(f"Could not parse request body: {str(parse_error)}")
                    request_data = None
        
        # Ensure request_data is a dictionary, not a string
        if request_data and not isinstance(request_data, dict):
            current_app.logger.warning(f"request_data is not a dict, it's {type(request_data)}: {request_data}")
            # Try to parse if it's a string
            if isinstance(request_data, str):
                try:
                    import json
                    request_data = json.loads(request_data)
                except Exception:
                    request_data = None
            else:
                request_data = None
        
        if request_data and isinstance(request_data, dict):
            current_app.logger.info(f"Request body data keys: {list(request_data.keys())}")
            
            # Check for property_id (number)
            if 'property_id' in request_data:
                try:
                    property_id = int(request_data['property_id'])
                    current_app.logger.info(f"Found property_id={property_id} in request body")
                    return property_id
                except (ValueError, TypeError) as e:
                    current_app.logger.warning(f"Error parsing property_id: {str(e)}")
            
            # Check for property_subdomain (string) - frontend extracted from URL
            if 'property_subdomain' in request_data:
                subdomain = str(request_data['property_subdomain']).lower().strip()
                current_app.logger.info(f"Found property_subdomain='{subdomain}' in request body, attempting to match")
                # Try to find property by subdomain
                from sqlalchemy import text
                
                # CRITICAL: Use portal_subdomain column first (it's designed for this exact purpose!)
                # The properties table has portal_subdomain, title, and building_name columns
                try:
                    # First, try portal_subdomain (exact match - this is the correct column!)
                    property_obj = db.session.execute(text(
                        """
                        SELECT id FROM properties 
                        WHERE LOWER(TRIM(COALESCE(portal_subdomain, ''))) = :subdomain
                        LIMIT 1
                        """
                    ), {'subdomain': subdomain}).first()
                    
                    if property_obj:
                        current_app.logger.info(f"Matched subdomain '{subdomain}' to property_id={property_obj[0]} (exact match on portal_subdomain)")
                        return property_obj[0]
                    
                    # Fallback: Try title column (from main-domain properties table structure)
                    property_obj = db.session.execute(text(
                        """
                        SELECT id FROM properties 
                        WHERE LOWER(TRIM(COALESCE(title, ''))) = :subdomain
                        LIMIT 1
                        """
                    ), {'subdomain': subdomain}).first()
                    
                    if property_obj:
                        current_app.logger.info(f"Matched subdomain '{subdomain}' to property_id={property_obj[0]} (exact match on title)")
                        return property_obj[0]
                    
                    # Fallback: Try building_name column
                    property_obj = db.session.execute(text(
                        """
                        SELECT id FROM properties 
                        WHERE LOWER(TRIM(COALESCE(building_name, ''))) = :subdomain
                        LIMIT 1
                        """
                    ), {'subdomain': subdomain}).first()
                    
                    if property_obj:
                        current_app.logger.info(f"Matched subdomain '{subdomain}' to property_id={property_obj[0]} (exact match on building_name)")
                        return property_obj[0]
                    
                    # Fallback: Try name column (if it exists in sub-domain model)
                    try:
                        property_obj = db.session.execute(text(
                            """
                            SELECT id FROM properties 
                            WHERE LOWER(TRIM(COALESCE(name, ''))) = :subdomain
                            LIMIT 1
                            """
                        ), {'subdomain': subdomain}).first()
                        
                        if property_obj:
                            current_app.logger.info(f"Matched subdomain '{subdomain}' to property_id={property_obj[0]} (exact match on name)")
                            return property_obj[0]
                    except Exception:
                        # name column doesn't exist, skip
                        pass
                    
                except Exception as exact_match_error:
                    current_app.logger.warning(f"Error in exact match query: {str(exact_match_error)}")
                
                # Try partial match if exact match fails
                try:
                    # Try portal_subdomain partial match
                    property_obj = db.session.execute(text(
                        """
                        SELECT id FROM properties 
                        WHERE LOWER(TRIM(COALESCE(portal_subdomain, ''))) LIKE :pattern
                        LIMIT 1
                        """
                    ), {'pattern': f'%{subdomain}%'}).first()
                    
                    if property_obj:
                        current_app.logger.info(f"Matched subdomain '{subdomain}' to property_id={property_obj[0]} (partial match on portal_subdomain)")
                        return property_obj[0]
                    
                    # Try title partial match
                    property_obj = db.session.execute(text(
                        """
                        SELECT id FROM properties 
                        WHERE LOWER(TRIM(COALESCE(title, ''))) LIKE :pattern
                        LIMIT 1
                        """
                    ), {'pattern': f'%{subdomain}%'}).first()
                    
                    if property_obj:
                        current_app.logger.info(f"Matched subdomain '{subdomain}' to property_id={property_obj[0]} (partial match on title)")
                        return property_obj[0]
                except Exception as partial_match_error:
                    current_app.logger.warning(f"Error in partial match query: {str(partial_match_error)}")
                
                # Log available properties for debugging
                try:
                    all_props = db.session.execute(text(
                        "SELECT id, portal_subdomain, title, building_name FROM properties LIMIT 10"
                    )).all()
                    current_app.logger.warning(f"Could not match subdomain '{subdomain}'. Available properties: {all_props}")
                except Exception:
                    current_app.logger.warning(f"Could not match subdomain '{subdomain}' and could not list properties")
        
        # Try to extract from subdomain in Origin or Host header
        origin = request.headers.get('Origin', '')
        host = request.headers.get('Host', '')
        
        # Log headers for debugging
        current_app.logger.info(f"Extracting property_id from headers - Origin: '{origin}', Host: '{host}'")
        
        # Check if subdomain contains property identifier
        # Example: pat.localhost:8080 -> try to find property with subdomain "pat"
        if origin or host:
            import re
            # Extract subdomain (e.g., "pat" from "pat.localhost:8080" or "pat.localhost")
            subdomain_match = re.search(r'([a-zA-Z0-9-]+)\.localhost', origin or host)
            if subdomain_match:
                subdomain = subdomain_match.group(1).lower()
                current_app.logger.info(f"Extracted subdomain '{subdomain}' from headers")
                # Try to find property by matching subdomain with property name (case-insensitive)
                # Match by exact name first, then partial match
                from sqlalchemy import text
                
                # Try portal_subdomain first (the correct column for subdomain matching)
                property_obj = db.session.execute(text(
                    "SELECT id FROM properties WHERE LOWER(TRIM(COALESCE(portal_subdomain, ''))) = :subdomain LIMIT 1"
                ), {'subdomain': subdomain}).first()
                
                if property_obj:
                    current_app.logger.info(f"Found property {property_obj[0]} for subdomain '{subdomain}' (exact match on portal_subdomain from headers)")
                    return property_obj[0]
                
                # Fallback: Try title column
                property_obj = db.session.execute(text(
                    "SELECT id FROM properties WHERE LOWER(TRIM(COALESCE(title, ''))) = :subdomain LIMIT 1"
                ), {'subdomain': subdomain}).first()
                
                if property_obj:
                    current_app.logger.info(f"Found property {property_obj[0]} for subdomain '{subdomain}' (exact match on title from headers)")
                    return property_obj[0]
                
                # Fallback: Try building_name column
                property_obj = db.session.execute(text(
                    "SELECT id FROM properties WHERE LOWER(TRIM(COALESCE(building_name, ''))) = :subdomain LIMIT 1"
                ), {'subdomain': subdomain}).first()
                
                if property_obj:
                    current_app.logger.info(f"Found property {property_obj[0]} for subdomain '{subdomain}' (exact match on building_name from headers)")
                    return property_obj[0]
                
                # Try partial match on portal_subdomain
                property_obj = db.session.execute(text(
                    "SELECT id FROM properties WHERE LOWER(TRIM(COALESCE(portal_subdomain, ''))) LIKE :pattern LIMIT 1"
                ), {'pattern': f'%{subdomain}%'}).first()
                
                if property_obj:
                    current_app.logger.info(f"Found property {property_obj[0]} for subdomain '{subdomain}' (partial match on portal_subdomain from headers)")
                    return property_obj[0]
                
                # Log for debugging - list all properties to help troubleshoot
                try:
                    all_props = db.session.execute(text(
                        "SELECT id, name, title, building_name FROM properties LIMIT 10"
                    )).all()
                    current_app.logger.warning(f"Could not find property for subdomain '{subdomain}'. Available properties: {all_props}")
                except Exception as list_error:
                    current_app.logger.warning(f"Could not find property for subdomain '{subdomain}'. Error listing properties: {str(list_error)}")
            else:
                current_app.logger.warning(f"No subdomain pattern found in Origin '{origin}' or Host '{host}'")
        else:
            current_app.logger.warning("No Origin or Host header found in request")
        
        return None
    except Exception as e:
        current_app.logger.warning(f"Error getting property_id from request: {str(e)}")
        return None

def get_role_value(role):
    """Safely get role value as lowercase string, handling both enum and string."""
    if not role:
        return 'tenant'
    
    # If it's an enum, get its value
    if hasattr(role, 'value'):
        role_str = role.value.upper()
    else:
        # It's already a string
        role_str = str(role).upper()
    
    # Map database values to lowercase for consistency
    # ADMIN is not supported in subdomain - map to property_manager for backward compatibility
    role_map = {
        'ADMIN': 'property_manager',  # Map ADMIN to property_manager in subdomain
        'MANAGER': 'property_manager',
        'STAFF': 'staff',
        'TENANT': 'tenant'
    }
    return role_map.get(role_str, 'tenant')

def is_staff_management_enabled(property_id):
    """Check if staff management is enabled for a property."""
    try:
        from models.property import Property
        import json
        
        property_obj = Property.query.get(property_id)
        if not property_obj:
            return False
        
        display_settings = {}
        if property_obj.display_settings:
            try:
                if isinstance(property_obj.display_settings, str):
                    display_settings = json.loads(property_obj.display_settings)
                elif isinstance(property_obj.display_settings, dict):
                    display_settings = property_obj.display_settings
            except (json.JSONDecodeError, TypeError):
                display_settings = {}
        
        # Default to True if not set (backward compatibility)
        return display_settings.get('staffManagementEnabled', True)
    except Exception as e:
        current_app.logger.error(f"Error checking staff management status: {str(e)}")
        return True  # Default to enabled on error to avoid blocking
from models.staff import Staff
from services.email_service import send_password_reset_email

auth_bp = Blueprint('auth', __name__)

def validate_email(email):
    """Validate email format."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_password(password):
    """Validate password strength."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r'\d', password):
        return False, "Password must contain at least one number"
    return True, "Password is valid"

@auth_bp.route('/login', methods=['POST'])
def login():
    """
    User login
    ---
    tags:
      - Authentication
    summary: Authenticate user and get access token
    description: Login with email/username and password to receive JWT tokens
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
            - password
          properties:
            email:
              type: string
              description: Email or username
            password:
              type: string
              format: password
    responses:
      200:
        description: Login successful
        schema:
          type: object
          properties:
            access_token:
              type: string
            refresh_token:
              type: string
            user:
              type: object
      401:
        description: Invalid credentials
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        
        # Ensure data is a dictionary, not a string
        if data and not isinstance(data, dict):
            current_app.logger.warning(f"Login request data is not a dict, it's {type(data)}: {data}")
            # Try to parse if it's a string
            if isinstance(data, str):
                try:
                    import json
                    data = json.loads(data)
                except Exception as parse_error:
                    current_app.logger.error(f"Could not parse login data as JSON: {str(parse_error)}")
                    return jsonify({'error': 'Invalid request format'}), 400
            else:
                return jsonify({'error': 'Invalid request format'}), 400
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Extract credentials
        email_or_username = data.get('email', '').strip()
        password = data.get('password', '')
        
        if not email_or_username or not password:
            return jsonify({'error': 'Email/username and password are required'}), 400
        
        # Find user by email or username
        try:
            user = User.query.filter(
                (User.email == email_or_username.lower()) | 
                (User.username == email_or_username.lower())
            ).first()
        except Exception as query_error:
            current_app.logger.error(f"Database query error: {str(query_error)}", exc_info=True)
            if current_app.config.get('DEBUG', False):
                return jsonify({'error': 'Database query failed', 'details': str(query_error)}), 500
            return jsonify({'error': 'Login failed'}), 500
        
        if not user:
            current_app.logger.warning(f"Login attempt failed - user not found: {email_or_username}")
            return jsonify({'error': 'Invalid credentials'}), 401
        
        # Log user found for debugging
        current_app.logger.info(f"User found: id={user.id}, email={user.email}, username={getattr(user, 'username', None)}, role={user.role}")
        
        # Check if user is active using status enum
        try:
            # Staff accounts don't require email verification - they're verified by property managers
            # Skip email verification check for staff
            if not user.is_staff() and not user.is_active_user():
                current_app.logger.warning(f"Login attempt failed - account not active: user_id={user.id}, status={getattr(user.status, 'value', user.status)}")
                status_value = getattr(user.status, 'value', str(user.status)) if hasattr(user, 'status') and user.status else 'unknown'
                if status_value == 'suspended':
                    return jsonify({'error': 'Account is suspended'}), 401
                elif status_value == 'inactive':
                    return jsonify({'error': 'Account is deactivated'}), 401
                elif status_value == 'pending_verification':
                    return jsonify({'error': 'Email verification required'}), 401
                else:
                    return jsonify({'error': 'Account is not active'}), 401
            # For staff with pending_verification status, auto-verify and activate them
            elif user.is_staff() and hasattr(user, 'status') and getattr(user.status, 'value', str(user.status)) == 'pending_verification':
                current_app.logger.info(f"Auto-verifying staff account: user_id={user.id}")
                user.email_verified = True
                from models.user import UserStatus
                user.status = UserStatus.ACTIVE
                db.session.commit()
        except Exception as attr_error:
            current_app.logger.error(f"Error checking user status: {str(attr_error)}", exc_info=True)
            if current_app.config.get('DEBUG', False):
                return jsonify({'error': 'Error checking user status', 'details': str(attr_error)}), 500
            return jsonify({'error': 'Login failed'}), 500
        
        # Verify password
        try:
            # Check if password_hash exists
            password_hash = getattr(user, 'password_hash', None)
            if not password_hash:
                current_app.logger.error(f"Login attempt failed - no password hash: user_id={user.id}, email={user.email}")
                if current_app.config.get('DEBUG', False):
                    return jsonify({
                        'error': 'Account password not set. Please contact administrator.',
                        'debug': {
                            'user_id': user.id,
                            'email': user.email,
                            'username': getattr(user, 'username', None),
                            'role': str(user.role)
                        }
                    }), 401
                return jsonify({'error': 'Invalid credentials'}), 401
            
            # Log password hash info (first 20 chars only for security)
            password_hash_preview = password_hash[:20] + '...' if password_hash and len(password_hash) > 20 else password_hash
            current_app.logger.debug(f"Checking password for user_id={user.id}, password_hash_preview={password_hash_preview}, password_length={len(password) if password else 0}")
            
            # Try password check
            password_valid = user.check_password(password)
            
            if not password_valid:
                current_app.logger.warning(f"Login attempt failed - invalid password: user_id={user.id}, email={user.email}, role={user.role}")
                
                # Additional debugging: Check if password_hash looks like a bcrypt hash
                is_bcrypt_format = password_hash.startswith('$2b$') or password_hash.startswith('$2a$') or password_hash.startswith('$2y$')
                
                if current_app.config.get('DEBUG', False):
                    # In DEBUG mode, provide more info about password check
                    return jsonify({
                        'error': 'Invalid credentials',
                        'debug': {
                            'user_id': user.id,
                            'email': user.email,
                            'username': getattr(user, 'username', None),
                            'role': str(user.role),
                            'has_password_hash': bool(password_hash),
                            'password_hash_length': len(password_hash) if password_hash else 0,
                            'password_hash_format_valid': is_bcrypt_format,
                            'password_provided': bool(password),
                            'password_length': len(password) if password else 0
                        }
                    }), 401
                return jsonify({'error': 'Invalid credentials'}), 401
        except Exception as pwd_error:
            current_app.logger.error(f"Password check error: {str(pwd_error)}", exc_info=True)
            if current_app.config.get('DEBUG', False):
                return jsonify({
                    'error': 'Password verification failed', 
                    'details': str(pwd_error),
                    'debug': {
                        'user_id': user.id,
                        'email': user.email,
                        'exception_type': type(pwd_error).__name__
                    }
                }), 500
            return jsonify({'error': 'Login failed'}), 500
        
        # Log successful password verification
        current_app.logger.info(f"Password verified successfully for user_id={user.id}, role={user.role}")
        
        # Block ADMIN users from logging into subdomain
        user_role_str = str(user.role).upper() if user.role else ''
        if user_role_str == 'ADMIN':
            current_app.logger.warning(f"Admin login attempt blocked - admin {user.id} tried to login to subdomain")
            return jsonify({
                'error': 'Admin accounts cannot access property subdomains. Please use the main domain portal.',
                'code': 'ADMIN_SUBDOMAIN_BLOCKED'
            }), 403
        
        # Check if 2FA is enabled - send email code if enabled (like main domain)
        if getattr(user, 'two_factor_enabled', False):
            from flask_mail import Message
            from app import mail
            
            code = str(random.randint(100000, 999999))
            user.two_factor_email_code = code
            user.two_factor_email_expires = datetime.now(timezone.utc) + timedelta(minutes=10)
            db.session.commit()
            
            try:
                msg = Message(
                    subject="Your verification code",
                    recipients=[user.email],
                    body=f"Your verification code is {code}. It expires in 10 minutes."
                )
                mail.send(msg)
            except Exception as email_error:
                current_app.logger.error(f"2FA email send error: {email_error}")
                db.session.rollback()
                return jsonify({'error': 'Failed to send verification code'}), 500
            
            return jsonify({
                'status': 'pending_2fa',
                'message': 'Verification code sent to your email.'
            }), 200
        
        # Log user role for debugging
        user_role_str = str(user.role).upper() if user.role else 'UNKNOWN'
        current_app.logger.info(f"Login attempt - user_id={user.id}, role={user_role_str}, is_tenant={user.is_tenant()}, is_staff={user.is_staff()}, is_property_manager={user.is_property_manager()}")
        
        # Check if this is a main-domain login attempt (staff cannot login to main-domain)
        # Main-domain typically runs on port 5000, sub-domain on port 5001
        # Also check Origin/Host headers to detect main-domain
        origin = request.headers.get('Origin', '')
        host = request.headers.get('Host', '')
        is_main_domain = False
        
        # Check if request is from main-domain (port 5000 or no subdomain pattern)
        if ':5000' in origin or ':5000' in host:
            is_main_domain = True
        elif origin and 'localhost' in origin and '.' not in origin.split('//')[1].split(':')[0]:
            # No subdomain in origin (e.g., localhost:3000 without subdomain)
            is_main_domain = True
        
        # Block staff from logging into main-domain
        if user.is_staff() and is_main_domain:
            current_app.logger.warning(f"Staff login attempt blocked - staff {user.id} tried to login to main-domain")
            return jsonify({
                'error': 'Staff accounts can only login to property subdomains, not the main domain.',
                'code': 'STAFF_MAIN_DOMAIN_BLOCKED'
            }), 403
        
        # For tenants: STRICTLY check if they belong to this property subdomain
        # Tenants can ONLY login to the property subdomain where they have an active rental
        if user.is_tenant():
            try:
                # CRITICAL: Get property_id from request (subdomain, header, query param, or body)
                # Pass the already-parsed data to avoid re-parsing the request body
                # This MUST be the property they're trying to login to, not auto-detected from their lease
                property_id = get_property_id_from_request(data=data)
                
                # Log for debugging
                current_app.logger.info(f"Tenant login attempt - user_id={user.id}, email={user.email}, extracted property_id={property_id}")
                
                # Also log request body if available
                try:
                    if request.is_json or 'application/json' in request.headers.get('Content-Type', ''):
                        body_data = request.get_json(force=True)
                        current_app.logger.info(f"Request body contains: {list(body_data.keys()) if body_data else 'empty'}")
                        if body_data and 'property_subdomain' in body_data:
                            current_app.logger.info(f"property_subdomain value: '{body_data.get('property_subdomain')}'")
                except Exception as body_log_error:
                    current_app.logger.warning(f"Could not log request body: {str(body_log_error)}")
                
                if not property_id:
                    # Log request details for debugging
                    origin = request.headers.get('Origin', '')
                    host = request.headers.get('Host', '')
                    content_type = request.headers.get('Content-Type', '')
                    current_app.logger.warning(f"Property ID not found in request. Origin: '{origin}', Host: '{host}', Content-Type: '{content_type}'")
                    
                    # List all properties to help debug
                    try:
                        from sqlalchemy import text
                        all_props = db.session.execute(text(
                            "SELECT id, name, title, building_name FROM properties LIMIT 10"
                        )).all()
                        current_app.logger.warning(f"Available properties in database: {all_props}")
                    except Exception as list_error:
                        current_app.logger.warning(f"Could not list properties: {str(list_error)}")
                
                # STRICT VALIDATION: For tenants, property_id MUST be provided in the request
                # We do NOT auto-detect from tenant's lease because that would allow them to login
                # to any property subdomain (it would always find their property and allow access)
                if not property_id:
                    return jsonify({
                        'error': 'Property context is required. Please login through your property portal.',
                        'code': 'PROPERTY_CONTEXT_REQUIRED',
                        'message': 'You must login through the specific property subdomain where you have an active rental.',
                        'debug': {
                            'origin': request.headers.get('Origin', ''),
                            'host': request.headers.get('Host', ''),
                            'content_type': request.headers.get('Content-Type', ''),
                            'request_method': request.method,
                            'has_json': request.is_json
                        } if current_app.config.get('DEBUG', False) else None
                    }), 403
                
                # Verify tenant belongs to THIS SPECIFIC property from the request
                # This prevents tenants from logging into other property subdomains
                if not tenant_belongs_to_property(user.id, property_id):
                    # Get property name for better error message
                    from models.property import Property
                    property_obj = Property.query.get(property_id)
                    property_name = property_obj.name if property_obj else f"Property {property_id}"
                    
                    # Also get tenant's actual property for helpful error message
                    from sqlalchemy import text
                    from datetime import date
                    
                    tenant_property_name = None
                    try:
                        # Get tenant's active property using new structure
                        tenant_property = db.session.execute(text(
                            """
                            SELECT p.name FROM tenant_units tu
                            INNER JOIN properties p ON tu.property_id = p.id
                            INNER JOIN tenants t ON tu.tenant_id = t.id
                            WHERE t.user_id = :user_id
                              AND tu.move_in_date IS NOT NULL 
                              AND tu.move_out_date IS NOT NULL 
                              AND tu.move_in_date <= CURDATE() 
                              AND tu.move_out_date >= CURDATE()
                            LIMIT 1
                            """
                        ), {
                            'user_id': user.id
                        }).first()
                        
                        if tenant_property:
                            tenant_property_name = tenant_property[0]
                    except Exception as prop_error:
                        current_app.logger.warning(f"Error getting tenant's property name: {str(prop_error)}")
                    
                    error_msg = f'You do not have access to {property_name}.'
                    if tenant_property_name:
                        error_msg += f' You can only access {tenant_property_name} where you have an active rental.'
                    else:
                        error_msg += ' You can only access the property where you have an active rental.'
                    
                    return jsonify({
                        'error': error_msg,
                        'code': 'PROPERTY_ACCESS_DENIED',
                        'property_id': property_id,
                        'attempted_property': property_name,
                        'your_property': tenant_property_name
                    }), 403
                
                # Log successful property validation
                current_app.logger.info(f"Tenant {user.id} validated for property {property_id}")
                
            except Exception as tenant_check_error:
                current_app.logger.error(f"Error checking tenant property access: {str(tenant_check_error)}", exc_info=True)
                # For security, deny login if we can't verify property access
                return jsonify({
                    'error': 'Unable to verify property access. Please contact support.',
                    'code': 'PROPERTY_VERIFICATION_FAILED'
                }), 403
        
        # For staff: STRICTLY check if they belong to this property subdomain
        # Staff can ONLY login to the property subdomain where they are assigned
        elif user.is_staff():
            try:
                # Get property_id from request (subdomain, header, query param, or body)
                property_id = get_property_id_from_request(data=data)
                
                # Log for debugging
                current_app.logger.info(f"Staff login attempt - user_id={user.id}, email={user.email}, extracted property_id={property_id}")
                
                if not property_id:
                    return jsonify({
                        'error': 'Property context is required. Please login through your property portal.',
                        'code': 'PROPERTY_CONTEXT_REQUIRED',
                        'message': 'You must login through the specific property subdomain where you are assigned.'
                    }), 403
                
                # Verify staff belongs to THIS SPECIFIC property from the request
                # This prevents staff from logging into other property subdomains
                if not staff_belongs_to_property(user.id, property_id):
                    # Get property name for better error message
                    from models.property import Property
                    property_obj = Property.query.get(property_id)
                    property_name = property_obj.name if property_obj else f"Property {property_id}"
                    
                    # Also get staff's actual property for helpful error message
                    from sqlalchemy import text
                    staff_property_name = None
                    try:
                        staff_property = db.session.execute(text(
                            """
                            SELECT p.name FROM staff s
                            INNER JOIN properties p ON s.property_id = p.id
                            WHERE s.user_id = :user_id
                            LIMIT 1
                            """
                        ), {
                            'user_id': user.id
                        }).first()
                        
                        if staff_property:
                            staff_property_name = staff_property[0]
                    except Exception as prop_error:
                        current_app.logger.warning(f"Error getting staff's property name: {str(prop_error)}")
                    
                    error_msg = f'You do not have access to {property_name}.'
                    if staff_property_name:
                        error_msg += f' You can only access {staff_property_name} where you are assigned.'
                    else:
                        error_msg += ' You can only access the property where you are assigned.'
                    
                    return jsonify({
                        'error': error_msg,
                        'code': 'PROPERTY_ACCESS_DENIED',
                        'property_id': property_id,
                        'attempted_property': property_name,
                        'your_property': staff_property_name
                    }), 403
                
                # Check if staff management is enabled for this property
                if not is_staff_management_enabled(property_id):
                    current_app.logger.warning(f"Staff login blocked - staff management disabled for property {property_id}")
                    return jsonify({
                        'error': 'Staff management is currently disabled for this property.',
                        'code': 'STAFF_MANAGEMENT_DISABLED',
                        'message': 'Staff accounts cannot login when staff management is disabled. Please contact the property manager.'
                    }), 403
                
                # Log successful property validation
                current_app.logger.info(f"Staff {user.id} validated for property {property_id}")
                
            except Exception as staff_check_error:
                current_app.logger.error(f"Error checking staff property access: {str(staff_check_error)}", exc_info=True)
                # For security, deny login if we can't verify property access
                return jsonify({
                    'error': 'Unable to verify property access. Please contact support.',
                    'code': 'PROPERTY_VERIFICATION_FAILED'
                }), 403
        
        # For property managers: STRICTLY check if they own the property from subdomain
        # Property managers can ONLY login to property subdomains they own
        elif user.is_property_manager():
            try:
                # Get property_id from request (subdomain, header, query param, or body)
                property_id = get_property_id_from_request(data=data)
                
                # Log for debugging
                current_app.logger.info(f"Property manager login attempt - user_id={user.id}, email={user.email}, extracted property_id={property_id}")
                
                if not property_id:
                    return jsonify({
                        'error': 'Property context is required. Please login through your property portal.',
                        'code': 'PROPERTY_CONTEXT_REQUIRED',
                        'message': 'You must login through the specific property subdomain you own.'
                    }), 403
                
                # Verify property exists and user owns THIS SPECIFIC property
                from models.property import Property
                property_obj = Property.query.get(property_id)
                if not property_obj:
                    return jsonify({
                        'error': 'Property not found',
                        'code': 'PROPERTY_NOT_FOUND'
                    }), 404
                
                # CRITICAL: Verify the property manager owns this property
                if property_obj.owner_id != user.id:
                    property_name = property_obj.name if property_obj else f"Property {property_id}"
                    
                    # Get property manager's owned properties for helpful error message
                    owned_properties = Property.query.filter_by(owner_id=user.id).all()
                    owned_property_names = [p.name for p in owned_properties] if owned_properties else []
                    
                    error_msg = f'You do not own {property_name}.'
                    if owned_property_names:
                        error_msg += f' You can only access: {", ".join(owned_property_names)}'
                    else:
                        error_msg += ' You do not own any properties.'
                    
                    return jsonify({
                        'error': error_msg,
                        'code': 'PROPERTY_ACCESS_DENIED',
                        'property_id': property_id,
                        'attempted_property': property_name,
                        'owned_properties': owned_property_names
                    }), 403
                
                # Log successful property validation
                current_app.logger.info(f"Property manager {user.id} validated for property {property_id}")
                
            except Exception as pm_check_error:
                current_app.logger.error(f"Error checking property manager property access: {str(pm_check_error)}", exc_info=True)
                # For security, deny login if we can't verify property access
                return jsonify({
                    'error': 'Unable to verify property access. Please contact support.',
                    'code': 'PROPERTY_VERIFICATION_FAILED'
                }), 403
        
        # Update last login (don't commit here, commit at the end)
        user.last_login = datetime.now(timezone.utc)
        
        # Get property_id for JWT token (if available from validation above)
        property_id_for_token = None
        if user.is_tenant() or user.is_staff() or user.is_property_manager():
            try:
                property_id_for_token = get_property_id_from_request(data=data)
            except Exception:
                pass
        
        # Create tokens
        try:
            # Safely get role value using helper function
            role_value = get_role_value(user.role)
            username_value = user.username if user.username else user.email
            
            # Build JWT claims - include property_id if available
            jwt_claims = {
                'role': role_value,
                'email': user.email,
                'username': username_value
            }
            
            # Add property_id to token for property managers, staff, and tenants
            # This ensures subsequent requests know which property context they're in
            if property_id_for_token:
                jwt_claims['property_id'] = property_id_for_token
                current_app.logger.info(f"Adding property_id {property_id_for_token} to JWT token for user {user.id}")
            
            access_token = create_access_token(
                identity=str(user.id),
                additional_claims=jwt_claims
            )
            refresh_token = create_refresh_token(identity=str(user.id))
        except Exception as token_error:
            current_app.logger.error(f"Token creation error: {str(token_error)}", exc_info=True)
            db.session.rollback()
            if current_app.config.get('DEBUG', False):
                return jsonify({'error': 'Failed to create authentication tokens', 'details': str(token_error)}), 500
            return jsonify({'error': 'Failed to create authentication tokens'}), 500
        
        # Get user profile data based on role
        profile_data = {}
        try:
            if user.is_tenant():
                # Try to get tenant profile, handle case where relationship might not work
                from models.tenant import Tenant
                tenant_profile = Tenant.query.filter_by(user_id=user.id).first()
                if tenant_profile:
                    profile_data = tenant_profile.to_dict(include_rent=True)
                else:
                    profile_data = {'role': 'tenant', 'user_id': user.id}
            elif user.is_staff():
                # Try to get staff profile
                from models.staff import Staff
                staff_profile = Staff.query.filter_by(user_id=user.id).first()
                if staff_profile:
                    profile_data = staff_profile.to_dict()
                else:
                    profile_data = {'role': 'staff', 'user_id': user.id}
            elif user.is_property_manager():
                # Property managers may not have a separate profile table
                # Use basic user information as profile
                profile_data = {
                    'role': 'property_manager',
                    'permissions': ['manage_properties', 'manage_tenants', 'manage_staff', 'view_reports']
                }
        except Exception as profile_error:
            current_app.logger.error(f"Profile loading error: {str(profile_error)}")
            profile_data = {'role': get_role_value(user.role), 'user_id': user.id}
        
        # Commit the last_login update
        try:
            db.session.commit()
        except Exception as commit_error:
            current_app.logger.error(f"Database commit error: {str(commit_error)}")
            db.session.rollback()
            # Still return success since login worked, just last_login update failed
            current_app.logger.warning("Login successful but failed to update last_login timestamp")
        
        # Safely convert user to dict
        try:
            user_dict = user.to_dict()
        except Exception as dict_error:
            current_app.logger.error(f"Error converting user to dict: {str(dict_error)}", exc_info=True)
            # Return minimal user info if to_dict fails
            user_dict = {
                'id': user.id,
                'email': getattr(user, 'email', ''),
                'username': getattr(user, 'username', ''),
                'first_name': getattr(user, 'first_name', ''),
                'last_name': getattr(user, 'last_name', ''),
                'role': user.role.value if user.role else 'tenant'
            }
        
        return jsonify({
            'message': 'Login successful',
            'access_token': access_token,
            'refresh_token': refresh_token,
            'user': user_dict,
            'profile': profile_data
        }), 200
        
    except Exception as e:
        db.session.rollback()
        import traceback
        error_trace = traceback.format_exc()
        current_app.logger.error(f"Login error: {str(e)}\n{error_trace}")
        
        # In development, return more detailed error information
        if current_app.config.get('DEBUG', False):
            return jsonify({
                'error': 'Login failed',
                'details': str(e),
                'type': type(e).__name__
            }), 500
        else:
            # Don't expose internal error details to client in production
            return jsonify({'error': 'Login failed'}), 500

@auth_bp.route('/refresh', methods=['POST'])
@jwt_required(refresh=True)
def refresh():
    """
    Refresh access token
    ---
    tags:
      - Authentication
    summary: Get a new access token using refresh token
    description: Use a valid refresh token to obtain a new access token
    security:
      - Bearer: []
    responses:
      200:
        description: Token refreshed successfully
        schema:
          type: object
          properties:
            access_token:
              type: string
      401:
        description: Invalid or expired refresh token
      500:
        description: Server error
    """
    try:
        current_user_id = get_jwt_identity()
        # Convert string back to integer for database lookup
        user = User.query.get(int(current_user_id))
        
        if not user or not user.is_active_user():
            return jsonify({'error': 'User not found or inactive'}), 401
        
        # Create new access token
        access_token = create_access_token(
            identity=str(user.id),
            additional_claims={
                'role': user.role.value,
                'email': user.email,
                'username': user.username
            }
        )
        
        return jsonify({
            'access_token': access_token
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Token refresh error: {str(e)}")
        return jsonify({'error': 'Token refresh failed'}), 500

@auth_bp.route('/register', methods=['POST'])
def register():
    """
    User registration
    ---
    tags:
      - Authentication
    summary: Register a new user account
    description: Create a new user account with email, username, password, and user details
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
            - username
            - password
            - first_name
            - last_name
            - role
          properties:
            email:
              type: string
              format: email
            username:
              type: string
            password:
              type: string
              format: password
            first_name:
              type: string
            last_name:
              type: string
            role:
              type: string
              enum: [tenant, staff, property_manager]
    responses:
      201:
        description: User registered successfully
        schema:
          type: object
          properties:
            message:
              type: string
            user:
              type: object
      400:
        description: Validation error
      409:
        description: Email or username already exists
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Extract required fields
        email = data.get('email', '').strip().lower()
        username = data.get('username', '').strip().lower()
        password = data.get('password', '')
        first_name = data.get('first_name', '').strip()
        last_name = data.get('last_name', '').strip()
        role = data.get('role', 'tenant')
        
        # Validate required fields
        if not all([email, username, password, first_name, last_name]):
            return jsonify({'error': 'All fields are required'}), 400
        
        # Validate email format
        if not validate_email(email):
            return jsonify({'error': 'Invalid email format'}), 400
        
        # Validate password strength
        password_valid, password_message = validate_password(password)
        if not password_valid:
            return jsonify({'error': password_message}), 400
        
        # Check if user already exists
        existing_user = User.query.filter(
            (User.email == email) | (User.username == username)
        ).first()
        
        if existing_user:
            if existing_user.email == email:
                return jsonify({'error': 'Email already registered'}), 409
            else:
                return jsonify({'error': 'Username already taken'}), 409
        
        # Validate role
        try:
            user_role = UserRole(role)
        except ValueError:
            return jsonify({'error': 'Invalid role'}), 400
        
        # Create user
        user = User(
            email=email,
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role=user_role,
            phone_number=data.get('phone_number', '').strip(),
            address=data.get('address', '').strip()
        )
        
        db.session.add(user)
        db.session.flush()  # Get user ID
        
        # Create role-specific profile
        if user_role == UserRole.TENANT:
            tenant_profile = Tenant(user_id=user.id)
            db.session.add(tenant_profile)
        elif user_role == UserRole.STAFF:
            # For staff registration, additional fields are required
            employee_id = data.get('employee_id')
            job_title = data.get('job_title')
            if not employee_id or not job_title:
                return jsonify({'error': 'Employee ID and job title are required for staff registration'}), 400
            
            staff_profile = Staff(
                user_id=user.id,
                employee_id=employee_id,
                staff_role=data.get('staff_role', 'other'),
                job_title=job_title,
                hire_date=datetime.now().date()
            )
            db.session.add(staff_profile)
        
        db.session.commit()
        
        return jsonify({
            'message': 'Registration successful',
            'user': user.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Registration error: {str(e)}")
        return jsonify({'error': 'Registration failed'}), 500

@auth_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    """
    Request password reset
    ---
    tags:
      - Authentication
    summary: Request password reset email
    description: Send a password reset link to the user's email address
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
          properties:
            email:
              type: string
              format: email
    responses:
      200:
        description: Reset email sent (if email exists)
        schema:
          type: object
          properties:
            message:
              type: string
      400:
        description: Validation error
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        email = data.get('email', '').strip().lower()
        
        if not email:
            return jsonify({'error': 'Email is required'}), 400
        
        if not validate_email(email):
            return jsonify({'error': 'Invalid email format'}), 400
        
        user = User.query.filter_by(email=email).first()
        
        if not user:
            # Don't reveal if email exists or not for security
            return jsonify({'message': 'If the email exists, a reset link has been sent'}), 200
        
        # Generate reset token
        reset_token = secrets.token_urlsafe(32)
        user.reset_token = reset_token
        user.reset_token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        
        db.session.commit()
        
        # Send reset email
        try:
            send_password_reset_email(user.email, user.first_name, reset_token)
        except Exception as email_error:
            current_app.logger.error(f"Failed to send reset email: {str(email_error)}")
            # Don't fail the request if email sending fails
        
        return jsonify({'message': 'If the email exists, a reset link has been sent'}), 200
        
    except Exception as e:
        current_app.logger.error(f"Forgot password error: {str(e)}")
        return jsonify({'error': 'Password reset request failed'}), 500

@auth_bp.route('/reset-password', methods=['POST'])
def reset_password():
    """
    Reset password
    ---
    tags:
      - Authentication
    summary: Reset password using token
    description: Reset user password using a valid reset token from email
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - token
            - password
          properties:
            token:
              type: string
              description: Password reset token from email
            password:
              type: string
              format: password
    responses:
      200:
        description: Password reset successfully
        schema:
          type: object
          properties:
            message:
              type: string
      400:
        description: Invalid token, expired token, or validation error
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        token = data.get('token', '').strip()
        new_password = data.get('password', '')
        
        if not token or not new_password:
            return jsonify({'error': 'Token and new password are required'}), 400
        
        # Validate password strength
        password_valid, password_message = validate_password(new_password)
        if not password_valid:
            return jsonify({'error': password_message}), 400
        
        user = User.query.filter_by(reset_token=token).first()
        
        if not user:
            return jsonify({'error': 'Invalid or expired reset token'}), 400
        
        # Check if token is expired
        current_time = datetime.now(timezone.utc)
        token_expiry = user.reset_token_expiry
        
        # Handle timezone-naive datetime from database
        if token_expiry and not token_expiry.tzinfo:
            # Assume database time is UTC if no timezone info
            token_expiry = token_expiry.replace(tzinfo=timezone.utc)
        
        if token_expiry and token_expiry < current_time:
            return jsonify({'error': 'Reset token has expired'}), 400
        
        # Update password
        user.set_password(new_password)
        user.reset_token = None
        user.reset_token_expiry = None
        
        db.session.commit()
        
        return jsonify({'message': 'Password reset successful'}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Password reset error: {str(e)}")
        return jsonify({'error': 'Password reset failed'}), 500

@auth_bp.route('/change-password', methods=['POST'])
@jwt_required()
def change_password():
    """
    Change password
    ---
    tags:
      - Authentication
    summary: Change the authenticated user's password
    description: Update password for the currently authenticated user
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - current_password
            - new_password
          properties:
            current_password:
              type: string
              format: password
            new_password:
              type: string
              format: password
    responses:
      200:
        description: Password changed successfully
        schema:
          type: object
          properties:
            message:
              type: string
      400:
        description: Validation error or incorrect current password
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
        
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        current_password = data.get('current_password', '')
        new_password = data.get('new_password', '')
        
        if not current_password or not new_password:
            return jsonify({'error': 'Current password and new password are required'}), 400
        
        # Verify current password
        if not user.check_password(current_password):
            return jsonify({'error': 'Current password is incorrect'}), 400
        
        # Validate new password strength
        password_valid, password_message = validate_password(new_password)
        if not password_valid:
            return jsonify({'error': password_message}), 400
        
        # Update password
        user.set_password(new_password)
        db.session.commit()
        
        return jsonify({'message': 'Password changed successfully'}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Change password error: {str(e)}")
        return jsonify({'error': 'Password change failed'}), 500

@auth_bp.route('/verify-2fa', methods=['POST'])
def verify_two_factor():
    """
    Verify two-factor authentication
    ---
    tags:
      - Authentication
    summary: Verify 2FA code sent via email
    description: Verify the two-factor authentication code after initial login
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - email
            - code
          properties:
            email:
              type: string
              format: email
            code:
              type: string
              description: 2FA verification code from email
    responses:
      200:
        description: 2FA verified successfully
        schema:
          type: object
          properties:
            access_token:
              type: string
            refresh_token:
              type: string
            user:
              type: object
      400:
        description: Invalid code or validation error
      403:
        description: Access denied (admin/staff restrictions)
      404:
        description: User not found
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        email = data.get('email', '').strip().lower()
        code = data.get('code', '').strip()
        
        if not email or not code:
            return jsonify({'error': 'Email and code are required'}), 400
        
        user = User.query.filter_by(email=email).first()
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        if not getattr(user, 'two_factor_enabled', False) or not user.two_factor_email_code:
            return jsonify({'error': '2FA not pending'}), 400
        
        if not user.two_factor_email_expires or user.two_factor_email_expires < datetime.now(timezone.utc):
            return jsonify({'error': 'Code expired'}), 400
        
        if str(user.two_factor_email_code) != str(code):
            return jsonify({'error': 'Invalid code'}), 400
        
        # Block ADMIN users from logging into subdomain
        user_role_str = str(user.role).upper() if user.role else ''
        if user_role_str == 'ADMIN':
            current_app.logger.warning(f"Admin 2FA login attempt blocked - admin {user.id} tried to login to subdomain")
            return jsonify({
                'error': 'Admin accounts cannot access property subdomains. Please use the main domain portal.',
                'code': 'ADMIN_SUBDOMAIN_BLOCKED'
            }), 403
        
        # Check if this is a main-domain login attempt (staff cannot login to main-domain)
        origin = request.headers.get('Origin', '')
        host = request.headers.get('Host', '')
        is_main_domain = False
        
        if ':5000' in origin or ':5000' in host:
            is_main_domain = True
        elif origin and 'localhost' in origin and '.' not in origin.split('//')[1].split(':')[0]:
            is_main_domain = True
        
        # Block staff from logging into main-domain
        if user.is_staff() and is_main_domain:
            current_app.logger.warning(f"Staff 2FA login attempt blocked - staff {user.id} tried to login to main-domain")
            return jsonify({
                'error': 'Staff accounts can only login to property subdomains, not the main domain.',
                'code': 'STAFF_MAIN_DOMAIN_BLOCKED'
            }), 403
        
        # For staff: Check if they belong to this property subdomain
        if user.is_staff():
            try:
                request_data = request.get_json() or {}
                property_id = get_property_id_from_request(data=request_data)
                
                if not property_id:
                    return jsonify({
                        'error': 'Property context is required. Please login through your property portal.',
                        'code': 'PROPERTY_CONTEXT_REQUIRED'
                    }), 403
                
                if not staff_belongs_to_property(user.id, property_id):
                    from models.property import Property
                    property_obj = Property.query.get(property_id)
                    property_name = property_obj.name if property_obj else f"Property {property_id}"
                    
                    return jsonify({
                        'error': f'You do not have access to {property_name}. You can only access the property where you are assigned.',
                        'code': 'PROPERTY_ACCESS_DENIED'
                    }), 403
                
                # Check if staff management is enabled for this property
                if not is_staff_management_enabled(property_id):
                    current_app.logger.warning(f"Staff 2FA login blocked - staff management disabled for property {property_id}")
                    return jsonify({
                        'error': 'Staff management is currently disabled for this property.',
                        'code': 'STAFF_MANAGEMENT_DISABLED',
                        'message': 'Staff accounts cannot login when staff management is disabled. Please contact the property manager.'
                    }), 403
            except Exception as staff_check_error:
                current_app.logger.error(f"Error checking staff property access in 2FA: {str(staff_check_error)}", exc_info=True)
                return jsonify({
                    'error': 'Unable to verify property access. Please contact support.',
                    'code': 'PROPERTY_VERIFICATION_FAILED'
                }), 403
        
        # Clear 2FA code and create tokens
        user.two_factor_email_code = None
        user.two_factor_email_expires = None
        user.last_login = datetime.now(timezone.utc)
        db.session.commit()
        
        # Create tokens
        role_value = get_role_value(user.role)
        username_value = user.username if user.username else user.email
        
        access_token = create_access_token(
            identity=str(user.id),
            additional_claims={
                'role': role_value,
                'email': user.email,
                'username': username_value
            }
        )
        refresh_token = create_refresh_token(identity=str(user.id))
        
        # Get user profile data based on role
        profile_data = {}
        try:
            if user.is_tenant():
                from models.tenant import Tenant
                tenant_profile = Tenant.query.filter_by(user_id=user.id).first()
                if tenant_profile:
                    profile_data = tenant_profile.to_dict(include_rent=True)
                else:
                    profile_data = {'role': 'tenant', 'user_id': user.id}
            elif user.is_staff():
                from models.staff import Staff
                staff_profile = Staff.query.filter_by(user_id=user.id).first()
                if staff_profile:
                    profile_data = staff_profile.to_dict()
                else:
                    profile_data = {'role': 'staff', 'user_id': user.id}
            elif user.is_property_manager():
                profile_data = {
                    'role': 'property_manager',
                    'permissions': ['manage_properties', 'manage_tenants', 'manage_staff', 'view_reports']
                }
        except Exception as profile_error:
            current_app.logger.error(f"Profile loading error: {str(profile_error)}")
            profile_data = {'role': role_value, 'user_id': user.id}
        
        user_dict = user.to_dict()
        
        return jsonify({
            'message': 'Login successful',
            'access_token': access_token,
            'refresh_token': refresh_token,
            'user': user_dict,
            'profile': profile_data
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'2FA verify error: {e}')
        return jsonify({'error': 'Verification failed'}), 500


@auth_bp.route('/logout', methods=['POST'])
@jwt_required()
def logout():
    """Logout user (client-side token removal)."""
    # Note: With JWT, logout is primarily handled client-side by removing the token
    # This endpoint can be used for logging purposes or token blacklisting if implemented
    return jsonify({'message': 'Logout successful'}), 200

@auth_bp.route('/me', methods=['GET'])
@jwt_required()
def get_current_user():
    """
    Get current user
    ---
    tags:
      - Authentication
    summary: Get authenticated user information
    description: Returns the currently authenticated user's profile information based on their role
    security:
      - Bearer: []
    responses:
      200:
        description: User information retrieved successfully
        schema:
          type: object
          properties:
            id:
              type: integer
            email:
              type: string
            username:
              type: string
            role:
              type: string
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
        
        # Get user profile data based on role
        profile_data = {}
        try:
            if user.is_tenant():
                # Try to get tenant profile, handle case where relationship might not work
                from models.tenant import Tenant
                tenant_profile = Tenant.query.filter_by(user_id=user.id).first()
                if tenant_profile:
                    profile_data = tenant_profile.to_dict(include_rent=True)
                else:
                    profile_data = {'role': 'tenant', 'user_id': user.id}
            elif user.is_staff():
                # Try to get staff profile
                from models.staff import Staff
                staff_profile = Staff.query.filter_by(user_id=user.id).first()
                if staff_profile:
                    profile_data = staff_profile.to_dict()
                else:
                    profile_data = {'role': 'staff', 'user_id': user.id}
            elif user.is_property_manager():
                # Property managers may not have a separate profile table
                # Use basic user information as profile
                profile_data = {
                    'role': 'property_manager',
                    'permissions': ['manage_properties', 'manage_tenants', 'manage_staff', 'view_reports']
                }
        except Exception as profile_error:
            current_app.logger.error(f"Profile loading error: {str(profile_error)}")
            profile_data = {'role': user.role.value, 'user_id': user.id}
        
        return jsonify({
            'user': user.to_dict(),
            'profile': profile_data
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Get current user error: {str(e)}")
        return jsonify({'error': 'Failed to get user information'}), 500

@auth_bp.route('/2fa/status', methods=['GET'])
@jwt_required()
def get_two_factor_status():
    """
    Get 2FA status
    ---
    tags:
      - Authentication
    summary: Get 2FA status for the current user
    description: Check if two-factor authentication is enabled for the authenticated user
    security:
      - Bearer: []
    responses:
      200:
        description: 2FA status retrieved successfully
        schema:
          type: object
          properties:
            enabled:
              type: boolean
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
        
        return jsonify({
            'enabled': getattr(user, 'two_factor_enabled', False) or False
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"2FA status error: {str(e)}")
        return jsonify({'error': 'Failed to get 2FA status'}), 500

@auth_bp.route('/2fa/enable', methods=['POST'])
@jwt_required()
def enable_two_factor():
    """
    Enable 2FA
    ---
    tags:
      - Authentication
    summary: Enable two-factor authentication
    description: Enable email-based two-factor authentication for the authenticated user
    security:
      - Bearer: []
    responses:
      200:
        description: 2FA enabled successfully
        schema:
          type: object
          properties:
            message:
              type: string
            enabled:
              type: boolean
      400:
        description: 2FA already enabled
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
        
        if getattr(user, 'two_factor_enabled', False):
            return jsonify({'error': '2FA is already enabled'}), 400
        
        # Enable 2FA (email-based, no setup needed)
        user.two_factor_enabled = True
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '2FA enabled successfully. You will receive verification codes via email when logging in.'
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"2FA enable error: {str(e)}")
        return jsonify({'error': 'Failed to enable 2FA'}), 500

@auth_bp.route('/2fa/disable', methods=['POST'])
@jwt_required()
def disable_two_factor():
    """Disable 2FA for the current user."""
    try:
        current_user_id = get_jwt_identity()
        user = User.query.get(current_user_id)
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        if not getattr(user, 'two_factor_enabled', False):
            return jsonify({'error': '2FA is not enabled'}), 400
        
        data = request.get_json() or {}
        password = data.get('password', '')
        
        if not password:
            return jsonify({'error': 'Password is required to disable 2FA'}), 400
        
        # Verify password
        if not user.check_password(password):
            return jsonify({'error': 'Invalid password'}), 400
        
        # Disable 2FA and clear any pending codes
        user.two_factor_enabled = False
        user.two_factor_email_code = None
        user.two_factor_email_expires = None
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '2FA disabled successfully'
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"2FA disable error: {str(e)}")
        return jsonify({'error': 'Failed to disable 2FA'}), 500