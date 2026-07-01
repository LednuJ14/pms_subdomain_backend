from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from sqlalchemy import text, or_
from datetime import datetime, date, timezone
from decimal import Decimal

from app import db
from models.user import User, UserRole
from models.bill import Bill, Payment, BillType, BillStatus, PaymentStatus, PaymentMethod
from models.tenant import Tenant
from models.property import Unit, Property

billing_bp = Blueprint('billing', __name__)

from models.user import User

def is_super_admin(user_id):
    if not user_id: return False
    user = User.query.get(user_id)
    return user and getattr(user, 'role', '') == 'ADMIN'


def get_property_id_from_request(data=None):
    """
    Try to get property_id from request.
    Checks request body, query parameter, header, subdomain, or Origin header.
    Returns None if not found.
    """
    try:
        # Check query parameter first (numeric id)
        property_id = request.args.get('property_id', type=int)
        if property_id:
            return property_id
        
        # Check header for numeric id
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
        
        # Try to extract property by subdomain-style identifier from header or query
        # This supports cases where frontend sends a subdomain instead of numeric id
        raw_property_id = request.headers.get('X-Property-ID') or request.args.get('property_id')
        if raw_property_id and not str(raw_property_id).isdigit():
            subdomain = str(raw_property_id).lower().strip()
            try:
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
        current_app.logger.warning(f"Error getting property_id from request: {str(e)}")
        return None

def get_current_user():
    """Get current user from JWT token."""
    current_user_id = get_jwt_identity()
    if not current_user_id:
        return None
    return User.query.get(current_user_id)

def require_role(allowed_roles):
    """Decorator to require specific roles."""
    def decorator(f):
        @jwt_required()
        def decorated_function(*args, **kwargs):
            current_user = get_current_user()
            if not current_user:
                return jsonify({'error': 'User not found'}), 404
            
            # Check if user role is allowed
            # Handle both enum and string roles
            if isinstance(current_user.role, UserRole):
                user_role = current_user.role.value
            else:
                user_role = str(current_user.role).upper()
            
            # Normalize allowed_roles to uppercase strings for comparison
            allowed_roles_upper = [r.upper() if isinstance(r, str) else r.value.upper() if isinstance(r, UserRole) else str(r).upper() for r in allowed_roles]
            
            if user_role.upper() not in allowed_roles_upper:
                return jsonify({'error': 'Insufficient permissions'}), 403
            
            return f(current_user, *args, **kwargs)
        decorated_function.__name__ = f.__name__
        return decorated_function
    return decorator

# =====================================================
# BILL MANAGEMENT (Property Manager Only)
# =====================================================

