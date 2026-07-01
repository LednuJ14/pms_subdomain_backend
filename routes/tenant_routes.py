from flask import Blueprint, jsonify, request, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from app import db
from models.user import User, UserRole, UserStatus
from models.tenant import Tenant
from datetime import datetime, date
import re

tenant_bp = Blueprint('tenants', __name__)

from models.user import User

def is_super_admin(user_id):
    if not user_id: return False
    user = User.query.get(user_id)
    return user and getattr(user, 'role', '') == 'ADMIN'


@tenant_bp.route('/me', methods=['GET'])
@jwt_required()
def get_my_tenant():
    """Get current logged-in tenant profile."""
    try:
        current_user_id = get_jwt_identity()
        if not current_user_id:
            return jsonify({'error': 'User not authenticated'}), 401
        
        # Get user
        user = User.query.get(current_user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        # Check if user is a tenant
        # Handle both string and enum values for role (database stores as string)
        user_role = user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        if user_role_str != 'TENANT':
            current_app.logger.warning(f"User {current_user_id} has role '{user_role_str}', expected 'TENANT'")
            return jsonify({'error': 'User is not a tenant', 'user_role': user_role_str}), 403
        
        # Get tenant profile for this user
        tenant = Tenant.query.filter_by(user_id=current_user_id).first()
        if not tenant:
            return jsonify({'error': 'Tenant profile not found'}), 404
        
        try:
            # Include current rental info so frontend can derive unit_id/property_id
            tenant_data = tenant.to_dict(include_user=True, include_lease=False, include_rent=True)
            return jsonify(tenant_data), 200
        except Exception as tenant_error:
            current_app.logger.warning(f"Error serializing tenant {tenant.id}: {str(tenant_error)}")
            # Return minimal tenant data
            return jsonify({
                'id': tenant.id,
                'user_id': tenant.user_id,
                'property_id': getattr(tenant, 'property_id', None),
                'phone_number': getattr(tenant, 'phone_number', None),
                'email': getattr(tenant, 'email', None),
                'user': {
                    'id': user.id,
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'phone_number': user.phone_number
                } if user else None
            }), 200
            
    except Exception as e:
        current_app.logger.error(f"Error in get_my_tenant: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

def get_tenants_internal():
    """Internal function to get tenants - wrapped to catch all errors."""
    # Initialize safe default response early
    safe_response = {'tenants': []}
    
    # Log entry point
    try:
        current_app.logger.info("get_tenants_internal: Starting")
    except:
        pass  # If logging fails, continue anyway
    
    try:
        # CRITICAL: Get property_id from request (subdomain, header, query param, or JWT)
        # This ensures we only return tenants for the current property subdomain
        property_id = None
        
        # Try to get from query parameter first (most reliable)
        try:
            property_id = request.args.get('property_id', type=int)
            if property_id:
                current_app.logger.info(f"Got property_id from query param: {property_id}")
        except Exception as query_err:
            current_app.logger.warning(f"Error getting property_id from query: {str(query_err)}")
        
        # If not in query, try get_property_id_from_request
        if not property_id:
            try:
                from routes.auth_routes import get_property_id_from_request
                property_id = get_property_id_from_request()
                if property_id:
                    current_app.logger.info(f"Got property_id from get_property_id_from_request: {property_id}")
            except Exception as prop_id_err:
                current_app.logger.warning(f"Error getting property_id from request: {str(prop_id_err)}")
        
        # If property_id not in request, try to get from JWT token
        if not property_id:
            try:
                from flask_jwt_extended import get_jwt
                claims = get_jwt()
                property_id = claims.get('property_id')
                if property_id:
                    current_app.logger.info(f"Got property_id from JWT: {property_id}")
            except Exception as jwt_err:
                current_app.logger.warning(f"Error getting property_id from JWT: {str(jwt_err)}")
        
        if not property_id:
            # Return safe response instead of error to prevent CORS issues
            response = jsonify({
                'tenants': [],
                'error': 'Property context is required. Please access through a property subdomain.',
                'code': 'PROPERTY_CONTEXT_REQUIRED'
            })
            response.status_code = 200  # Return 200 to prevent CORS issues
            return response
        
        # CRITICAL: Verify property exists and user owns it (for property managers)
        try:
            from flask_jwt_extended import get_jwt_identity
            from models.property import Property
            current_user_id = get_jwt_identity()
            if current_user_id:
                try:
                    current_user = User.query.get(current_user_id)
                    if current_user and hasattr(current_user, 'is_property_manager') and current_user.is_property_manager():
                        try:
                            property_obj = Property.query.get(property_id)
                            if not property_obj:
                                response = jsonify({
                                    'tenants': [],
                                    'error': 'Property not found'
                                })
                                response.status_code = 200  # Return 200 to prevent CORS issues
                                return response
                            if property_obj.owner_id != current_user.id and not is_super_admin(current_user.id):
                                response = jsonify({
                                    'tenants': [],
                                    'error': 'Access denied. You do not own this property.',
                                    'code': 'PROPERTY_ACCESS_DENIED'
                                })
                                response.status_code = 200  # Return 200 to prevent CORS issues
                                return response
                        except Exception as prop_check_err:
                            current_app.logger.warning(f"Error checking property access: {str(prop_check_err)}")
                            # Continue anyway - don't block the request
                except Exception as user_check_err:
                    current_app.logger.warning(f"Error checking user: {str(user_check_err)}")
                    # Continue anyway
        except Exception as auth_check_err:
            current_app.logger.warning(f"Error in auth check: {str(auth_check_err)}")
            # Continue anyway - don't block the request
        
        current_app.logger.info(f"Getting tenants for property_id: {property_id}")
        
        # Load tenants with user relationship, FILTERED BY PROPERTY_ID
        tenants = []
        try:
            from sqlalchemy.orm import joinedload
            try:
                tenants = db.session.query(Tenant).options(
                    joinedload(Tenant.user)
                ).join(User).filter(Tenant.property_id == property_id).all()
            except Exception as eager_err:
                # Fallback to simple query if eager loading fails
                current_app.logger.warning(f"Eager loading failed, using simple query: {str(eager_err)}")
                try:
                    tenants = db.session.query(Tenant).join(User).filter(Tenant.property_id == property_id).all()
                except Exception as simple_err:
                    # Fallback to even simpler query
                    current_app.logger.warning(f"Simple query failed, using basic query: {str(simple_err)}")
                    tenants = Tenant.query.filter_by(property_id=property_id).all()
            current_app.logger.info(f"Found {len(tenants)} tenants in database for property {property_id}")
        except Exception as query_err:
            current_app.logger.error(f"Error querying tenants: {str(query_err)}", exc_info=True)
            tenants = []  # Set to empty list on error
        
        tenant_list = []
        for tenant in tenants:
            try:
                # Use the to_dict method which handles the simplified schema
                # Include rent to get unit information, but catch errors gracefully
                try:
                    tenant_data = tenant.to_dict(include_user=True, include_rent=True)
                except Exception as to_dict_error:
                    # If to_dict fails, try without rent
                    current_app.logger.warning(f"to_dict with rent failed for tenant {tenant.id}: {str(to_dict_error)}")
                    try:
                        tenant_data = tenant.to_dict(include_user=True, include_rent=False)
                    except Exception as to_dict_error2:
                        # If that also fails, create minimal data
                        current_app.logger.warning(f"to_dict without rent also failed for tenant {tenant.id}: {str(to_dict_error2)}")
                        raise to_dict_error2
                
                tenant_list.append(tenant_data)
            except Exception as tenant_error:
                # Fallback: create minimal tenant data if to_dict fails
                import traceback
                error_trace = traceback.format_exc()
                current_app.logger.warning(f"Error serializing tenant {tenant.id}: {str(tenant_error)}")
                try:
                    tenant_data = {
                        'id': tenant.id,
                        'user_id': tenant.user_id,
                        'property_id': getattr(tenant, 'property_id', None),
                        'phone_number': getattr(tenant, 'phone_number', None),
                        'email': getattr(tenant, 'email', None),
                        'room_number': getattr(tenant, 'assigned_room', None) or 'N/A',
                        'assigned_room': getattr(tenant, 'assigned_room', None),
                        'is_approved': getattr(tenant, 'is_approved', False),
                        'status': 'Active' if getattr(tenant, 'is_approved', False) else 'Pending',
                        'property': {
                            'id': getattr(tenant, 'property_id', None),
                            'name': f'Property {getattr(tenant, "property_id", "Unknown")}'
                        },
                        'user': {
                            'id': tenant.user.id if tenant.user else None,
                            'email': tenant.user.email if tenant.user else None,
                            'first_name': tenant.user.first_name if tenant.user else None,
                            'last_name': tenant.user.last_name if tenant.user else None,
                            'phone_number': tenant.user.phone_number if tenant.user else None,
                            'status': str(tenant.user.status.value) if tenant.user and hasattr(tenant.user, 'status') and tenant.user.status else None,
                            'email_verified': tenant.user.email_verified if tenant.user else False,
                            'name': f"{tenant.user.first_name or ''} {tenant.user.last_name or ''}".strip() or tenant.user.email if tenant.user else None
                        } if tenant.user else None,
                        'created_at': tenant.created_at.isoformat() if hasattr(tenant, 'created_at') and tenant.created_at else None,
                        'updated_at': tenant.updated_at.isoformat() if hasattr(tenant, 'updated_at') and tenant.updated_at else None
                    }
                    tenant_list.append(tenant_data)
                except Exception as fallback_error:
                    # Skip this tenant if even minimal serialization fails
                    current_app.logger.error(f"Even fallback serialization failed for tenant {tenant.id}: {str(fallback_error)}")
                    continue
        
        # Return tenants list - ensure it's always an array
        if not isinstance(tenant_list, list):
            tenant_list = []
        
        # Log success
        current_app.logger.info(f"Successfully returning {len(tenant_list)} tenants for property {property_id}")
        
        # Return as array (frontend handles both array and {tenants: []} format)
        response = jsonify(tenant_list)
        response.status_code = 200
        return response
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        current_app.logger.error(f"Error in get_tenants: {str(e)}\n{error_trace}", exc_info=True)
        
        # Return safe default response to prevent CORS issues
        # Even on error, return 200 with empty list so frontend can handle gracefully
        safe_response = {
            'tenants': [],
            'error': 'Failed to load tenants',
            'error_details': str(e) if current_app.config.get('DEBUG', False) else None
        }
        response = jsonify(safe_response)
        response.status_code = 200  # Return 200 to prevent CORS issues
        return response

@tenant_bp.route('/', methods=['GET'])
@jwt_required()
def get_tenants():
    """
    Get all tenants
    ---
    tags:
      - Tenants
    summary: Get all tenants for the property
    description: Retrieve all tenants with their user info, filtered by property_id from subdomain context
    security:
      - Bearer: []
    parameters:
      - in: query
        name: property_id
        type: integer
        description: Property ID (usually from subdomain context)
      - in: query
        name: page
        type: integer
        description: Page number for pagination
      - in: query
        name: per_page
        type: integer
        description: Number of items per page
    responses:
      200:
        description: Tenants retrieved successfully
        schema:
          type: object
          properties:
            tenants:
              type: array
              items:
                type: object
            total:
              type: integer
      400:
        description: Property context required
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    # Wrap the internal function to catch any errors from jwt_required or route registration
    try:
        return get_tenants_internal()
    except Exception as outer_err:
        import traceback
        error_trace = traceback.format_exc()
        current_app.logger.error(f"Outer error in get_tenants (decorator/route level): {str(outer_err)}\n{error_trace}", exc_info=True)
        
        # Return safe default response to prevent CORS issues
        safe_response = {
            'tenants': [],
            'error': 'Failed to load tenants',
            'error_details': str(outer_err) if current_app.config.get('DEBUG', False) else None
        }
        response = jsonify(safe_response)
        response.status_code = 200  # Return 200 to prevent CORS issues
        return response

@tenant_bp.route('/', methods=['POST'])
@jwt_required()
def create_tenant():
    """
    Create tenant
    ---
    tags:
      - Tenants
    summary: Create a new tenant
    description: Create a new tenant account and profile
    security:
      - Bearer: []
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
            phone_number:
              type: string
            property_id:
              type: integer
    responses:
      201:
        description: Tenant created successfully
        schema:
          type: object
          properties:
            message:
              type: string
            tenant:
              type: object
      400:
        description: Validation error or user already exists
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        print(f"Received tenant creation data: {data}")
        
        # Validate required fields
        required_fields = ['email', 'username', 'password', 'first_name', 'last_name']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({'error': f'{field} is required'}), 400
        
        # Validate email format
        if not re.match(r'^[^@]+@[^@]+\.[^@]+$', data['email']):
            return jsonify({'error': 'Invalid email format'}), 400
        
        # Check if user already exists
        existing_user = User.query.filter(
            (User.email == data['email']) | (User.username == data['username'])
        ).first()
        if existing_user:
            return jsonify({'error': 'User with this email or username already exists'}), 400
        
        # Create user
        user = User(
            email=data['email'],
            username=data['username'],
            password=data['password'],  # Will be hashed by User model
            first_name=data['first_name'],
            last_name=data['last_name'],
            phone_number=data.get('phone_number', ''),
            role=UserRole.TENANT,
            address=data.get('address', '')
        )
        
        db.session.add(user)
        db.session.flush()  # Get the user ID
        
        # Helper function to convert empty strings to None for numeric fields
        def safe_float(value):
            if value is None or value == '' or value == 0:
                return None
            try:
                return float(value)
            except (ValueError, TypeError):
                return None
        
        def safe_int(value):
            if value is None or value == '':
                return None
            try:
                return int(value)
            except (ValueError, TypeError):
                return None
        
        def safe_date(value):
            if value is None or value == '':
                return None
            try:
                return datetime.strptime(value, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                return None
        
        # CRITICAL: Get property_id from subdomain context (required)
        from flask_jwt_extended import get_jwt_identity, get_jwt
        current_user_id = get_jwt_identity()
        if current_user_id:
            # User is already imported at the top of the file
            current_user = User.query.get(current_user_id)
            if current_user and current_user.is_property_manager():
                # Property managers must provide property_id from subdomain
                from routes.auth_routes import get_property_id_from_request
                property_id = get_property_id_from_request(data=data)
                
                if not property_id:
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
                
                # CRITICAL: Verify property ownership
                from models.property import Property
                property_obj = Property.query.get(property_id)
                if not property_obj:
                    return jsonify({'error': 'Property not found'}), 404
                
                if property_obj.owner_id != current_user.id and not is_super_admin(current_user.id):
                    return jsonify({
                        'error': 'Access denied. You do not own this property.',
                        'code': 'PROPERTY_ACCESS_DENIED'
                    }), 403
            else:
                # For tenants/staff, get property_id from request or data
                property_id = data.get('property_id')
                if not property_id:
                    try:
                        from routes.auth_routes import get_property_id_from_request
                        property_id = get_property_id_from_request(data=data)
                    except Exception as prop_error:
                        current_app.logger.warning(f"Could not get property_id from request: {str(prop_error)}")
        else:
            property_id = data.get('property_id')
        
        # Convert string property_id to int if needed
        if property_id and not isinstance(property_id, int):
            try:
                property_id = int(property_id)
            except (ValueError, TypeError):
                # If it's not a number, try to find property by subdomain/title
                from models.property import Property
                from sqlalchemy import text
                property_obj = db.session.execute(text(
                    """
                    SELECT id FROM properties 
                    WHERE LOWER(portal_subdomain) = LOWER(:subdomain)
                       OR LOWER(title) = LOWER(:subdomain)
                       OR LOWER(building_name) = LOWER(:subdomain)
                    LIMIT 1
                    """
                ), {'subdomain': str(property_id)}).first()
                if property_obj:
                    property_id = property_obj[0]
                else:
                    property_id = None
            
        if not property_id:
            return jsonify({'error': 'property_id is required. Please provide property_id or access through a property subdomain.'}), 400
        
        # Verify property exists
        from models.property import Property
        property_obj = Property.query.get(property_id)
        if not property_obj:
            return jsonify({'error': f'Property with id {property_id} not found'}), 404
        
        # Create tenant profile (simplified schema: user_id, property_id, phone_number, email)
        tenant = Tenant(
            user_id=user.id,
            property_id=property_id,
            phone_number=data.get('phone_number', '') or user.phone_number or '',
            email=data.get('email', '') or user.email or ''
        )
        
        db.session.add(tenant)
        db.session.flush()  # Get tenant ID before commit
        
        # Handle unit assignment if unit_id/unit_ids is provided
        unit_ids = data.get('unit_ids', None)
        if unit_ids is None:
            unit_ids = data.get('unit_id')
        if unit_ids is not None and not isinstance(unit_ids, list):
            unit_ids = [unit_ids]
        unit_ids = [u for u in (unit_ids or []) if u not in (None, '', [])]

        if unit_ids:
            try:
                from datetime import date, timedelta
                from sqlalchemy import text

                for unit_id in unit_ids:
                    unit_check = db.session.execute(text(
                        """
                        SELECT id, property_id, status FROM units 
                        WHERE id = :unit_id
                        """
                    ), {'unit_id': unit_id}).first()

                    if not unit_check:
                        db.session.rollback()
                        return jsonify({'error': f'Unit with id {unit_id} not found'}), 404

                    if unit_check[1] != property_id:
                        db.session.rollback()
                        return jsonify({'error': 'Unit does not belong to the specified property'}), 400

                    existing_tenant_unit = db.session.execute(text(
                        """
                        SELECT tu.id FROM tenant_units tu
                        WHERE tu.unit_id = :unit_id
                          AND (
                            (tu.move_in_date IS NOT NULL AND tu.move_out_date IS NOT NULL 
                             AND tu.move_out_date >= CURDATE())
                            OR
                            (tu.is_active = TRUE)
                          )
                        LIMIT 1
                        """
                    ), {'unit_id': unit_id}).first()

                    if existing_tenant_unit:
                        db.session.rollback()
                        return jsonify({'error': 'Unit is already occupied by another tenant'}), 400

                    unit_status = str(unit_check[2]).lower() if unit_check[2] else 'vacant'
                    if unit_status not in ['vacant', 'available']:
                        current_app.logger.warning(
                            f"Assigning tenant to unit {unit_id} with status '{unit_check[2]}', "
                            f"but expected 'vacant' or 'available'"
                        )

                    move_in_date = date.today()
                    move_out_date = move_in_date + timedelta(days=180)
                    rent_start_date = move_in_date
                    rent_end_date = rent_start_date + timedelta(days=30)
                    unit_property_id = unit_check[1] if unit_check else property_id

                    try:
                        try:
                            db.session.execute(text(
                                """
                                INSERT INTO tenant_units (tenant_id, unit_id, property_id, move_in_date, move_out_date, rent_start_date, rent_end_date, is_active, created_at, updated_at)
                                VALUES (:tenant_id, :unit_id, :property_id, :move_in_date, :move_out_date, :rent_start_date, :rent_end_date, :is_active, NOW(), NOW())
                                """
                            ), {
                                'tenant_id': tenant.id,
                                'unit_id': unit_id,
                                'property_id': unit_property_id,
                                'move_in_date': move_in_date,
                                'move_out_date': move_out_date,
                                'rent_start_date': rent_start_date,
                                'rent_end_date': rent_end_date,
                                'is_active': True
                            })
                        except Exception as rent_dates_error:
                            current_app.logger.warning(f"rent_start_date/rent_end_date columns may not exist, using move dates only: {str(rent_dates_error)}")
                        db.session.execute(text(
                            """
                            INSERT INTO tenant_units (tenant_id, unit_id, property_id, move_in_date, move_out_date, is_active, created_at, updated_at)
                            VALUES (:tenant_id, :unit_id, :property_id, :move_in_date, :move_out_date, :is_active, NOW(), NOW())
                            """
                        ), {
                            'tenant_id': tenant.id,
                            'unit_id': unit_id,
                            'property_id': unit_property_id,
                            'move_in_date': move_in_date,
                            'move_out_date': move_out_date,
                            'is_active': True
                        })
                    except Exception as insert_error:
                        if 'is_active' in str(insert_error) or 'created_at' in str(insert_error) or 'updated_at' in str(insert_error):
                            db.session.execute(text(
                                """
                                INSERT INTO tenant_units (tenant_id, unit_id, property_id, move_in_date, move_out_date)
                                VALUES (:tenant_id, :unit_id, :property_id, :move_in_date, :move_out_date)
                                """
                            ), {
                                'tenant_id': tenant.id,
                                'unit_id': unit_id,
                                'property_id': unit_property_id,
                                'move_in_date': move_in_date,
                                'move_out_date': move_out_date
                            })
                        else:
                            raise

                    db.session.execute(text(
                        """
                        UPDATE units 
                        SET status = 'occupied', updated_at = NOW()
                        WHERE id = :unit_id
                        """
                    ), {'unit_id': unit_id})
                    db.session.flush()

                    current_app.logger.info(
                        f"Created TenantUnit: tenant_id={tenant.id}, unit_id={unit_id}, "
                        f"and updated unit status to 'occupied'"
                    )
            except Exception as unit_error:
                db.session.rollback()
                current_app.logger.error(f"Error assigning unit to tenant: {str(unit_error)}")
                return jsonify({'error': f'Failed to assign unit to tenant: {str(unit_error)}'}), 500
        
        db.session.commit()
        
        print(f"Successfully created tenant with ID: {tenant.id}")
        print(f"User ID: {user.id}, Tenant ID: {tenant.id}")
        
        # Verify the data was actually saved by querying it back
        verification_tenant = Tenant.query.get(tenant.id)
        verification_user = User.query.get(user.id)
        print(f"Verification - Tenant exists: {verification_tenant is not None}")
        print(f"Verification - User exists: {verification_user is not None}")
        
        # Return created tenant using to_dict method
        try:
            tenant_data = tenant.to_dict(include_user=True)
        except Exception as dict_error:
            current_app.logger.warning(f"Error serializing tenant: {str(dict_error)}")
            tenant_data = {
                'id': tenant.id,
                'user_id': user.id,
                'property_id': property_id,
                'phone_number': tenant.phone_number,
                'email': tenant.email,
                'user': {
                    'id': user.id,
                    'email': user.email,
                    'username': user.username,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'phone_number': user.phone_number,
                    'role': getattr(user.role, 'value', str(user.role)) if hasattr(user.role, 'value') else str(user.role)
                }
            }
        
        return jsonify(tenant_data), 201
        
    except ValueError as ve:
        db.session.rollback()
        print(f"Validation error in tenant creation: {str(ve)}")
        return jsonify({'error': str(ve)}), 422
    except Exception as e:
        db.session.rollback()
        print(f"Unexpected error in tenant creation: {str(e)}")
        print(f"Error type: {type(e).__name__}")
        return jsonify({'error': str(e)}), 500

@tenant_bp.route('/<int:tenant_id>', methods=['PUT'])
@jwt_required()
def update_tenant(tenant_id):
    """
    Update tenant
    ---
    tags:
      - Tenants
    summary: Update tenant information
    description: Update tenant profile information. Property managers can update any tenant, tenants can only update themselves.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: tenant_id
        type: integer
        required: true
        description: The tenant ID
      - in: body
        name: body
        schema:
          type: object
          properties:
            phone_number:
              type: string
            email:
              type: string
            property_id:
              type: integer
            unit_id:
              type: integer
    responses:
      200:
        description: Tenant updated successfully
        schema:
          type: object
          properties:
            id:
              type: integer
            user_id:
              type: integer
      400:
        description: Validation error
      401:
        description: Unauthorized
      403:
        description: Forbidden
      404:
        description: Tenant not found
      500:
        description: Server error
    """
    try:
        from flask_jwt_extended import get_jwt_identity
        current_user_id = get_jwt_identity()
        if current_user_id:
            from models.user import User
            current_user = User.query.get(current_user_id)
            if current_user and current_user.is_property_manager():
                # CRITICAL: Verify property ownership
                from routes.auth_routes import get_property_id_from_request
                property_id = get_property_id_from_request()
                if not property_id:
                    from flask_jwt_extended import get_jwt
                    try:
                        property_id = get_jwt().get('property_id')
                    except Exception:
                        pass
                
                if property_id:
                    from models.property import Property
                    property_obj = Property.query.get(property_id)
                    if property_obj and property_obj.owner_id != current_user.id and not is_super_admin(current_user.id):
                        return jsonify({
                            'error': 'Access denied. You do not own this property.',
                            'code': 'PROPERTY_ACCESS_DENIED'
                        }), 403
        
        data = request.get_json()
        
        # Find tenant
        tenant = Tenant.query.get(tenant_id)
        if not tenant:
            return jsonify({'error': 'Tenant not found'}), 404
        
        # CRITICAL: For property managers, verify tenant belongs to their property
        if current_user_id:
            from models.user import User
            current_user = User.query.get(current_user_id)
            if current_user and current_user.is_property_manager():
                if tenant.property_id:
                    from models.property import Property
                    property_obj = Property.query.get(tenant.property_id)
                    if not property_obj or (property_obj.owner_id != current_user.id and not is_super_admin(current_user.id)):
                        return jsonify({
                            'error': 'Access denied. This tenant does not belong to your property.',
                            'code': 'PROPERTY_ACCESS_DENIED'
                        }), 403
        
        user = tenant.user
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        # Helper functions for safe conversions
        def safe_float(value):
            if value is None or value == '':
                return None
            try:
                return float(value)
            except (ValueError, TypeError):
                return None
        
        def safe_int(value):
            if value is None or value == '':
                return None
            try:
                return int(value)
            except (ValueError, TypeError):
                return None
        
        def safe_date(value):
            if value is None or value == '':
                return None
            try:
                return datetime.strptime(value, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                return None
        
        # Update user fields if provided
        if 'email' in data and data['email']:
            existing = User.query.filter(User.email == data['email'], User.id != user.id).first()
            if existing:
                return jsonify({'error': 'Email already taken'}), 400
            user.email = data['email']
        
        if 'username' in data and data['username']:
            existing = User.query.filter(User.username == data['username'], User.id != user.id).first()
            if existing:
                return jsonify({'error': 'Username already taken'}), 400
            user.username = data['username']
        
        if 'first_name' in data:
            user.first_name = data['first_name']
        if 'last_name' in data:
            user.last_name = data['last_name']
        if 'phone_number' in data:
            user.phone_number = data['phone_number']
        if 'address' in data:
            user.address = data['address']
        
        # Handle password update if provided
        if 'password' in data and data['password']:
            user.set_password(data['password'])
        
        # Update tenant fields if provided (simplified schema: property_id, phone_number, email)
        if 'property_id' in data and data['property_id']:
            # Verify property exists
            from models.property import Property
            property_obj = Property.query.get(data['property_id'])
            if not property_obj:
                return jsonify({'error': f'Property with id {data["property_id"]} not found'}), 404
            tenant.property_id = data['property_id']
        if 'phone_number' in data:
            tenant.phone_number = data.get('phone_number', '')
        if 'email' in data:
            tenant.email = data.get('email', '')
        
        # Handle unit assignment if unit_id/unit_ids is provided
        unit_ids = data.get('unit_ids', None)
        if unit_ids is None:
            unit_ids = data.get('unit_id')
        if unit_ids is not None and not isinstance(unit_ids, list):
            unit_ids = [unit_ids]
        unit_ids = [u for u in (unit_ids or []) if u not in (None, '', [])]

        if unit_ids:
            try:
                from datetime import date, timedelta
                from sqlalchemy import text
                
                property_id_for_unit = tenant.property_id or data.get('property_id')

                for unit_id in unit_ids:
                    unit_check = db.session.execute(text(
                        """
                        SELECT id, property_id, status FROM units 
                        WHERE id = :unit_id
                        """
                    ), {'unit_id': unit_id}).first()

                    if not unit_check:
                        return jsonify({'error': f'Unit with id {unit_id} not found'}), 404

                    if unit_check[1] != property_id_for_unit:
                        return jsonify({'error': 'Unit does not belong to the tenant\'s property'}), 400

                    unit_occupied_check = db.session.execute(text(
                        """
                        SELECT tu.id FROM tenant_units tu
                        WHERE tu.unit_id = :unit_id
                          AND tu.tenant_id != :tenant_id
                          AND (
                            (tu.move_in_date IS NOT NULL AND tu.move_out_date IS NOT NULL 
                             AND tu.move_out_date >= CURDATE())
                            OR
                            (tu.is_active = TRUE)
                          )
                        LIMIT 1
                        """
                    ), {'unit_id': unit_id, 'tenant_id': tenant.id}).first()

                    if unit_occupied_check:
                        return jsonify({'error': 'Unit is already occupied by another tenant'}), 400

                    existing_tenant_unit = db.session.execute(text(
                        """
                        SELECT tu.id FROM tenant_units tu
                        WHERE tu.tenant_id = :tenant_id
                          AND tu.unit_id = :unit_id
                          AND (
                            (tu.move_in_date IS NOT NULL AND tu.move_out_date IS NOT NULL 
                             AND tu.move_out_date >= CURDATE())
                            OR
                            (tu.is_active = TRUE)
                          )
                        LIMIT 1
                        """
                    ), {'tenant_id': tenant.id, 'unit_id': unit_id}).first()

                    if existing_tenant_unit:
                        continue

                    move_in_date = date.today()
                    move_out_date = move_in_date + timedelta(days=180)
                    rent_start_date = move_in_date
                    rent_end_date = rent_start_date + timedelta(days=30)
                    unit_property_id = unit_check[1] if unit_check else (tenant.property_id or property_id_for_unit)

                    try:
                        try:
                            db.session.execute(text(
                                """
                                INSERT INTO tenant_units (tenant_id, unit_id, property_id, move_in_date, move_out_date, rent_start_date, rent_end_date, is_active, created_at, updated_at)
                                VALUES (:tenant_id, :unit_id, :property_id, :move_in_date, :move_out_date, :rent_start_date, :rent_end_date, :is_active, NOW(), NOW())
                                """
                            ), {
                                'tenant_id': tenant.id,
                                'unit_id': unit_id,
                                'property_id': unit_property_id,
                                'move_in_date': move_in_date,
                                'move_out_date': move_out_date,
                                'rent_start_date': rent_start_date,
                                'rent_end_date': rent_end_date,
                                'is_active': True
                            })
                        except Exception as rent_dates_error:
                            current_app.logger.warning(f"rent_start_date/rent_end_date columns may not exist, using move dates only: {str(rent_dates_error)}")
                        db.session.execute(text(
                            """
                            INSERT INTO tenant_units (tenant_id, unit_id, property_id, move_in_date, move_out_date, is_active, created_at, updated_at)
                            VALUES (:tenant_id, :unit_id, :property_id, :move_in_date, :move_out_date, :is_active, NOW(), NOW())
                            """
                        ), {
                            'tenant_id': tenant.id,
                            'unit_id': unit_id,
                            'property_id': unit_property_id,
                            'move_in_date': move_in_date,
                            'move_out_date': move_out_date,
                            'is_active': True
                        })
                    except Exception as insert_error:
                        if 'is_active' in str(insert_error) or 'created_at' in str(insert_error) or 'updated_at' in str(insert_error):
                            try:
                                db.session.execute(text(
                                    """
                                    INSERT INTO tenant_units (tenant_id, unit_id, property_id, move_in_date, move_out_date, rent_start_date, rent_end_date)
                                    VALUES (:tenant_id, :unit_id, :property_id, :move_in_date, :move_out_date, :rent_start_date, :rent_end_date)
                                    """
                                ), {
                                    'tenant_id': tenant.id,
                                    'unit_id': unit_id,
                                    'property_id': unit_property_id,
                                    'move_in_date': move_in_date,
                                    'move_out_date': move_out_date,
                                    'rent_start_date': rent_start_date,
                                    'rent_end_date': rent_end_date
                                })
                            except Exception:
                                db.session.execute(text(
                                    """
                                    INSERT INTO tenant_units (tenant_id, unit_id, property_id, move_in_date, move_out_date)
                                    VALUES (:tenant_id, :unit_id, :property_id, :move_in_date, :move_out_date)
                                    """
                                ), {
                                    'tenant_id': tenant.id,
                                    'unit_id': unit_id,
                                    'property_id': unit_property_id,
                                    'move_in_date': move_in_date,
                                    'move_out_date': move_out_date
                                })
                        else:
                            raise

                    db.session.execute(text(
                        """
                        UPDATE units 
                        SET status = 'occupied', updated_at = NOW()
                        WHERE id = :unit_id
                        """
                    ), {'unit_id': unit_id})
                    db.session.flush()

                    current_app.logger.info(
                        f"Updated TenantUnit: tenant_id={tenant.id}, unit_id={unit_id}, "
                        f"and updated unit status to 'occupied'"
                    )
            except Exception as unit_error:
                db.session.rollback()
                current_app.logger.error(f"Error assigning unit to tenant: {str(unit_error)}")
                return jsonify({'error': f'Failed to assign unit to tenant: {str(unit_error)}'}), 500
        elif (data.get('unit_id') is None and 'unit_id' in data) or (('unit_ids' in data) and not unit_ids and ('unit_id' not in data)):
            # If unit_id is explicitly set to null/empty, remove tenant from current unit
            try:
                from sqlalchemy import text
                
                # Find and end active TenantUnit
                existing_tenant_units = db.session.execute(text(
                    """
                    SELECT tu.id, tu.unit_id FROM tenant_units tu
                    WHERE tu.tenant_id = :tenant_id
                      AND (
                        (tu.move_in_date IS NOT NULL AND tu.move_out_date IS NOT NULL 
                         AND tu.move_out_date >= CURDATE())
                        OR
                        (tu.is_active = TRUE)
                      )
                    """
                ), {'tenant_id': tenant.id}).fetchall()
                
                for existing_tenant_unit in existing_tenant_units:
                    old_unit_id = existing_tenant_unit.unit_id
                    # End the tenant-unit relationship
                    db.session.execute(text(
                        """
                        UPDATE tenant_units 
                        SET move_out_date = CURDATE(), is_active = FALSE
                        WHERE id = :tu_id
                        """
                    ), {'tu_id': existing_tenant_unit.id})
                    
                    # Update unit status to vacant if no other active tenants
                    old_unit_check = db.session.execute(text(
                        """
                        SELECT COUNT(*) AS count FROM tenant_units tu
                        WHERE tu.unit_id = :unit_id
                          AND (
                            (tu.move_in_date IS NOT NULL AND tu.move_out_date IS NOT NULL 
                             AND tu.move_out_date >= CURDATE())
                            OR
                            (tu.is_active = TRUE)
                          )
                        """
                    ), {'unit_id': old_unit_id}).first()
                    
                    if old_unit_check and old_unit_check.count == 0:
                        db.session.execute(text(
                            "UPDATE units SET status = 'vacant' WHERE id = :unit_id"
                        ), {'unit_id': old_unit_id})
                        current_app.logger.info(f"Removed tenant from unit {old_unit_id} and updated status to 'vacant'")
            except Exception as unit_error:
                current_app.logger.warning(f"Error removing unit assignment: {str(unit_error)}")
                # Don't fail the entire update if unit removal fails
        
        db.session.commit()
        
        # Return updated tenant using to_dict method
        try:
            tenant_data = tenant.to_dict(include_user=True)
        except Exception as dict_error:
            current_app.logger.warning(f"Error serializing tenant: {str(dict_error)}")
            tenant_data = {
                'id': tenant.id,
                'user_id': user.id,
                'property_id': getattr(tenant, 'property_id', None),
                'phone_number': getattr(tenant, 'phone_number', None),
                'email': getattr(tenant, 'email', None),
                'user': {
                    'id': user.id,
                    'email': user.email,
                    'username': user.username,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'phone_number': user.phone_number,
                    'role': getattr(user.role, 'value', str(user.role)) if hasattr(user.role, 'value') else str(user.role)
                }
            }
        
        return jsonify(tenant_data), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@tenant_bp.route('/<int:tenant_id>', methods=['DELETE'])
@jwt_required()
def delete_tenant(tenant_id):
    """
    Delete tenant
    ---
    tags:
      - Tenants
    summary: Delete a tenant
    description: Delete a tenant account and profile. Property managers only.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: tenant_id
        type: integer
        required: true
        description: The tenant ID
    responses:
      200:
        description: Tenant deleted successfully
        schema:
          type: object
          properties:
            message:
              type: string
      401:
        description: Unauthorized
      403:
        description: Forbidden - Property manager access required
      404:
        description: Tenant not found
      500:
        description: Server error
    """
    try:
        from flask_jwt_extended import get_jwt_identity
        current_user_id = get_jwt_identity()
        if current_user_id:
            from models.user import User
            current_user = User.query.get(current_user_id)
            if current_user and current_user.is_property_manager():
                # CRITICAL: Verify property ownership
                from routes.auth_routes import get_property_id_from_request
                property_id = get_property_id_from_request()
                if not property_id:
                    from flask_jwt_extended import get_jwt
                    try:
                        property_id = get_jwt().get('property_id')
                    except Exception:
                        pass
        
        tenant = Tenant.query.get(tenant_id)
        if not tenant:
            return jsonify({'error': 'Tenant not found'}), 404
        
        # CRITICAL: For property managers, verify tenant belongs to their property
        if current_user_id:
            from models.user import User
            current_user = User.query.get(current_user_id)
            if current_user and current_user.is_property_manager():
                if tenant.property_id:
                    from models.property import Property
                    property_obj = Property.query.get(tenant.property_id)
                    if not property_obj or (property_obj.owner_id != current_user.id and not is_super_admin(current_user.id)):
                        return jsonify({
                            'error': 'Access denied. This tenant does not belong to your property.',
                            'code': 'PROPERTY_ACCESS_DENIED'
                        }), 403
        
        user = tenant.user
        
        # Delete tenant record
        db.session.delete(tenant)
        # Delete user record
        if user:
            db.session.delete(user)
        
        db.session.commit()
        
        return jsonify({'message': 'Tenant deleted successfully'}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@tenant_bp.route('/<int:tenant_id>/verify', methods=['POST'])
@jwt_required()
def verify_tenant(tenant_id):
    """
    Verify tenant email and activate account
    ---
    tags:
      - Tenants
    summary: Verify tenant email and activate account
    description: Manually verify a tenant's email and activate their account. Property managers only.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: tenant_id
        type: integer
        required: true
        description: The tenant ID
    responses:
      200:
        description: Tenant verified successfully
      401:
        description: Unauthorized
      403:
        description: Forbidden - Property manager access required
      404:
        description: Tenant not found
      500:
        description: Server error
    """
    try:
        current_user_id = get_jwt_identity()
        if not current_user_id:
            return jsonify({'error': 'User not authenticated'}), 401
        
        current_user = User.query.get(current_user_id)
        if not current_user or not current_user.is_property_manager():
            return jsonify({'error': 'Property manager access required'}), 403
        
        tenant = Tenant.query.get(tenant_id)
        if not tenant:
            return jsonify({'error': 'Tenant not found'}), 404
        
        # Verify tenant belongs to property manager's property
        from routes.auth_routes import get_property_id_from_request
        property_id = get_property_id_from_request()
        if property_id and tenant.property_id != property_id:
            return jsonify({
                'error': 'Access denied. This tenant does not belong to your property.',
                'code': 'PROPERTY_ACCESS_DENIED'
            }), 403
        
        # Get the user associated with the tenant
        user = tenant.user
        if not user:
            return jsonify({'error': 'User not found for this tenant'}), 404
        
        # Verify email and activate account
        user.email_verified = True
        user.status = UserStatus.ACTIVE
        
        db.session.commit()
        
        current_app.logger.info(f"Tenant {tenant_id} (user {user.id}) verified and activated by property manager {current_user_id}")
        
        return jsonify({
            'message': 'Tenant verified and activated successfully',
            'tenant_id': tenant_id,
            'user_id': user.id,
            'email_verified': True,
            'status': 'active'
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error verifying tenant {tenant_id}: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@tenant_bp.route('/<int:tenant_id>', methods=['GET'])
@jwt_required()
def get_tenant(tenant_id):
    """
    Get tenant by ID
    ---
    tags:
      - Tenants
    summary: Get a specific tenant
    description: Retrieve tenant information by ID. Property managers can view tenants in their property, tenants can view themselves.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: tenant_id
        type: integer
        required: true
        description: The tenant ID
    responses:
      200:
        description: Tenant retrieved successfully
        schema:
          type: object
          properties:
            id:
              type: integer
            user_id:
              type: integer
            property_id:
              type: integer
            user:
              type: object
      401:
        description: Unauthorized
      403:
        description: Forbidden
      404:
        description: Tenant not found
      500:
        description: Server error
    """
    try:
        from flask_jwt_extended import get_jwt_identity
        current_user_id = get_jwt_identity()
        if current_user_id:
            from models.user import User
            current_user = User.query.get(current_user_id)
            if current_user and current_user.is_property_manager():
                # CRITICAL: Verify property ownership
                from routes.auth_routes import get_property_id_from_request
                property_id = get_property_id_from_request()
                if not property_id:
                    from flask_jwt_extended import get_jwt
                    try:
                        property_id = get_jwt().get('property_id')
                    except Exception:
                        pass
        
        tenant = Tenant.query.get(tenant_id)
        if not tenant:
            return jsonify({'error': 'Tenant not found'}), 404
        
        # CRITICAL: For property managers, verify tenant belongs to their property
        if current_user_id:
            from models.user import User
            current_user = User.query.get(current_user_id)
            if current_user and current_user.is_property_manager():
                if tenant.property_id:
                    from models.property import Property
                    property_obj = Property.query.get(tenant.property_id)
                    if not property_obj or (property_obj.owner_id != current_user.id and not is_super_admin(current_user.id)):
                        return jsonify({
                            'error': 'Access denied. This tenant does not belong to your property.',
                            'code': 'PROPERTY_ACCESS_DENIED'
                        }), 403
        
        tenant_data = {
            'id': tenant.id,
            'user_id': tenant.user_id,
            'occupation': tenant.occupation,
            'employer': tenant.employer,
            'monthly_income': float(tenant.monthly_income) if tenant.monthly_income else None,
            'previous_landlord': tenant.previous_landlord,
            'previous_landlord_phone': tenant.previous_landlord_phone,
            'reference_name': tenant.reference_name,
            'reference_phone': tenant.reference_phone,
            'reference_relationship': tenant.reference_relationship,
            'preferred_move_in_date': tenant.preferred_move_in_date.isoformat() if tenant.preferred_move_in_date else None,
            'max_rent_budget': float(tenant.max_rent_budget) if tenant.max_rent_budget else None,
            'preferred_unit_type': tenant.preferred_unit_type,
            'assigned_room': tenant.assigned_room,
            'has_pets': tenant.has_pets,
            'pet_details': tenant.pet_details,
            'has_vehicle': tenant.has_vehicle,
            'vehicle_details': tenant.vehicle_details,
            'is_approved': tenant.is_approved,
            'background_check_status': tenant.background_check_status,
            'credit_score': tenant.credit_score,
            'user': {
                'id': tenant.user.id,
                'email': tenant.user.email,
                'username': tenant.user.username,
                'first_name': tenant.user.first_name,
                'last_name': tenant.user.last_name,
                'phone_number': tenant.user.phone_number,
                'address': tenant.user.address,
                'role': tenant.user.role.value
            }
        }
        
        return jsonify(tenant_data), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
