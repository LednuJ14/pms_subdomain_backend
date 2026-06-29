from flask import Blueprint, jsonify, request, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from app import db
from models.user import User, UserRole
from models.tenant import Tenant, TenantUnit
from models.rental_contract import RentalContract, ContractType, ContractStatus
from models.property import Property, Unit
from datetime import datetime, date, timedelta, timezone
from sqlalchemy import text, or_, and_
import os

contract_bp = Blueprint('contracts', __name__)


def get_current_user():
    """Get current authenticated user."""
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
            if isinstance(current_user.role, UserRole):
                user_role = current_user.role.value
            else:
                user_role = str(current_user.role).upper()
            
            allowed_roles_upper = [r.upper() if isinstance(r, str) else r.value.upper() if isinstance(r, UserRole) else str(r).upper() for r in allowed_roles]
            
            if user_role.upper() not in allowed_roles_upper:
                return jsonify({'error': 'Insufficient permissions'}), 403
            
            return f(current_user, *args, **kwargs)
        decorated_function.__name__ = f.__name__
        return decorated_function
    return decorator


# =====================================================
# CONTRACT MANAGEMENT
# =====================================================

@contract_bp.route('/', methods=['POST'])
@require_role([UserRole.PROPERTY_MANAGER])
def create_contract(current_user):
    """
    Create a new rental contract.
    
    Required fields:
    - tenant_unit_id: ID of the tenant-unit relationship
    - contract_type: 'quarterly' or 'yearly'
    - start_date: Contract start date (YYYY-MM-DD)
    - monthly_rent: Monthly rent amount
    
    Optional fields:
    - security_deposit: Security deposit amount
    - end_date: Contract end date (auto-calculated if not provided)
    - terms_and_conditions: Contract terms
    - special_conditions: Special conditions
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Required fields
        tenant_unit_id = data.get('tenant_unit_id')
        contract_type = data.get('contract_type')
        start_date_str = data.get('start_date')
        monthly_rent = data.get('monthly_rent')
        
        if not all([tenant_unit_id, contract_type, start_date_str, monthly_rent]):
            return jsonify({
                'error': 'Missing required fields',
                'required': ['tenant_unit_id', 'contract_type', 'start_date', 'monthly_rent']
            }), 400
        
        # Validate contract type
        if contract_type.lower() not in ['quarterly', 'yearly']:
            return jsonify({'error': 'contract_type must be "quarterly" or "yearly"'}), 400
        
        # Parse start date
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Invalid start_date format. Use YYYY-MM-DD'}), 400
        
        # Get tenant unit
        tenant_unit = TenantUnit.query.get(tenant_unit_id)
        if not tenant_unit:
            return jsonify({'error': 'Tenant unit not found'}), 404
        
        # Get tenant and unit
        tenant = Tenant.query.get(tenant_unit.tenant_id)
        if not tenant:
            return jsonify({'error': 'Tenant not found'}), 404
        
        unit = Unit.query.get(tenant_unit.unit_id)
        if not unit:
            return jsonify({'error': 'Unit not found'}), 404
        
        # Get property_id from unit or tenant
        property_id = unit.property_id if hasattr(unit, 'property_id') else tenant.property_id
        
        # Check if there's already an active contract for this tenant_unit
        existing_contract = RentalContract.query.filter_by(
            tenant_unit_id=tenant_unit_id,
            status='active'
        ).first()
        
        if existing_contract:
            return jsonify({
                'error': 'An active contract already exists for this tenant-unit relationship',
                'existing_contract_id': existing_contract.id
            }), 400
        
        # Create contract
        contract = RentalContract(
            tenant_unit_id=tenant_unit_id,
            tenant_id=tenant.id,
            unit_id=unit.id,
            property_id=property_id,
            contract_type=contract_type.lower(),
            start_date=start_date,
            monthly_rent=monthly_rent,
            security_deposit=data.get('security_deposit'),
            end_date=datetime.strptime(data.get('end_date'), '%Y-%m-%d').date() if data.get('end_date') else None,
            terms_and_conditions=data.get('terms_and_conditions'),
            special_conditions=data.get('special_conditions'),
            status='draft'
        )
        
        db.session.add(contract)
        db.session.commit()
        
        return jsonify({
            'message': 'Contract created successfully',
            'contract': contract.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating contract: {str(e)}")
        return jsonify({'error': f'Failed to create contract: {str(e)}'}), 500


@contract_bp.route('/', methods=['GET'])
@jwt_required()
def get_contracts():
    """
    Get all contracts (filtered by user role).
    - Property Managers/Admins: See all contracts
    - Tenants: See only their own contracts
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Determine user role
        if isinstance(current_user.role, UserRole):
            user_role = current_user.role.value
        else:
            user_role = str(current_user.role).upper()
        
        # Build query
        query = RentalContract.query
        
        # Filter by role
        if user_role == 'TENANT':
            # Tenants can only see their own contracts
            tenant = Tenant.query.filter_by(user_id=current_user.id).first()
            if not tenant:
                return jsonify({'error': 'Tenant profile not found'}), 404
            query = query.filter_by(tenant_id=tenant.id)
        elif user_role in ['PROPERTY_MANAGER', 'MANAGER']:
            # Property managers can see all contracts
            # Optionally filter by property_id if provided
            property_id = request.args.get('property_id', type=int)
            if property_id:
                query = query.filter_by(property_id=property_id)
        
        # Additional filters
        status = request.args.get('status')
        if status:
            query = query.filter_by(status=status)
        
        contract_type = request.args.get('contract_type')
        if contract_type:
            query = query.filter_by(contract_type=contract_type.lower())
        
        tenant_id = request.args.get('tenant_id', type=int)
        if tenant_id:
            query = query.filter_by(tenant_id=tenant_id)
        
        unit_id = request.args.get('unit_id', type=int)
        if unit_id:
            query = query.filter_by(unit_id=unit_id)
        
        # Get expired contracts
        show_expired = request.args.get('show_expired', 'false').lower() == 'true'
        if not show_expired:
            # Only show non-expired contracts
            today = date.today()
            query = query.filter(RentalContract.end_date >= today)
        
        # Order by creation date (newest first)
        query = query.order_by(RentalContract.created_at.desc())
        
        # Pagination
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        per_page = min(per_page, 100)  # Limit to 100 per page
        
        contracts = query.paginate(page=page, per_page=per_page, error_out=False)
        
        return jsonify({
            'contracts': [contract.to_dict(include_tenant=True, include_unit=True) for contract in contracts.items],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': contracts.total,
                'pages': contracts.pages
            }
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error getting contracts: {str(e)}")
        return jsonify({'error': f'Failed to get contracts: {str(e)}'}), 500


@contract_bp.route('/<int:contract_id>', methods=['GET'])
@jwt_required()
def get_contract(contract_id):
    """Get a specific contract by ID."""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        contract = RentalContract.query.get(contract_id)
        if not contract:
            return jsonify({'error': 'Contract not found'}), 404
        
        # Check permissions
        if isinstance(current_user.role, UserRole):
            user_role = current_user.role.value
        else:
            user_role = str(current_user.role).upper()
        
        if user_role == 'TENANT':
            # Tenants can only see their own contracts
            tenant = Tenant.query.filter_by(user_id=current_user.id).first()
            if not tenant or contract.tenant_id != tenant.id:
                return jsonify({'error': 'Access denied'}), 403
        
        return jsonify(contract.to_dict(include_tenant=True, include_unit=True)), 200
        
    except Exception as e:
        current_app.logger.error(f"Error getting contract: {str(e)}")
        return jsonify({'error': f'Failed to get contract: {str(e)}'}), 500


@contract_bp.route('/<int:contract_id>', methods=['PUT'])
@require_role([UserRole.PROPERTY_MANAGER])
def update_contract(current_user, contract_id):
    """Update a contract (only draft contracts can be updated)."""
    try:
        contract = RentalContract.query.get(contract_id)
        if not contract:
            return jsonify({'error': 'Contract not found'}), 404
        
        # Only allow updating draft contracts
        if contract.status != 'draft':
            return jsonify({'error': 'Only draft contracts can be updated'}), 400
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Update allowed fields
        if 'monthly_rent' in data:
            contract.monthly_rent = data['monthly_rent']
            contract.total_contract_value = contract._calculate_total_value()
        
        if 'security_deposit' in data:
            contract.security_deposit = data['security_deposit']
        
        if 'start_date' in data:
            try:
                contract.start_date = datetime.strptime(data['start_date'], '%Y-%m-%d').date()
                contract.end_date = contract._calculate_end_date(contract.start_date, contract.contract_type)
                contract.total_contract_value = contract._calculate_total_value()
            except ValueError:
                return jsonify({'error': 'Invalid start_date format. Use YYYY-MM-DD'}), 400
        
        if 'end_date' in data:
            try:
                contract.end_date = datetime.strptime(data['end_date'], '%Y-%m-%d').date()
                contract.total_contract_value = contract._calculate_total_value()
            except ValueError:
                return jsonify({'error': 'Invalid end_date format. Use YYYY-MM-DD'}), 400
        
        if 'contract_type' in data:
            contract_type = data['contract_type'].lower()
            if contract_type not in ['quarterly', 'yearly']:
                return jsonify({'error': 'contract_type must be "quarterly" or "yearly"'}), 400
            contract.contract_type = contract_type
            contract.end_date = contract._calculate_end_date(contract.start_date, contract_type)
            contract.total_contract_value = contract._calculate_total_value()
        
        if 'terms_and_conditions' in data:
            contract.terms_and_conditions = data['terms_and_conditions']
        
        if 'special_conditions' in data:
            contract.special_conditions = data['special_conditions']
        
        contract.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        
        return jsonify({
            'message': 'Contract updated successfully',
            'contract': contract.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating contract: {str(e)}")
        return jsonify({'error': f'Failed to update contract: {str(e)}'}), 500


@contract_bp.route('/<int:contract_id>/activate', methods=['POST'])
@require_role([UserRole.PROPERTY_MANAGER])
def activate_contract(current_user, contract_id):
    """Activate a contract (both parties must have signed)."""
    try:
        contract = RentalContract.query.get(contract_id)
        if not contract:
            return jsonify({'error': 'Contract not found'}), 404
        
        if contract.status != 'draft':
            return jsonify({'error': 'Only draft contracts can be activated'}), 400
        
        # Check if both parties have signed
        if not contract.tenant_signed or not contract.landlord_signed:
            return jsonify({
                'error': 'Both parties must sign the contract before activation',
                'tenant_signed': contract.tenant_signed,
                'landlord_signed': contract.landlord_signed
            }), 400
        
        contract.activate()
        
        # Update tenant_unit dates to match contract
        tenant_unit = TenantUnit.query.get(contract.tenant_unit_id)
        if tenant_unit:
            tenant_unit.rent_start_date = contract.start_date
            tenant_unit.rent_end_date = contract.end_date
            tenant_unit.move_in_date = contract.start_date
            tenant_unit.move_out_date = contract.end_date
            if contract.monthly_rent:
                tenant_unit.monthly_rent = contract.monthly_rent
            if contract.security_deposit:
                tenant_unit.security_deposit = contract.security_deposit
            db.session.commit()
        
        return jsonify({
            'message': 'Contract activated successfully',
            'contract': contract.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error activating contract: {str(e)}")
        return jsonify({'error': f'Failed to activate contract: {str(e)}'}), 500


@contract_bp.route('/<int:contract_id>/sign-tenant', methods=['POST'])
@require_role([UserRole.TENANT])
def sign_contract_tenant(current_user, contract_id):
    """Sign contract as tenant."""
    try:
        contract = RentalContract.query.get(contract_id)
        if not contract:
            return jsonify({'error': 'Contract not found'}), 404
        
        # Verify this is the tenant's contract
        tenant = Tenant.query.filter_by(user_id=current_user.id).first()
        if not tenant or contract.tenant_id != tenant.id:
            return jsonify({'error': 'Access denied'}), 403
        
        if contract.tenant_signed:
            return jsonify({'error': 'Contract already signed by tenant'}), 400
        
        contract.sign_by_tenant()
        
        return jsonify({
            'message': 'Contract signed by tenant successfully',
            'contract': contract.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error signing contract: {str(e)}")
        return jsonify({'error': f'Failed to sign contract: {str(e)}'}), 500


@contract_bp.route('/<int:contract_id>/sign-landlord', methods=['POST'])
@require_role([UserRole.PROPERTY_MANAGER])
def sign_contract_landlord(current_user, contract_id):
    """Sign contract as landlord/property manager."""
    try:
        contract = RentalContract.query.get(contract_id)
        if not contract:
            return jsonify({'error': 'Contract not found'}), 404
        
        if contract.landlord_signed:
            return jsonify({'error': 'Contract already signed by landlord'}), 400
        
        contract.sign_by_landlord(current_user.id)
        
        return jsonify({
            'message': 'Contract signed by landlord successfully',
            'contract': contract.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error signing contract: {str(e)}")
        return jsonify({'error': f'Failed to sign contract: {str(e)}'}), 500


@contract_bp.route('/<int:contract_id>/renew', methods=['POST'])
@require_role([UserRole.PROPERTY_MANAGER])
def renew_contract(current_user, contract_id):
    """
    Renew a contract (create a new contract based on existing one).
    
    Optional fields:
    - contract_type: New contract type ('quarterly' or 'yearly'), defaults to same as current
    - monthly_rent: New monthly rent, defaults to same as current
    - start_date: Start date for new contract, defaults to day after current contract ends
    """
    try:
        contract = RentalContract.query.get(contract_id)
        if not contract:
            return jsonify({'error': 'Contract not found'}), 404
        
        if contract.status not in ['active', 'expired']:
            return jsonify({'error': 'Only active or expired contracts can be renewed'}), 400
        
        data = request.get_json() or {}
        
        # Create renewal contract
        new_contract = contract.renew(
            new_contract_type=data.get('contract_type'),
            new_monthly_rent=data.get('monthly_rent'),
            start_date=datetime.strptime(data['start_date'], '%Y-%m-%d').date() if data.get('start_date') else None
        )
        
        return jsonify({
            'message': 'Contract renewed successfully',
            'old_contract': contract.to_dict(),
            'new_contract': new_contract.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error renewing contract: {str(e)}")
        return jsonify({'error': f'Failed to renew contract: {str(e)}'}), 500


@contract_bp.route('/<int:contract_id>/terminate', methods=['POST'])
@require_role([UserRole.PROPERTY_MANAGER])
def terminate_contract(current_user, contract_id):
    """Terminate a contract."""
    try:
        contract = RentalContract.query.get(contract_id)
        if not contract:
            return jsonify({'error': 'Contract not found'}), 404
        
        if contract.status in ['terminated', 'cancelled']:
            return jsonify({'error': 'Contract is already terminated or cancelled'}), 400
        
        data = request.get_json() or {}
        termination_reason = data.get('termination_reason')
        
        contract.terminate(termination_reason=termination_reason, terminated_by=current_user.id)
        
        # Also terminate the tenant_unit relationship
        tenant_unit = TenantUnit.query.get(contract.tenant_unit_id)
        if tenant_unit:
            tenant_unit.terminate_rent(termination_reason=termination_reason)
        
        return jsonify({
            'message': 'Contract terminated successfully',
            'contract': contract.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error terminating contract: {str(e)}")
        return jsonify({'error': f'Failed to terminate contract: {str(e)}'}), 500


@contract_bp.route('/<int:contract_id>/generate-document', methods=['POST'])
def generate_contract_document(contract_id):
    try:
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        expected_api_key = os.environ.get('CROSS_DOMAIN_API_KEY')
        if expected_api_key and api_key != expected_api_key:
            return jsonify({'error': 'Unauthorized'}), 401
        contract = RentalContract.query.get(contract_id)
        if not contract:
            return jsonify({'error': 'Contract not found'}), 404
        if contract.status != 'active' or not contract.tenant_signed or not contract.landlord_signed:
            return jsonify({
                'error': 'Contract must be active and fully signed to generate document',
                'status': contract.status,
                'tenant_signed': contract.tenant_signed,
                'landlord_signed': contract.landlord_signed
            }), 400
        tenant_unit_id = None
        tenant_profile_id = None
        if request.is_json:
            payload = request.get_json(silent=True) or {}
            tenant_unit_id = payload.get('tenant_unit_id')
            tenant_profile_id = payload.get('tenant_profile_id')
        updated = False
        if tenant_unit_id and not contract.tenant_unit_id:
            contract.tenant_unit_id = tenant_unit_id
            updated = True
        if tenant_profile_id and not contract.tenant_id:
            contract.tenant_id = tenant_profile_id
            updated = True
        if updated:
            contract.updated_at = datetime.now(timezone.utc)
            db.session.commit()
        try:
            contract._generate_pdf_and_upload_document()
        except Exception as e:
            current_app.logger.error(f"Failed to generate contract PDF: {str(e)}")
            return jsonify({'error': 'Failed to generate contract document'}), 500
        return jsonify({
            'message': 'Contract document generated successfully',
            'contract': contract.to_dict()
        }), 200
    except Exception as e:
        current_app.logger.error(f"Contract generate-document error: {str(e)}")
        return jsonify({'error': 'Failed to generate contract document'}), 500


@contract_bp.route('/expiring', methods=['GET'])
@jwt_required()
def get_expiring_contracts():
    """Get contracts expiring within a specified number of days (default: 30 days)."""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        days = request.args.get('days', 30, type=int)
        
        # Calculate expiry date threshold
        expiry_threshold = date.today() + timedelta(days=days)
        
        # Build query
        query = RentalContract.query.filter(
            and_(
                RentalContract.status == 'active',
                RentalContract.end_date >= date.today(),
                RentalContract.end_date <= expiry_threshold
            )
        )
        
        # Filter by role
        if isinstance(current_user.role, UserRole):
            user_role = current_user.role.value
        else:
            user_role = str(current_user.role).upper()
        
        if user_role == 'TENANT':
            tenant = Tenant.query.filter_by(user_id=current_user.id).first()
            if tenant:
                query = query.filter_by(tenant_id=tenant.id)
        
        query = query.order_by(RentalContract.end_date.asc())
        
        contracts = query.all()
        
        return jsonify({
            'contracts': [contract.to_dict(include_tenant=True, include_unit=True) for contract in contracts],
            'expiry_threshold': expiry_threshold.isoformat(),
            'days': days
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Error getting expiring contracts: {str(e)}")
        return jsonify({'error': f'Failed to get expiring contracts: {str(e)}'}), 500


@contract_bp.route('/generate-documents', methods=['POST'])
def generate_contract_documents():
    try:
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        expected_api_key = os.environ.get('CROSS_DOMAIN_API_KEY')
        if expected_api_key and api_key != expected_api_key:
            return jsonify({'error': 'Unauthorized'}), 401
        data = request.get_json() or {}
        property_id = data.get('property_id')
        only_missing = data.get('only_missing', True)
        query = RentalContract.query.filter(
            RentalContract.status == 'active',
            RentalContract.tenant_signed.is_(True),
            RentalContract.landlord_signed.is_(True)
        )
        if property_id:
            query = query.filter(RentalContract.property_id == property_id)
        if only_missing:
            query = query.filter(
                or_(
                    RentalContract.contract_document_path.is_(None),
                    RentalContract.contract_document_path == ''
                )
            )
        contracts = query.all()
        generated = 0
        skipped = []
        errors = []
        for contract in contracts:
            if not contract.tenant_id or not contract.tenant_unit_id:
                skipped.append(contract.id)
                continue
            try:
                contract._generate_pdf_and_upload_document()
                generated += 1
            except Exception as e:
                current_app.logger.error(f"Bulk contract document generation error for {contract.id}: {str(e)}")
                errors.append({'contract_id': contract.id, 'error': str(e)})
        return jsonify({
            'message': 'Contract document generation completed',
            'total_candidates': len(contracts),
            'generated': generated,
            'skipped': skipped,
            'errors': errors
        }), 200
    except Exception as e:
        current_app.logger.error(f"Bulk generate-documents error: {str(e)}")
        return jsonify({'error': 'Failed to generate contract documents'}), 500