@billing_bp.route('/bills', methods=['GET'])
@jwt_required()
def get_bills():
    """
    Get bills
    ---
    tags:
      - Billing
    summary: Get all bills for the current property context
    description: Retrieve bills filtered by user's role and property context
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
        name: tenant_id
        type: integer
    responses:
      200:
        description: Bills retrieved successfully
        schema:
          type: object
          properties:
            bills:
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
        
        # Get property_id from request (subdomain, header, query param, or JWT)
        property_id = get_property_id_from_request()
        
        # If property_id not in request, try to get from JWT token
        if not property_id:
            from flask_jwt_extended import get_jwt
            try:
                claims = get_jwt()
                property_id = claims.get('property_id')
            except Exception:
                pass
        
        # CRITICAL: Do NOT auto-detect from owned properties
        # Property managers must access through the correct subdomain
        if not property_id:
            return jsonify({
                'error': 'Property context is required. Please access through a property subdomain.',
                'code': 'PROPERTY_CONTEXT_REQUIRED'
            }), 400
        
        # CRITICAL: Verify property exists and user owns it (for property managers)
        if isinstance(current_user.role, UserRole):
            user_role = current_user.role.value
        else:
            user_role = str(current_user.role).upper()
        
        if user_role in ['MANAGER', 'PROPERTY_MANAGER', 'ADMIN']:
            property_obj = Property.query.get(property_id)
            if not property_obj:
                return jsonify({'error': 'Property not found'}), 404
            if property_obj.owner_id != current_user.id and not is_super_admin(current_user.id):
                return jsonify({
                    'error': 'Access denied. You do not own this property.',
                    'code': 'PROPERTY_ACCESS_DENIED'
                }), 403
        
        # Get query parameters
        tenant_id = request.args.get('tenant_id', type=int)
        status = request.args.get('status')
        bill_type = request.args.get('bill_type')
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        # Base query - filter by property through units
        query = Bill.query.join(Unit).filter(Unit.property_id == property_id)
        
        # Apply filters
        if tenant_id:
            query = query.filter(Bill.tenant_id == tenant_id)
        if status:
            # Use string comparison since status is now String type
            status_str = str(status).lower().strip()
            valid_statuses = ['pending', 'paid', 'partial', 'overdue', 'cancelled']
            if status_str not in valid_statuses:
                return jsonify({'error': f'Invalid status: {status}'}), 400
            query = query.filter(Bill.status == status_str)
        if bill_type:
            # Use string comparison since bill_type is now String type
            bill_type_str = str(bill_type).lower().strip()
            valid_bill_types = ['rent', 'utilities', 'maintenance', 'parking', 'other']
            if bill_type_str not in valid_bill_types:
                return jsonify({'error': f'Invalid bill type: {bill_type}'}), 400
            query = query.filter(Bill.bill_type == bill_type_str)
        
        # Order by due date (oldest first)
        query = query.order_by(Bill.due_date.asc())
        
        # Paginate
        bills = query.paginate(page=page, per_page=per_page, error_out=False)
        
        # Safely serialize bills with error handling
        bills_list = []
        for bill in bills.items:
            try:
                bill_dict = bill.to_dict(include_tenant=True, include_unit=True, include_payments=True)
                bills_list.append(bill_dict)
            except Exception as bill_error:
                current_app.logger.warning(f"Error serializing bill {bill.id}: {str(bill_error)}")
                # Include minimal bill data if serialization fails
                try:
                    bills_list.append({
                        'id': bill.id,
                        'bill_number': bill.bill_number,
                        'tenant_id': bill.tenant_id,
                        'unit_id': bill.unit_id,
                        'bill_type': bill.bill_type.value if hasattr(bill.bill_type, 'value') else str(bill.bill_type),
                        'title': bill.title,
                        'amount': float(bill.amount),
                        'amount_paid': float(bill.amount_paid),
                        'amount_due': float(bill.amount_due),
                        'due_date': bill.due_date.isoformat() if bill.due_date else None,
                        'status': bill.status.value if hasattr(bill.status, 'value') else str(bill.status),
                        'created_at': bill.created_at.isoformat() if bill.created_at else None
                    })
                except Exception:
                    # Skip this bill if even minimal serialization fails
                    continue
        
        return jsonify({
            'bills': bills_list,
            'total': bills.total,
            'pages': bills.pages,
            'current_page': page,
            'per_page': per_page,
            'has_next': bills.has_next,
            'has_prev': bills.has_prev
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Get bills error: {str(e)}", exc_info=True)
        # Return empty list instead of error to prevent UI crash
        return jsonify({
            'bills': [],
            'total': 0,
            'pages': 0,
            'current_page': 1,
            'per_page': per_page,
            'has_next': False,
            'has_prev': False,
            'error': 'Failed to retrieve bills'
        }), 200

@billing_bp.route('/bills', methods=['POST'])
@require_role(['MANAGER'])
def create_bill(current_user):
    """
    Create bill
    ---
    tags:
      - Billing
    summary: Create a new bill
    description: Create a new bill. Property Manager only.
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - tenant_id
            - amount
            - due_date
            - bill_type
          properties:
            tenant_id:
              type: integer
            amount:
              type: number
            due_date:
              type: string
              format: date
            bill_type:
              type: string
            description:
              type: string
    responses:
      201:
        description: Bill created successfully
        schema:
          type: object
          properties:
            message:
              type: string
            bill:
              type: object
      400:
        description: Validation error
      401:
        description: Unauthorized
      403:
        description: Forbidden - Property Manager access required
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Get property_id from request (subdomain, header, query param, body, or JWT)
        property_id = get_property_id_from_request(data=data)
        
        # If property_id not in request, try to get from JWT token
        if not property_id:
            from flask_jwt_extended import get_jwt
            try:
                claims = get_jwt()
                property_id = claims.get('property_id')
            except Exception:
                pass
        
        # CRITICAL: Do NOT auto-detect from owned properties
        # Property managers must access through the correct subdomain
        if not property_id:
            return jsonify({
                'error': 'Property context is required. Please access through a property subdomain.',
                'code': 'PROPERTY_CONTEXT_REQUIRED'
            }), 400
        
        # Validate required fields
        required_fields = ['tenant_id', 'bill_type', 'title', 'amount', 'due_date']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Verify tenant exists
        tenant = Tenant.query.get(data['tenant_id'])
        if not tenant:
            return jsonify({'error': 'Tenant not found'}), 404
        
        # Get unit_id from tenant's active tenant_units relationship
        # If unit_id is provided, use it; otherwise, get from tenant's current unit
        unit_id = data.get('unit_id')
        if not unit_id:
            # Try to get unit from tenant's active tenant_units
            from models.tenant import TenantUnit
            from datetime import date
            from sqlalchemy import text
            
            # Check for active tenant_unit using raw SQL for flexibility
            # Simplified structure: only check by dates (move_in_date and move_out_date)
            active_tenant_unit = db.session.execute(text(
                """
                SELECT tu.unit_id FROM tenant_units tu
                INNER JOIN units u ON tu.unit_id = u.id
                WHERE tu.tenant_id = :tenant_id 
                  AND u.property_id = :property_id
                  AND tu.move_in_date IS NOT NULL 
                  AND tu.move_out_date IS NOT NULL 
                  AND tu.move_out_date >= CURDATE()
                LIMIT 1
                """
            ), {
                'tenant_id': tenant.id,
                'property_id': property_id
            }).first()
            
            if active_tenant_unit:
                unit_id = active_tenant_unit[0]
            else:
                return jsonify({'error': 'Tenant does not have an active unit in this property. Please assign a unit first.'}), 400
        
        # Verify unit belongs to property using raw SQL to avoid enum validation issues
        from sqlalchemy import text
        unit_check = db.session.execute(text(
            """
            SELECT id, property_id FROM units 
            WHERE id = :unit_id AND property_id = :property_id
            LIMIT 1
            """
        ), {'unit_id': unit_id, 'property_id': property_id}).first()
        
        if not unit_check:
            return jsonify({'error': 'Unit does not belong to this property'}), 400
        
        # Generate bill number
        bill_number = data.get('bill_number')
        if not bill_number:
            # Generate unique bill number: BILL-YYYY-MMDD-XXX
            today = date.today()
            last_bill = Bill.query.order_by(Bill.id.desc()).first()
            sequence = (last_bill.id + 1) if last_bill else 1
            bill_number = f"BILL-{today.strftime('%Y-%m%d')}-{sequence:03d}"
        
        # Create bill
        # Validate bill_type - accept string values directly (matches database enum)
        bill_type_str = str(data['bill_type']).lower().strip()
        valid_bill_types = ['rent', 'utilities', 'maintenance', 'parking', 'other']
        if bill_type_str not in valid_bill_types:
            return jsonify({'error': f'Invalid bill type: {data["bill_type"]}. Must be one of: {", ".join(valid_bill_types)}'}), 400
        bill_type = bill_type_str  # Use string directly
        
        # Parse due_date - handle both ISO format and date string
        due_date_str = data['due_date']
        if 'T' in due_date_str:
            due_date_str = due_date_str.split('T')[0]
        due_date_obj = datetime.strptime(due_date_str, '%Y-%m-%d').date()
        
        # Parse optional period dates
        period_start_obj = None
        if data.get('period_start'):
            period_start_str = data['period_start']
            if 'T' in period_start_str:
                period_start_str = period_start_str.split('T')[0]
            period_start_obj = datetime.strptime(period_start_str, '%Y-%m-%d').date()
        
        period_end_obj = None
        if data.get('period_end'):
            period_end_str = data['period_end']
            if 'T' in period_end_str:
                period_end_str = period_end_str.split('T')[0]
            period_end_obj = datetime.strptime(period_end_str, '%Y-%m-%d').date()
        
        # Set bill_date explicitly (defaults to today)
        bill_date_obj = date.today()
        if data.get('bill_date'):
            bill_date_str = data['bill_date']
            if 'T' in bill_date_str:
                bill_date_str = bill_date_str.split('T')[0]
            bill_date_obj = datetime.strptime(bill_date_str, '%Y-%m-%d').date()
        
        bill = Bill(
            bill_number=bill_number,
            tenant_id=data['tenant_id'],
            unit_id=unit_id,
            bill_type=bill_type,
            title=data['title'],
            amount=Decimal(str(data['amount'])),
            due_date=due_date_obj,
            bill_date=bill_date_obj,  # Set explicitly
            description=data.get('description'),
            period_start=period_start_obj,
            period_end=period_end_obj,
            is_recurring=data.get('is_recurring', False),
            recurring_frequency=data.get('recurring_frequency'),
            notes=data.get('notes')
        )
        
        db.session.add(bill)
        try:
            db.session.commit()
        except Exception as commit_error:
            db.session.rollback()
            current_app.logger.error(f"Database commit error: {str(commit_error)}", exc_info=True)
            raise  # Re-raise to be caught by outer exception handler
        
        # Create notification for tenant
        try:
            from services.notification_service import NotificationService
            NotificationService.notify_bill_created(bill)
        except Exception as notif_error:
            # Don't fail the bill creation if notification fails
            current_app.logger.warning(f"Failed to create notification for bill {bill.id}: {str(notif_error)}")
        
        return jsonify({
            'message': 'Bill created successfully',
            'bill': bill.to_dict(include_tenant=True, include_unit=True)
        }), 201
        
    except ValueError as e:
        db.session.rollback()
        current_app.logger.error(f"Create bill ValueError: {str(e)}", exc_info=True)
        error_msg = f'Invalid date format: {str(e)}'
        if current_app.config.get('DEBUG', False):
            error_msg += f' | Full error: {str(e)}'
        return jsonify({'error': error_msg}), 400
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Create bill error: {str(e)}", exc_info=True)
        error_msg = 'Failed to create bill'
        if current_app.config.get('DEBUG', False):
            error_msg += f': {str(e)}'
        return jsonify({'error': error_msg, 'details': str(e) if current_app.config.get('DEBUG', False) else None}), 500

@billing_bp.route('/bills/<int:bill_id>', methods=['PUT'])
@require_role(['MANAGER'])
def update_bill(current_user, bill_id):
    """
    Update bill
    ---
    tags:
      - Billing
    summary: Update a bill
    description: Update bill information. Property Manager only.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: bill_id
        type: integer
        required: true
        description: The bill ID
      - in: body
        name: body
        schema:
          type: object
          properties:
            amount:
              type: number
            due_date:
              type: string
              format: date
            bill_type:
              type: string
            description:
              type: string
            status:
              type: string
    responses:
      200:
        description: Bill updated successfully
        schema:
          type: object
          properties:
            message:
              type: string
            bill:
              type: object
      400:
        description: Validation error
      401:
        description: Unauthorized
      403:
        description: Forbidden - Property Manager access required
      404:
        description: Bill not found
      500:
        description: Server error
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Get bill
        bill = Bill.query.get(bill_id)
        if not bill:
            return jsonify({'error': 'Bill not found'}), 404
        
        # Get property context
        property_id = get_property_id_from_request(data=data)
        
        # If property_id not in request, try to get from JWT token
        if not property_id:
            from flask_jwt_extended import get_jwt
            try:
                claims = get_jwt()
                property_id = claims.get('property_id')
            except Exception:
                pass
        
        # CRITICAL: Do NOT auto-detect from owned properties
        # Property managers must access through the correct subdomain
        if not property_id:
            return jsonify({
                'error': 'Property context is required. Please access through a property subdomain.',
                'code': 'PROPERTY_CONTEXT_REQUIRED'
            }), 400
        
        # CRITICAL: Verify property exists and user owns it
        property_obj = Property.query.get(property_id)
        if not property_obj:
            return jsonify({'error': 'Property not found'}), 404
        
        if property_obj.owner_id != current_user.id and not is_super_admin(current_user.id):
            return jsonify({
                'error': 'Access denied. You do not own this property.',
                'code': 'PROPERTY_ACCESS_DENIED'
            }), 403
        
        # Verify bill belongs to this property using raw SQL to avoid enum validation issues
        unit_check = db.session.execute(text(
            """
            SELECT id, property_id FROM units 
            WHERE id = :unit_id AND property_id = :property_id
            LIMIT 1
            """
        ), {'unit_id': bill.unit_id, 'property_id': property_id}).first()
        
        if not unit_check:
            return jsonify({'error': 'Bill does not belong to this property'}), 403
        
        # Update fields if provided
        if 'tenant_id' in data:
            tenant = Tenant.query.get(data['tenant_id'])
            if not tenant:
                return jsonify({'error': 'Tenant not found'}), 404
            bill.tenant_id = data['tenant_id']
        
        if 'unit_id' in data:
            unit_id = data['unit_id']
            # Verify unit belongs to property using raw SQL to avoid enum validation issues
            unit_check = db.session.execute(text(
                """
                SELECT id, property_id FROM units 
                WHERE id = :unit_id AND property_id = :property_id
                LIMIT 1
                """
            ), {'unit_id': unit_id, 'property_id': property_id}).first()
            
            if not unit_check:
                return jsonify({'error': 'Unit does not belong to this property'}), 400
            bill.unit_id = unit_id
        
        if 'bill_type' in data:
            bill_type_str = str(data['bill_type']).lower().strip()
            valid_bill_types = ['rent', 'utilities', 'maintenance', 'parking', 'other']
            if bill_type_str not in valid_bill_types:
                return jsonify({'error': f'Invalid bill type: {data["bill_type"]}'}), 400
            bill.bill_type = bill_type_str
        
        if 'title' in data:
            bill.title = data['title'].strip()
        
        if 'amount' in data:
            bill.amount = Decimal(str(data['amount']))
            # amount_due is a computed property, so it will be recalculated automatically
            # No need to set it directly - it's calculated as amount - amount_paid
        
        if 'due_date' in data:
            due_date_str = data['due_date']
            if 'T' in due_date_str:
                due_date_str = due_date_str.split('T')[0]
            bill.due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
        
        if 'description' in data:
            bill.description = data.get('description')
        
        if 'is_recurring' in data:
            bill.is_recurring = data.get('is_recurring', False)
        
        if 'recurring_frequency' in data:
            bill.recurring_frequency = data.get('recurring_frequency')
        
        if 'notes' in data:
            bill.notes = data.get('notes')
        
        # Update status if provided (but be careful - don't override if bill has payments)
        if 'status' in data and not bill.payments:
            status_str = str(data['status']).lower().strip()
            valid_statuses = ['pending', 'paid', 'partial', 'overdue', 'cancelled']
            if status_str in valid_statuses:
                bill.status = status_str
        
        db.session.commit()
        
        return jsonify({
            'message': 'Bill updated successfully',
            'bill': bill.to_dict(include_tenant=True, include_unit=True, include_payments=True)
        }), 200
        
    except ValueError as e:
        db.session.rollback()
        current_app.logger.error(f"Update bill ValueError: {str(e)}", exc_info=True)
        return jsonify({'error': f'Invalid data format: {str(e)}'}), 400
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update bill error: {str(e)}", exc_info=True)
        error_msg = 'Failed to update bill'
        if current_app.config.get('DEBUG', False):
            error_msg += f': {str(e)}'
        return jsonify({'error': error_msg, 'details': str(e) if current_app.config.get('DEBUG', False) else None}), 500

@billing_bp.route('/bills/<int:bill_id>', methods=['DELETE'])
@require_role(['MANAGER'])
def delete_bill(current_user, bill_id):
    """
    Delete bill
    ---
    tags:
      - Billing
    summary: Delete a bill
    description: Delete a bill. Property Manager only.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: bill_id
        type: integer
        required: true
        description: The bill ID
    responses:
      200:
        description: Bill deleted successfully
        schema:
          type: object
          properties:
            message:
              type: string
      401:
        description: Unauthorized
      403:
        description: Forbidden - Property Manager access required
      404:
        description: Bill not found
      500:
        description: Server error
    """
    try:
        # Get bill
        bill = Bill.query.get(bill_id)
        if not bill:
            return jsonify({'error': 'Bill not found'}), 404
        
        # Get property context
        property_id = get_property_id_from_request()
        
        # If still no property_id, try to get from user's managed properties
        if not property_id:
            if isinstance(current_user.role, UserRole):
                user_role = current_user.role.value
            else:
                user_role = str(current_user.role).upper()
            
            if user_role in ['MANAGER', 'PROPERTY_MANAGER', 'ADMIN']:
                try:
                    managed_property = Property.query.filter_by(
                        manager_id=current_user.id
                    ).first()
                    if managed_property:
                        property_id = managed_property.id
                except Exception as e:
                    current_app.logger.warning(f"Error getting managed property: {str(e)}")
        
        if not property_id:
            return jsonify({'error': 'Property context is required'}), 400
        
        # Verify bill belongs to this property
        # Verify bill belongs to property using raw SQL to avoid enum validation issues
        unit_check = db.session.execute(text(
            """
            SELECT id, property_id FROM units 
            WHERE id = :unit_id AND property_id = :property_id
            LIMIT 1
            """
        ), {'unit_id': bill.unit_id, 'property_id': property_id}).first()
        
        if not unit_check:
            return jsonify({'error': 'Bill does not belong to this property'}), 403
        
        # Check if bill has payments - if so, warn or prevent deletion
        if bill.payments and len(bill.payments) > 0:
            # Check if any payments are approved or completed
            has_approved_payments = any(
                payment.status in ['approved', 'completed'] 
                for payment in bill.payments
            )
            if has_approved_payments:
                return jsonify({
                    'error': 'Cannot delete bill with approved or completed payments. Please cancel the bill instead.'
                }), 400
        
        # Delete bill (cascade will handle payments if configured)
        db.session.delete(bill)
        db.session.commit()
        
        return jsonify({
            'message': 'Bill deleted successfully'
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete bill error: {str(e)}", exc_info=True)
        error_msg = 'Failed to delete bill'
        if current_app.config.get('DEBUG', False):
            error_msg += f': {str(e)}'
        return jsonify({'error': error_msg, 'details': str(e) if current_app.config.get('DEBUG', False) else None}), 500

# =====================================================
# PAYMENT PROOF SUBMISSION (Tenant)
# =====================================================

@billing_bp.route('/bills/<int:bill_id>/submit-payment', methods=['POST'])
@jwt_required()
def submit_payment_proof(bill_id):
    """
    Submit payment proof
    ---
    tags:
      - Billing
    summary: Submit proof of payment for a bill
    description: Submit proof of payment for a bill. Tenant only.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: bill_id
        type: integer
        required: true
        description: The bill ID
      - in: formData
        name: proof_file
        type: file
        required: true
        description: Payment proof file (image or PDF)
      - in: formData
        name: notes
        type: string
        description: Additional notes
    responses:
      200:
        description: Payment proof submitted successfully
        schema:
          type: object
          properties:
            message:
              type: string
            payment:
              type: object
      400:
        description: Validation error
      401:
        description: Unauthorized
      403:
        description: Forbidden - Tenant access required
      404:
        description: Bill not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Verify user is a tenant
        # Handle both string and enum values for role (database stores as string)
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        if user_role_str != 'TENANT':
            return jsonify({'error': 'Only tenants can submit payment proofs'}), 403
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Get bill
        bill = Bill.query.get(bill_id)
        if not bill:
            return jsonify({'error': 'Bill not found'}), 404
        
        # Verify tenant owns this bill
        tenant = Tenant.query.filter_by(user_id=current_user.id).first()
        if not tenant or bill.tenant_id != tenant.id:
            return jsonify({'error': 'You can only submit payment for your own bills'}), 403
        
        # Validate required fields
        if 'proof_of_payment' not in data:
            return jsonify({'error': 'Proof of payment is required'}), 400
        if 'payment_method' not in data:
            return jsonify({'error': 'Payment method is required'}), 400
        if 'amount' not in data:
            return jsonify({'error': 'Payment amount is required'}), 400
        
        # Validate payment method (accept string values, convert to lowercase)
        payment_method_str = str(data['payment_method']).lower().strip()
        valid_payment_methods = ['cash', 'gcash', 'check', 'bank_transfer', 'credit_card', 'debit_card', 'online', 'mobile']
        if payment_method_str not in valid_payment_methods:
            return jsonify({'error': f'Invalid payment method: {data["payment_method"]}'}), 400
        
        # Validate amount
        payment_amount = Decimal(str(data['amount']))
        if payment_amount <= 0:
            return jsonify({'error': 'Payment amount must be greater than 0'}), 400
        
        # Calculate amount due from bill
        bill_amount_due = bill.amount_due
        if payment_amount > bill_amount_due:
            return jsonify({'error': f'Payment amount cannot exceed amount due (₱{bill_amount_due})'}), 400
        
        # Create payment record with pending_approval status
        # Use string values for payment_method and status to match database
        payment = Payment(
            bill_id=bill_id,
            amount=payment_amount,
            payment_method=payment_method_str,  # Use string value
            status='pending_approval',  # Use string literal - Requires manager approval
            payment_date=date.today(),
            reference_number=data.get('reference_number'),  # GCash reference, transaction ID, etc.
            proof_of_payment=data['proof_of_payment'],  # URL or base64 image
            remarks=data.get('remarks', '')  # Tenant's remarks
        )
        
        db.session.add(payment)
        
        # Update bill status to partial if partial payment, or keep as pending
        # Don't mark as paid until manager approves
        try:
            db.session.commit()
        except Exception as commit_error:
            db.session.rollback()
            current_app.logger.error(f"Database commit error: {str(commit_error)}", exc_info=True)
            if current_app.config.get('DEBUG', False):
                return jsonify({
                    'error': 'Failed to submit payment proof',
                    'details': str(commit_error),
                    'type': type(commit_error).__name__
                }), 500
            return jsonify({'error': 'Failed to submit payment proof. Please try again.'}), 500
        
        # Create notification for property manager
        try:
            from services.notification_service import NotificationService
            NotificationService.notify_pm_payment_submitted(payment)
        except Exception as notif_error:
            # Don't fail payment submission if notification fails
            current_app.logger.warning(f"Failed to create PM notification for payment {payment.id}: {str(notif_error)}")
        
        return jsonify({
            'message': 'Payment proof submitted successfully. Waiting for manager approval.',
            'payment': payment.to_dict(include_bill=True)
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Submit payment proof error: {str(e)}", exc_info=True)
        if current_app.config.get('DEBUG', False):
            return jsonify({
                'error': 'Failed to submit payment proof',
                'details': str(e),
                'type': type(e).__name__
            }), 500
        return jsonify({'error': 'Failed to submit payment proof'}), 500

# =====================================================
# PAYMENT APPROVAL (Property Manager)
# =====================================================

@billing_bp.route('/payments/<int:payment_id>/approve', methods=['POST'])
@require_role(['MANAGER'])
def approve_payment(current_user, payment_id):
    """
    Approve payment
    ---
    tags:
      - Billing
    summary: Approve a payment proof
    description: Approve a payment proof. Property Manager only.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: payment_id
        type: integer
        required: true
        description: The payment ID
    responses:
      200:
        description: Payment approved successfully
        schema:
          type: object
          properties:
            message:
              type: string
            payment:
              type: object
      401:
        description: Unauthorized
      403:
        description: Forbidden - Property Manager access required
      404:
        description: Payment not found
      500:
        description: Server error
    """
    try:
        payment = Payment.query.get(payment_id)
        if not payment:
            return jsonify({'error': 'Payment not found'}), 404
        
        # Get property context
        property_id = get_property_id_from_request()
        
        # If still no property_id, try to get from user's managed properties
        if not property_id:
            if isinstance(current_user.role, UserRole):
                user_role = current_user.role.value
            else:
                user_role = str(current_user.role).upper()
            
            if user_role in ['MANAGER', 'PROPERTY_MANAGER', 'ADMIN']:
                try:
                    managed_property = Property.query.filter_by(
                        manager_id=current_user.id
                    ).first()
                    if managed_property:
                        property_id = managed_property.id
                except Exception as e:
                    current_app.logger.warning(f"Error getting managed property: {str(e)}")
        
        if not property_id:
            return jsonify({'error': 'Property context is required'}), 400
        
        # Verify bill belongs to this property
        bill = payment.bill
        if not bill:
            return jsonify({'error': 'Bill not found'}), 404
        
        # Verify payment belongs to property using raw SQL to avoid enum validation issues
        unit_check = db.session.execute(text(
            """
            SELECT id, property_id FROM units 
            WHERE id = :unit_id AND property_id = :property_id
            LIMIT 1
            """
        ), {'unit_id': bill.unit_id, 'property_id': property_id}).first()
        
        if not unit_check:
            return jsonify({'error': 'Payment does not belong to this property'}), 403
        
        # Update payment status
        payment.status = 'approved'  # Use string literal
        payment.verified_by = current_user.id
        payment.verified_at = datetime.now()
        payment.processed_by = current_user.id
        
        db.session.commit()
        
        # Check if bill is now fully paid - if so, mark payment as completed
        # Calculate total paid from all approved/completed payments
        from sqlalchemy import func
        total_paid = db.session.query(func.sum(Payment.amount)).filter(
            Payment.bill_id == bill.id,
            Payment.status.in_(['completed', 'approved'])  # Use string literals
        ).scalar() or Decimal('0.00')
        
        # If fully paid, mark payment as completed
        if total_paid >= bill.amount:
            payment.status = 'completed'  # Use string literal
        
        # Update bill status
        if total_paid >= bill.amount:
            bill.status = 'paid'  # Use string value
            bill.paid_date = date.today()
            
            # AUTO-RENEWAL: If this is a rent bill and it's fully paid, automatically extend the rental period
            # This extends rent_end_date and move_out_date based on contract type (quarterly/yearly) or default 30 days
            if str(bill.bill_type).lower() == 'rent':
                try:
                    from models.tenant import TenantUnit
                    from models.rental_contract import RentalContract
                    from datetime import timedelta
                    
                    # Get the active tenant_unit for this tenant and unit
                    tenant_unit_result = db.session.execute(text(
                        """
                        SELECT tu.id, tu.rent_start_date, tu.rent_end_date, tu.move_out_date, tu.monthly_rent
                        FROM tenant_units tu
                        WHERE tu.tenant_id = :tenant_id 
                          AND tu.unit_id = :unit_id
                          AND (tu.move_out_date IS NULL OR tu.move_out_date >= CURDATE())
                        ORDER BY tu.created_at DESC
                        LIMIT 1
                        """
                    ), {
                        'tenant_id': bill.tenant_id,
                        'unit_id': bill.unit_id
                    }).first()
                    
                    if tenant_unit_result:
                        tenant_unit_id = tenant_unit_result[0]
                        current_rent_start = tenant_unit_result[1]
                        current_rent_end = tenant_unit_result[2] or tenant_unit_result[3]  # rent_end_date or move_out_date
                        current_move_out = tenant_unit_result[3]  # move_out_date
                        
                        # Determine billing period length (default 30 days = 1 month)
                        from datetime import timedelta
                        period_days = 30
                        if bill.period_start and bill.period_end:
                            try:
                                delta_days = (bill.period_end - bill.period_start).days
                                if delta_days > 0:
                                    period_days = delta_days
                            except Exception:
                                pass
                        
                        # Compute new rent period
                        if current_rent_end:
                            # Next period starts where the last one ended
                            new_rent_start = current_rent_end
                        elif bill.period_end:
                            new_rent_start = bill.period_end
                        elif bill.period_start:
                            new_rent_start = bill.period_start
                        else:
                            new_rent_start = date.today()
                        
                        new_rent_end = new_rent_start + timedelta(days=period_days)
                        
                        # Update tenant_unit with new monthly rent period
                        try:
                            db.session.execute(text(
                                """
                                UPDATE tenant_units 
                                SET rent_start_date = :rent_start_date,
                                    rent_end_date = :rent_end_date,
                                    updated_at = NOW()
                                WHERE id = :tenant_unit_id
                                """
                            ), {
                                'tenant_unit_id': tenant_unit_id,
                                'rent_start_date': new_rent_start,
                                'rent_end_date': new_rent_end
                            })
                        except Exception as update_error:
                            # If rent_start_date/rent_end_date columns don't exist, fall back to extending move_out_date only
                            current_app.logger.warning(f"rent_start_date/rent_end_date columns may not exist, updating move_out_date only: {str(update_error)}")
                            db.session.execute(text(
                                """
                                UPDATE tenant_units 
                                SET move_out_date = :move_out_date,
                                    updated_at = NOW()
                                WHERE id = :tenant_unit_id
                                """
                            ), {
                                'tenant_unit_id': tenant_unit_id,
                                'move_out_date': new_rent_end
                            })
                        
                        current_app.logger.debug(
                            f"Auto-updated monthly rent period for tenant {bill.tenant_id}, unit {bill.unit_id} "
                            f"to {new_rent_start} - {new_rent_end} (period_days={period_days})"
                        )
                except Exception as renewal_error:
                    # Don't fail payment approval if renewal fails
                    current_app.logger.warning(f"Failed to auto-renew rental after rent payment: {str(renewal_error)}")
        elif total_paid > 0:
            bill.status = 'partial'  # Use string value
        else:
            bill.status = 'pending'  # Use string value
        
        db.session.commit()
        
        # Create notification for tenant
        try:
            from services.notification_service import NotificationService
            NotificationService.notify_payment_approved(payment)
        except Exception as notif_error:
            # Don't fail the approval if notification fails
            current_app.logger.warning(f"Failed to create notification for payment {payment.id}: {str(notif_error)}")
        
        return jsonify({
            'message': 'Payment approved successfully',
            'payment': payment.to_dict(include_bill=True),
            'bill': bill.to_dict(include_tenant=True, include_unit=True)
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Approve payment error: {str(e)}", exc_info=True)
        if current_app.config.get('DEBUG', False):
            return jsonify({
                'error': 'Failed to approve payment',
                'details': str(e),
                'type': type(e).__name__
            }), 500
        return jsonify({'error': 'Failed to approve payment'}), 500

@billing_bp.route('/payments/<int:payment_id>/reject', methods=['POST'])
@require_role(['MANAGER'])
def reject_payment(current_user, payment_id):
    """
    Reject payment
    ---
    tags:
      - Billing
    summary: Reject a payment proof
    description: Reject a payment proof. Property Manager only.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: payment_id
        type: integer
        required: true
        description: The payment ID
      - in: body
        name: body
        schema:
          type: object
          properties:
            reason:
              type: string
              description: Reason for rejection
    responses:
      200:
        description: Payment rejected successfully
        schema:
          type: object
          properties:
            message:
              type: string
            payment:
              type: object
      401:
        description: Unauthorized
      403:
        description: Forbidden - Property Manager access required
      404:
        description: Payment not found
      500:
        description: Server error
    """
    try:
        data = request.get_json() or {}
        payment = Payment.query.get(payment_id)
        if not payment:
            return jsonify({'error': 'Payment not found'}), 404
        
        # Get property context
        property_id = get_property_id_from_request()
        
        # If still no property_id, try to get from user's managed properties
        if not property_id:
            if isinstance(current_user.role, UserRole):
                user_role = current_user.role.value
            else:
                user_role = str(current_user.role).upper()
            
            if user_role in ['MANAGER', 'PROPERTY_MANAGER', 'ADMIN']:
                try:
                    managed_property = Property.query.filter_by(
                        manager_id=current_user.id
                    ).first()
                    if managed_property:
                        property_id = managed_property.id
                except Exception as e:
                    current_app.logger.warning(f"Error getting managed property: {str(e)}")
        
        if not property_id:
            return jsonify({'error': 'Property context is required'}), 400
        
        # Verify bill belongs to this property
        bill = payment.bill
        if not bill:
            return jsonify({'error': 'Bill not found'}), 404
        
        # Verify payment belongs to property using raw SQL to avoid enum validation issues
        unit_check = db.session.execute(text(
            """
            SELECT id, property_id FROM units 
            WHERE id = :unit_id AND property_id = :property_id
            LIMIT 1
            """
        ), {'unit_id': bill.unit_id, 'property_id': property_id}).first()
        
        if not unit_check:
            return jsonify({'error': 'Payment does not belong to this property'}), 403
        
        # Update payment status
        payment.status = 'rejected'  # Use string literal
        payment.verified_by = current_user.id
        payment.verified_at = datetime.now()
        payment.processed_by = current_user.id
        if data.get('rejection_reason'):
            payment.notes = f"{payment.notes or ''}\nRejection reason: {data['rejection_reason']}".strip()
        
        db.session.commit()
        
        # Create notification for tenant
        try:
            from services.notification_service import NotificationService
            NotificationService.notify_payment_rejected(payment, reason=data.get('rejection_reason'))
        except Exception as notif_error:
            # Don't fail the rejection if notification fails
            current_app.logger.warning(f"Failed to create notification for payment {payment.id}: {str(notif_error)}")
        
        return jsonify({
            'message': 'Payment rejected',
            'payment': payment.to_dict(include_bill=True)
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Reject payment error: {str(e)}", exc_info=True)
        return jsonify({'error': 'Failed to reject payment'}), 500

# =====================================================
# PAYMENT LISTING (Property Manager & Tenant)
# =====================================================

@billing_bp.route('/payments', methods=['GET'])
@jwt_required()
def get_payments():
    """
    Get payments
    ---
    tags:
      - Billing
    summary: Get payments for bills
    description: Retrieve payments filtered by user's role
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
        name: bill_id
        type: integer
    responses:
      200:
        description: Payments retrieved successfully
        schema:
          type: object
          properties:
            payments:
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
        
        # Get property_id from request (subdomain, header, query param, or JWT)
        property_id = get_property_id_from_request()
        
        # If still no property_id, try to get from user's managed properties (for property managers)
        if not property_id:
            if isinstance(current_user.role, UserRole):
                user_role = current_user.role.value
            else:
                user_role = str(current_user.role).upper()
            
            if user_role in ['MANAGER', 'PROPERTY_MANAGER', 'ADMIN']:
                try:
                    # Get the first managed property
                    managed_property = Property.query.filter_by(
                        manager_id=current_user.id
                    ).first()
                    if managed_property:
                        property_id = managed_property.id
                except Exception as e:
                    current_app.logger.warning(f"Error getting managed property: {str(e)}")
        
        if not property_id:
            return jsonify({'error': 'Property context is required'}), 400
        
        # Get query parameters
        bill_id = request.args.get('bill_id', type=int)
        status = request.args.get('status')
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        
        # Base query - filter by property through bills and units
        query = Payment.query.join(Bill, Payment.bill_id == Bill.id).join(Unit, Bill.unit_id == Unit.id).filter(Unit.property_id == property_id)
        
        # If tenant, only show their payments
        if current_user.role == UserRole.TENANT:
            tenant = Tenant.query.filter_by(user_id=current_user.id).first()
            if tenant:
                query = query.filter(Bill.tenant_id == tenant.id)
        
        # Apply filters
        if bill_id:
            query = query.filter(Payment.bill_id == bill_id)
        if status:
            try:
                status_enum = PaymentStatus(status)
                query = query.filter(Payment.status == status_enum)
            except ValueError:
                return jsonify({'error': f'Invalid status: {status}'}), 400
        
        # Order by creation date (newest first)
        query = query.order_by(Payment.created_at.desc())
        
        # Paginate
        payments = query.paginate(page=page, per_page=per_page, error_out=False)
        
        return jsonify({
            'payments': [payment.to_dict(include_bill=True) for payment in payments.items],
            'total': payments.total,
            'pages': payments.pages,
            'current_page': page,
            'per_page': per_page,
            'has_next': payments.has_next,
            'has_prev': payments.has_prev
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Get payments error: {str(e)}", exc_info=True)
        return jsonify({'error': 'Failed to retrieve payments'}), 500

@billing_bp.route('/payments/<int:payment_id>', methods=['GET'])
@jwt_required()
def get_payment(payment_id):
    """
    Get payment by ID
    ---
    tags:
      - Billing
    summary: Get a specific payment
    description: Retrieve a specific payment by ID
    security:
      - Bearer: []
    parameters:
      - in: path
        name: payment_id
        type: integer
        required: true
        description: The payment ID
    responses:
      200:
        description: Payment retrieved successfully
        schema:
          type: object
          properties:
            payment:
              type: object
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Payment not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        payment = Payment.query.get(payment_id)
        if not payment:
            return jsonify({'error': 'Payment not found'}), 404
        
        # If tenant, verify they own this payment
        if current_user.role == UserRole.TENANT:
            tenant = Tenant.query.filter_by(user_id=current_user.id).first()
            if not tenant or payment.bill.tenant_id != tenant.id:
                return jsonify({'error': 'Payment not found'}), 404
        
        return jsonify({
            'payment': payment.to_dict(include_bill=True)
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Get payment error: {str(e)}", exc_info=True)
        return jsonify({'error': 'Failed to retrieve payment'}), 500

# =====================================================
# BILLS DASHBOARD (Statistics)
# =====================================================

@billing_bp.route('/dashboard', methods=['GET'])
@jwt_required()
def get_bills_dashboard():
    """
    Get bills dashboard
    ---
    tags:
      - Billing
    summary: Get bills dashboard statistics
    description: Retrieve billing dashboard statistics filtered by user's role
    security:
      - Bearer: []
    responses:
      200:
        description: Dashboard statistics retrieved successfully
        schema:
          type: object
          properties:
            total_bills:
              type: integer
            paid_bills:
              type: integer
            pending_bills:
              type: integer
            overdue_bills:
              type: integer
            total_amount:
              type: number
            paid_amount:
              type: number
            pending_amount:
              type: number
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Get property_id from request (subdomain, header, query param, or JWT)
        property_id = get_property_id_from_request()
        
        # If still no property_id, try to get from user's managed properties (for property managers)
        if not property_id:
            if isinstance(current_user.role, UserRole):
                user_role = current_user.role.value
            else:
                user_role = str(current_user.role).upper()
            
            if user_role in ['MANAGER', 'PROPERTY_MANAGER', 'ADMIN']:
                try:
                    # Get the first managed property
                    managed_property = Property.query.filter_by(
                        manager_id=current_user.id
                    ).first()
                    if managed_property:
                        property_id = managed_property.id
                except Exception as e:
                    current_app.logger.warning(f"Error getting managed property: {str(e)}")
        
        if not property_id:
            return jsonify({'error': 'Property context is required'}), 400
        
        # Get all bills for this property
        bills = Bill.query.join(Unit).filter(Unit.property_id == property_id).all()
        
        # Calculate statistics
        total_bills = len(bills)
        total_revenue = sum(float(bill.amount_paid) for bill in bills)
        pending_payments = sum(float(bill.amount_due) for bill in bills if str(bill.status).lower() == 'pending')
        overdue_amount = sum(float(bill.amount_due) for bill in bills if bill.status == BillStatus.OVERDUE)
        
        # Count bills by status
        paid_bills = sum(1 for bill in bills if bill.status == BillStatus.PAID)
        pending_bills = sum(1 for bill in bills if bill.status == BillStatus.PENDING)
        overdue_bills = sum(1 for bill in bills if bill.status == BillStatus.OVERDUE)
        partial_bills = sum(1 for bill in bills if bill.status == BillStatus.PARTIAL)
        
        return jsonify({
            'total_revenue': total_revenue,
            'pending_payments': pending_payments,
            'overdue_amount': overdue_amount,
            'total_bills': total_bills,
            'paid_bills': paid_bills,
            'pending_bills': pending_bills,
            'overdue_bills': overdue_bills,
            'partial_bills': partial_bills
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Get bills dashboard error: {str(e)}", exc_info=True)
        return jsonify({
            'total_revenue': 0,
            'pending_payments': 0,
            'overdue_amount': 0,
            'total_bills': 0,
            'paid_bills': 0,
            'pending_bills': 0,
            'overdue_bills': 0,
            'partial_bills': 0,
            'error': 'Failed to retrieve dashboard data'
        }), 200  # Return 200 with default values to prevent UI crash
