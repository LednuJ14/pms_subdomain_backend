from flask import Blueprint, request, jsonify, current_app, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.utils import secure_filename
from datetime import datetime, timezone
import os
import uuid
import mimetypes

from app import db
from models.document import Document, DocumentType
from models.user import User, UserRole
from utils.error_responses import (
    property_context_required,
    property_access_denied,
    property_not_found
)
from utils.logging_helpers import log_property_access_attempt, log_property_operation

document_bp = Blueprint('documents', __name__)

from models.user import User

def is_super_admin(user_id):
    if not user_id: return False
    user = User.query.get(user_id)
    return user and getattr(user, 'role', '') == 'ADMIN'


def get_current_user():
    """Helper function to get current user from JWT token."""
    current_user_id = get_jwt_identity()
    return User.query.get(current_user_id)

def allowed_file(filename):
    """Check if file extension is allowed."""
    ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@document_bp.route('/', methods=['GET'])
@jwt_required()
def get_documents():
    """
    Get documents
    ---
    tags:
      - Documents
    summary: Get documents filtered by user's role and permissions
    description: Retrieve documents based on the authenticated user's role and property context
    security:
      - Bearer: []
    parameters:
      - in: query
        name: page
        type: integer
        description: Page number for pagination
      - in: query
        name: per_page
        type: integer
        description: Number of items per page
      - in: query
        name: search
        type: string
        description: Search term
      - in: query
        name: type
        type: string
        description: Document type filter
      - in: query
        name: property_id
        type: integer
        description: Filter by property ID
    responses:
      200:
        description: Documents retrieved successfully
        schema:
          type: object
          properties:
            documents:
              type: array
              items:
                type: object
            total:
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
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        search = request.args.get('search', '')
        doc_type = request.args.get('type')
        
        # Base query
        query = Document.query
        
        # Get property_id filter
        property_id_filter = request.args.get('property_id', type=int)
        unit_id_filter = request.args.get('unit_id', type=int)
        
        # Get user role as string for comparison
        user_role_str = str(current_user.role).upper() if current_user.role else ''
        if isinstance(current_user.role, UserRole):
            user_role_str = current_user.role.value.upper()
        elif isinstance(current_user.role, str):
            user_role_str = current_user.role.upper()
        
        # Filter by property_id if provided (for property-specific views)
        if property_id_filter:
            query = query.filter(Document.property_id == property_id_filter)
        
        # Filter by user role and property
        if user_role_str == 'TENANT':
            # Tenants can see public documents, their own uploaded documents, or documents visible to tenants
            tenant_profile = None
            try:
                from models.tenant import Tenant
                tenant_profile = Tenant.query.filter_by(user_id=current_user.id).first()
            except Exception:
                pass
            
            if tenant_profile and tenant_profile.property_id:
                # Show documents for their property that are:
                # 1. Public or tenants_only (visible to all tenants)
                # 2. Tenant-specific documents (visibility='private' with tenant_id matching this tenant)
                # 3. Documents uploaded by them
                current_app.logger.debug(f"Tenant {current_user.id} filtering documents for property_id={tenant_profile.property_id}")
                query = query.filter(
                    (Document.property_id == tenant_profile.property_id) &
                    (
                        (Document.visibility.in_(['public', 'tenants_only'])) |
                        ((Document.visibility == 'private') & (Document.tenant_id == tenant_profile.id)) |
                        (Document.uploaded_by == current_user.id)
                    )
                )
                # Log for debugging
                count_before = query.count()
                current_app.logger.debug(f"Found {count_before} documents for tenant {current_user.id} in property {tenant_profile.property_id}")
            else:
                # If no tenant profile, only show public documents uploaded by them
                current_app.logger.warning(f"Tenant {current_user.id} has no property_id, showing only public/own documents")
                query = query.filter(
                    (Document.visibility == 'public') |
                    (Document.uploaded_by == current_user.id)
                )
        elif user_role_str == 'STAFF':
            # Staff can see all documents for their property except private tenant documents
            try:
                from models.staff import Staff
                staff_profile = Staff.query.filter_by(user_id=current_user.id).first()
                if staff_profile:
                    # Staff typically work for a property - you might want to add property_id to staff
                    # For now, show all non-private documents
                    query = query.filter(
                        Document.visibility.in_(['public', 'tenants_only', 'staff_only'])
                    )
                else:
                    query = query.filter(Document.visibility.in_(['public', 'tenants_only', 'staff_only']))
            except Exception:
                query = query.filter(Document.visibility.in_(['public', 'tenants_only', 'staff_only']))
        elif user_role_str in ['MANAGER', 'PROPERTY_MANAGER', 'ADMIN']:
            # Property managers can only see documents for their current property subdomain
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
            
            if not property_id:
                return property_context_required()
            
            # CRITICAL: Verify property exists and user owns it
            from models.property import Property
            property_obj = Property.query.get(property_id)
            if not property_obj:
                log_property_access_attempt(current_user.id, property_id, action='get_documents', success=False)
                return property_not_found()
            
            if property_obj.owner_id != current_user.id and not is_super_admin(current_user.id):
                log_property_access_attempt(current_user.id, property_id, action='get_documents', success=False)
                return property_access_denied()
            
            # Log successful property access
            log_property_access_attempt(current_user.id, property_id, action='get_documents', success=True)
            
            # Filter documents by property_id
            if property_id_filter:
                # If query param provided, verify it matches subdomain property
                if property_id_filter != property_id:
                    return jsonify({
                        'error': 'Property ID mismatch. Please access through the correct subdomain.',
                        'code': 'PROPERTY_MISMATCH'
                    }), 400
                query = query.filter(Document.property_id == property_id_filter)
            else:
                query = query.filter(Document.property_id == property_id)
        
        # Apply search filter
        if search:
            query = query.filter(
                Document.name.ilike(f'%{search}%')
            )
        
        # Apply type filter
        if doc_type:
            query = query.filter(Document.document_type == str(doc_type).lower())
        
        # Order by creation date (newest first)
        query = query.order_by(Document.created_at.desc())
        
        # Paginate
        documents = query.paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return jsonify({
            'documents': [doc.to_dict() for doc in documents.items],
            'total': documents.total,
            'pages': documents.pages,
            'current_page': page,
            'per_page': per_page,
            'has_next': documents.has_next,
            'has_prev': documents.has_prev
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Get documents error: {str(e)}")
        return jsonify({'error': 'Failed to fetch documents'}), 500

@document_bp.route('/<int:document_id>', methods=['GET'])
@jwt_required()
def get_document(document_id):
    """
    Get document by ID
    ---
    tags:
      - Documents
    summary: Get a specific document
    description: Retrieve document information by ID. Tenants can only access public documents or their own.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: document_id
        type: integer
        required: true
        description: The document ID
    responses:
      200:
        description: Document retrieved successfully
        schema:
          type: object
          properties:
            document:
              type: object
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Document not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        document = Document.query.get(document_id)
        if not document:
            return jsonify({'error': 'Document not found'}), 404
        
        # Check permissions
        user_role_str = str(current_user.role).upper() if current_user.role else ''
        if isinstance(current_user.role, UserRole):
            user_role_str = current_user.role.value.upper()
        elif isinstance(current_user.role, str):
            user_role_str = current_user.role.upper()
        
        if user_role_str == 'TENANT':
            tenant_profile = None
            try:
                from models.tenant import Tenant
                tenant_profile = Tenant.query.filter_by(user_id=current_user.id).first()
            except Exception:
                pass
            
            if tenant_profile:
                # Tenants can access:
                # 1. Public or tenants_only documents
                # 2. Tenant-specific documents (private with tenant_id matching this tenant)
                # 3. Documents they uploaded
                can_access = (
                    document.visibility in ['public', 'tenants_only'] or
                    (document.visibility == 'private' and document.tenant_id == tenant_profile.id) or
                    document.uploaded_by == current_user.id
                )
                if not can_access:
                    return jsonify({'error': 'Access denied'}), 403
            else:
                # If no tenant profile, only allow public documents or documents they uploaded
                if not (document.visibility == 'public' or document.uploaded_by == current_user.id):
                    return jsonify({'error': 'Access denied'}), 403
        
        return jsonify({'document': document.to_dict()}), 200
        
    except Exception as e:
        current_app.logger.error(f"Get document error: {str(e)}")
        return jsonify({'error': 'Failed to fetch document'}), 500

@document_bp.route('/', methods=['POST'])
@jwt_required()
def upload_document():
    """
    Upload document
    ---
    tags:
      - Documents
    summary: Upload a new document
    description: Upload a document file. Allowed for tenants, staff, and property managers.
    security:
      - Bearer: []
    parameters:
      - in: formData
        name: file
        type: file
        required: true
        description: Document file to upload
      - in: formData
        name: title
        type: string
        description: Document title
      - in: formData
        name: document_type
        type: string
        description: Type of document
      - in: formData
        name: property_id
        type: integer
        description: Associated property ID
    responses:
      201:
        description: Document uploaded successfully
        schema:
          type: object
          properties:
            message:
              type: string
            document:
              type: object
      400:
        description: Validation error or no file provided
      401:
        description: Unauthorized
      403:
        description: Forbidden - Role not allowed
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Allow tenants, property managers, and staff to upload documents
        # Get user role as string for comparison
        user_role_str = str(current_user.role).upper() if current_user.role else ''
        if isinstance(current_user.role, UserRole):
            user_role_str = current_user.role.value.upper()
        elif isinstance(current_user.role, str):
            user_role_str = current_user.role.upper()
        
        allowed_roles = ['TENANT', 'STAFF', 'MANAGER', 'PROPERTY_MANAGER']
        if user_role_str not in allowed_roles:
            return jsonify({'error': 'Access denied. Only tenants, staff, and property managers can upload documents.'}), 403
        
        # Debug: Log request information
        current_app.logger.debug(f"Upload request - Content-Type: {request.content_type}")
        current_app.logger.debug(f"Upload request - Files: {list(request.files.keys())}")
        current_app.logger.debug(f"Upload request - Form keys: {list(request.form.keys())}")
        
        # Check if file is present
        if 'file' not in request.files:
            current_app.logger.error(f"No 'file' in request.files. Available keys: {list(request.files.keys())}")
            return jsonify({'error': 'No file provided', 'debug': {'files_keys': list(request.files.keys()), 'content_type': request.content_type}}), 400
        
        file = request.files['file']
        if file.filename == '':
            current_app.logger.error("File filename is empty")
            return jsonify({'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'error': 'File type not allowed'}), 400
        
        # Get form data
        name = request.form.get('name', file.filename) or file.filename
        doc_type = request.form.get('document_type', 'other')
        property_id = request.form.get('property_id')
        unit_id = request.form.get('unit_id')
        visibility = request.form.get('visibility', 'private')
        tenant_id = request.form.get('tenant_id')  # For tenant-specific documents
        
        # Get property_id from subdomain if not provided
        if not property_id:
            try:
                from routes.auth_routes import get_property_id_from_request
                property_id = get_property_id_from_request(data=request.form.to_dict() if hasattr(request.form, 'to_dict') else {})
            except Exception as prop_error:
                current_app.logger.warning(f"Could not get property_id from request: {str(prop_error)}")
        
        # For tenants, get property_id from their tenant profile if not provided
        if not property_id and user_role_str == 'TENANT':
            try:
                from models.tenant import Tenant
                tenant_profile = Tenant.query.filter_by(user_id=current_user.id).first()
                if tenant_profile:
                    property_id = tenant_profile.property_id
            except Exception as tenant_error:
                current_app.logger.warning(f"Could not get property_id from tenant profile: {str(tenant_error)}")
        
        # CRITICAL: Do NOT auto-detect from owned properties for property managers
        # Property managers must access through the correct subdomain
        # If property_id not in request, try to get from JWT token
        if not property_id and user_role_str in ['MANAGER', 'PROPERTY_MANAGER', 'ADMIN']:
            from flask_jwt_extended import get_jwt
            try:
                claims = get_jwt()
                property_id = claims.get('property_id')
            except Exception:
                pass
        
        # CRITICAL: For property managers, verify ownership before allowing upload
        if property_id and user_role_str in ['MANAGER', 'PROPERTY_MANAGER', 'ADMIN']:
            try:
                property_id_int = int(property_id)
                from models.property import Property
                property_obj = Property.query.get(property_id_int)
                if not property_obj:
                    return jsonify({'error': 'Property not found'}), 404
                
                if property_obj.owner_id != current_user.id and not is_super_admin(current_user.id):
                    return jsonify({
                        'error': 'Access denied. You do not own this property.',
                        'code': 'PROPERTY_ACCESS_DENIED'
                    }), 403
            except (ValueError, TypeError):
                pass
        
        # Validate and convert property_id (handle both numeric IDs and subdomain strings)
        property_id_final = None
        if property_id:
            try:
                # Try to convert to int first (numeric property_id)
                property_id_int = int(property_id)
                from models.property import Property
                property_obj = Property.query.get(property_id_int)
                if property_obj:
                    property_id_final = property_id_int
                    current_app.logger.debug(f"Using property_id: {property_id_final}")
                else:
                    current_app.logger.warning(f"Property with id {property_id_int} not found")
                    # For property managers, allow upload without valid property_id
                    if user_role_str not in ['MANAGER', 'PROPERTY_MANAGER', 'ADMIN']:
                        return jsonify({'error': f'Property with id {property_id_int} not found'}), 404
            except (ValueError, TypeError) as e:
                # If not a number, try to find by subdomain
                try:
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
                        property_id_final = property_obj[0]
                        current_app.logger.debug(f"Found property {property_id_final} by subdomain/name: {property_id}")
                    else:
                        current_app.logger.warning(f"Property not found by subdomain/name: {property_id}")
                        # For property managers, allow upload without valid property_id
                        if user_role_str not in ['MANAGER', 'PROPERTY_MANAGER', 'ADMIN']:
                            return jsonify({'error': f'Property not found: {property_id}'}), 404
                except Exception as lookup_error:
                    current_app.logger.error(f"Error looking up property by subdomain: {str(lookup_error)}")
                    # For property managers, allow upload even if lookup fails
                    if user_role_str not in ['MANAGER', 'PROPERTY_MANAGER', 'ADMIN']:
                        return jsonify({'error': 'Invalid property_id'}), 400
        
        # For tenant-visible documents (tenants_only, public), property_id is REQUIRED
        # This ensures tenants can see documents in their property
        if visibility in ['tenants_only', 'public'] and not property_id_final:
            # Try one more time to get property_id from request
            if not property_id_final:
                try:
                    from routes.auth_routes import get_property_id_from_request
                    # Try to get from headers/query params
                    property_id_final = get_property_id_from_request()
                except Exception:
                    pass
            
            if not property_id_final:
                return jsonify({
                    'error': 'Property ID is required for documents visible to tenants. Please ensure you are accessing the correct property subdomain.'
                }), 400
        
        # For tenants, property_id is always required
        if not property_id_final and user_role_str == 'TENANT':
            return jsonify({'error': 'Property ID is required for tenant document uploads'}), 400
        
        # Use the validated property_id (can be None for property managers only if visibility is private/staff_only)
        property_id = property_id_final
        
        # Validate document type (accept as string)
        document_type_str = str(doc_type).lower() if doc_type else 'other'
        valid_types = ['lease', 'invoice', 'receipt', 'policy', 'maintenance', 'other']
        if document_type_str not in valid_types:
            document_type_str = 'other'
        
        # Generate clean filename (use original filename, ensure uniqueness on disk)
        original_filename = secure_filename(file.filename)
        
        # Check if Cloudinary is configured
        if current_app.config.get('CLOUDINARY_CLOUD_NAME'):
            from utils.cloudinary_helpers import upload_to_cloudinary
            success, file_path, error = upload_to_cloudinary(
                file, 
                folder=f"jacs/subdomain/documents/{property_id if property_id else 'general'}"
            )
            if not success:
                return jsonify({'error': error}), 400
        else:
            # Ensure upload directory exists
            upload_dir = os.path.join(current_app.instance_path, current_app.config.get('UPLOAD_FOLDER', 'uploads'))
            os.makedirs(upload_dir, exist_ok=True)
            
            # Generate unique file path on disk (using UUID only for file system)
            # But store original filename in database
            file_extension = os.path.splitext(original_filename)[1] if '.' in original_filename else ''
            file_base_name = os.path.splitext(original_filename)[0] if '.' in original_filename else original_filename
            
            # Check if file already exists, if so append a number
            disk_filename = original_filename
            counter = 1
            while os.path.exists(os.path.join(upload_dir, disk_filename)):
                disk_filename = f"{file_base_name}_{counter}{file_extension}"
                counter += 1
            
            # Save file with clean name (or numbered if duplicate)
            file_path = os.path.join(upload_dir, disk_filename)
            file.save(file_path)
        
        # Validate tenant_id if provided (for tenant-specific documents)
        tenant_id_final = None
        if tenant_id:
            try:
                tenant_id_int = int(tenant_id)
                from models.tenant import Tenant
                tenant_obj = Tenant.query.get(tenant_id_int)
                if tenant_obj:
                    tenant_id_final = tenant_id_int
                    # Ensure tenant belongs to the same property if property_id is set
                    if property_id and tenant_obj.property_id != property_id:
                        return jsonify({'error': 'Tenant does not belong to the specified property'}), 400
                else:
                    return jsonify({'error': 'Invalid tenant_id'}), 400
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid tenant_id format'}), 400
        
        # For tenant-specific documents (visibility='private' with tenant_id), ensure tenant_id is set
        if visibility == 'private' and tenant_id_final:
            # This is a tenant-specific document
            pass
        elif visibility == 'private' and not tenant_id_final:
            # Private document without tenant_id - general private document
            pass
        
        # Create document record (simplified schema: name, filename, file_path, document_type, uploaded_by, property_id, tenant_id, visibility)
        # property_id can be None for property managers uploading general documents
        # tenant_id is set for tenant-specific documents (visibility='private' with tenant_id)
        # Store original filename in database (clean name without UUID)
        document = Document(
            name=name,
            filename=original_filename,  # Store original filename, not the disk filename
            file_path=file_path,
            document_type=document_type_str,  # Store as string
            uploaded_by=current_user.id,
            property_id=property_id,  # Can be None for property managers
            tenant_id=tenant_id_final,  # Set for tenant-specific documents
            visibility=visibility if visibility in ['public', 'tenants_only', 'staff_only', 'private'] else 'private'
        )
        
        db.session.add(document)
        db.session.commit()
        
        current_app.logger.debug(f"Document uploaded: {document.id} by user {current_user.id}")
        
        return jsonify({
            'message': 'Document uploaded successfully',
            'document': document.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Upload document error: {str(e)}")
        return jsonify({'error': 'Failed to upload document'}), 500

@document_bp.route('/<int:document_id>/download', methods=['GET'])
def download_document(document_id):
    """
    Download document
    ---
    tags:
      - Documents
    summary: Download a document file
    description: Download a document file. JWT optional for main domain cross-domain access via API key.
    parameters:
      - in: path
        name: document_id
        type: integer
        required: true
        description: The document ID
      - in: header
        name: X-API-Key
        type: string
        description: API key for cross-domain access (optional)
      - in: query
        name: api_key
        type: string
        description: API key as query parameter (optional)
    responses:
      200:
        description: Document file
        schema:
          type: file
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Document not found
      500:
        description: Server error
    """
    try:
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        expected_api_key = os.environ.get('CROSS_DOMAIN_API_KEY')
        is_main_domain_request = False
        if expected_api_key and api_key == expected_api_key:
            is_main_domain_request = True
        document = Document.query.get(document_id)
        if not document:
            return jsonify({'error': 'Document not found'}), 404
        if not is_main_domain_request:
            try:
                from flask_jwt_extended import get_jwt_identity
                current_user_id = get_jwt_identity()
                if current_user_id:
                    current_user = get_current_user()
                    if current_user:
                        user_role_str = str(current_user.role).upper() if current_user.role else ''
                        if isinstance(current_user.role, UserRole):
                            user_role_str = current_user.role.value.upper()
                        elif isinstance(current_user.role, str):
                            user_role_str = current_user.role.upper()
                        if user_role_str == 'TENANT':
                            tenant_profile = None
                            try:
                                from models.tenant import Tenant
                                tenant_profile = Tenant.query.filter_by(user_id=current_user.id).first()
                            except Exception:
                                tenant_profile = None
                            has_access = False
                            if document.uploaded_by == current_user.id:
                                has_access = True
                            elif document.visibility in ['public', 'tenants_only']:
                                if tenant_profile and tenant_profile.property_id == document.property_id:
                                    has_access = True
                                elif document.visibility == 'public':
                                    has_access = True
                            elif document.visibility == 'private' and tenant_profile and document.tenant_id == tenant_profile.id:
                                has_access = True
                            if not has_access:
                                return jsonify({'error': 'Access denied'}), 403
                        elif user_role_str == 'STAFF':
                            if document.visibility == 'private' and document.uploaded_by != current_user.id:
                                return jsonify({'error': 'Access denied'}), 403
                        else:
                            pass
                else:
                    current_app.logger.warning('Download without JWT identity; treating as authenticated guest for development')
            except Exception:
                current_app.logger.warning('JWT decode failed for document download; allowing request for development')
        file_path = document.file_path
        
        # Handle external URLs (like Cloudinary)
        if file_path and (file_path.startswith('http://') or file_path.startswith('https://')):
            from flask import redirect
            return redirect(file_path)
            
        if not os.path.exists(file_path):
            regenerated_path = None
            try:
                from models.rental_contract import RentalContract
                contract_number = None
                filename = document.filename or ''
                lower_name = filename.lower()
                if document.document_type == 'lease' and lower_name.startswith('rental_contract_') and (lower_name.endswith('.pdf') or lower_name.endswith('.docx')):
                    # Handle both .pdf and .docx
                    ext_len = 4 if lower_name.endswith('.pdf') else 5
                    contract_number = filename[len('rental_contract_'):-ext_len]
                contract = None
                if contract_number:
                    contract = RentalContract.query.filter_by(contract_number=contract_number).first()
                if contract:
                    contract._generate_pdf_and_upload_document()
                    regenerated_path = contract.contract_document_path
                    if regenerated_path:
                        document.file_path = regenerated_path
                        db.session.add(document)
                        db.session.commit()
            except Exception as regen_error:
                current_app.logger.error(f"Download regeneration error: {str(regen_error)}")
            
            # If regenerated path is a URL, redirect to it
            if regenerated_path and (regenerated_path.startswith('http://') or regenerated_path.startswith('https://')):
                from flask import redirect
                return redirect(regenerated_path)
                
            if regenerated_path and os.path.exists(regenerated_path):
                file_path = regenerated_path
            else:
                return jsonify({'error': 'File not found on server'}), 404
        mime_type = mimetypes.guess_type(document.filename)[0] or 'application/octet-stream'
        return send_file(
            file_path,
            as_attachment=True,
            download_name=document.filename or document.name,
            mimetype=mime_type
        )
    except Exception as e:
        current_app.logger.error(f"Download document error: {str(e)}")
        return jsonify({'error': 'Failed to download document'}), 500

@document_bp.route('/<int:document_id>', methods=['PUT'])
@jwt_required()
def update_document(document_id):
    """
    Update document
    ---
    tags:
      - Documents
    summary: Update document metadata
    description: Update document information. Users can update their own documents, managers/staff can update any.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: document_id
        type: integer
        required: true
        description: The document ID
      - in: body
        name: body
        schema:
          type: object
          properties:
            name:
              type: string
            description:
              type: string
            document_type:
              type: string
            visibility:
              type: string
              enum: [public, tenants_only, staff_only, private]
            property_id:
              type: integer
    responses:
      200:
        description: Document updated successfully
        schema:
          type: object
          properties:
            message:
              type: string
            document:
              type: object
      400:
        description: Validation error
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Document not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Allow users to update their own documents, or managers/staff to update any
        user_role_str = str(current_user.role).upper() if current_user.role else ''
        if isinstance(current_user.role, UserRole):
            user_role_str = current_user.role.value.upper()
        elif isinstance(current_user.role, str):
            user_role_str = current_user.role.upper()
        
        document = Document.query.get(document_id)
        if not document:
            return jsonify({'error': 'Document not found'}), 404
        
        # Users can update their own documents, managers/staff can update any
        if document.uploaded_by != current_user.id:
            if user_role_str not in ['MANAGER', 'PROPERTY_MANAGER', 'STAFF']:
                return jsonify({'error': 'Access denied. You can only update your own documents.'}), 403
            
            # CRITICAL: For property managers, verify property ownership
            if user_role_str in ['MANAGER', 'PROPERTY_MANAGER', 'ADMIN'] and document.property_id:
                from models.property import Property
                property_obj = Property.query.get(document.property_id)
                if not property_obj:
                    return jsonify({'error': 'Property not found'}), 404
                
                if property_obj.owner_id != current_user.id and not is_super_admin(current_user.id):
                    return jsonify({
                        'error': 'Access denied. You do not own this property.',
                        'code': 'PROPERTY_ACCESS_DENIED'
                    }), 403
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Update fields if provided (simplified schema)
        if 'name' in data:
            document.name = data['name'].strip()
        
        if 'document_type' in data:
            doc_type_str = str(data['document_type']).lower()
            valid_types = ['lease', 'invoice', 'receipt', 'policy', 'maintenance', 'other']
            if doc_type_str in valid_types:
                document.document_type = doc_type_str
            else:
                return jsonify({'error': f'Invalid document type: {data["document_type"]}'}), 400
        
        if 'property_id' in data and data['property_id']:
            # Verify property exists
            try:
                from models.property import Property
                property_obj = Property.query.get(int(data['property_id']))
                if not property_obj:
                    return jsonify({'error': f'Property with id {data["property_id"]} not found'}), 404
                document.property_id = int(data['property_id'])
            except (ValueError, TypeError):
                return jsonify({'error': 'Invalid property_id'}), 400
        
        if 'visibility' in data:
            visibility_str = str(data['visibility']).lower()
            if visibility_str in ['public', 'tenants_only', 'staff_only', 'private']:
                document.visibility = visibility_str
            else:
                return jsonify({'error': f'Invalid visibility: {data["visibility"]}'}), 400
        
        db.session.commit()
        
        current_app.logger.debug(f"Document updated: {document_id} by user {current_user.id}")
        
        return jsonify({
            'message': 'Document updated successfully',
            'document': document.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update document error: {str(e)}")
        return jsonify({'error': 'Failed to update document'}), 500

@document_bp.route('/<int:document_id>', methods=['DELETE'])
@jwt_required()
def delete_document(document_id):
    """
    Delete document
    ---
    tags:
      - Documents
    summary: Delete a document
    description: Delete a document. Users can delete their own documents, managers/staff can delete any.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: document_id
        type: integer
        required: true
        description: The document ID
    responses:
      200:
        description: Document deleted successfully
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
        description: Document not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        document = Document.query.get(document_id)
        if not document:
            return jsonify({'error': 'Document not found'}), 404
        
        # Allow users to delete their own documents, or managers/staff to delete any
        user_role_str = str(current_user.role).upper() if current_user.role else ''
        if isinstance(current_user.role, UserRole):
            user_role_str = current_user.role.value.upper()
        elif isinstance(current_user.role, str):
            user_role_str = current_user.role.upper()
        
        # Users can delete their own documents, managers/staff can delete any
        if document.uploaded_by != current_user.id:
            if user_role_str not in ['MANAGER', 'PROPERTY_MANAGER', 'STAFF']:
                return jsonify({'error': 'Access denied. You can only delete your own documents.'}), 403
            
            # CRITICAL: For property managers, verify property ownership
            if user_role_str in ['MANAGER', 'PROPERTY_MANAGER', 'ADMIN'] and document.property_id:
                from models.property import Property
                property_obj = Property.query.get(document.property_id)
                if not property_obj:
                    return jsonify({'error': 'Property not found'}), 404
                
                if property_obj.owner_id != current_user.id and not is_super_admin(current_user.id):
                    return jsonify({
                        'error': 'Access denied. You do not own this property.',
                        'code': 'PROPERTY_ACCESS_DENIED'
                    }), 403
        
        # Delete file from filesystem
        if os.path.exists(document.file_path):
            try:
                os.remove(document.file_path)
            except OSError as e:
                current_app.logger.warning(f"Failed to delete file: {str(e)}")
        
        # Delete database record
        db.session.delete(document)
        db.session.commit()
        
        current_app.logger.debug(f"Document deleted: {document_id} by user {current_user.id}")
        
        return jsonify({'message': 'Document deleted successfully'}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete document error: {str(e)}")
        return jsonify({'error': 'Failed to delete document'}), 500

@document_bp.route('/types', methods=['GET'])
@jwt_required()
def get_document_types():
    """
    Get document types
    ---
    tags:
      - Documents
    summary: Get available document types
    description: Retrieve list of available document types
    security:
      - Bearer: []
    responses:
      200:
        description: Document types retrieved successfully
        schema:
          type: array
          items:
            type: object
            properties:
              value:
                type: string
              label:
                type: string
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        # Return document types as strings (matching database enum as string)
        types = [
            {'value': 'lease', 'label': 'Lease'},
            {'value': 'invoice', 'label': 'Invoice'},
            {'value': 'receipt', 'label': 'Receipt'},
            {'value': 'policy', 'label': 'Policy'},
            {'value': 'maintenance', 'label': 'Maintenance'},
            {'value': 'other', 'label': 'Other'}
        ]
        return jsonify({'document_types': types}), 200
    except Exception as e:
        current_app.logger.error(f"Get document types error: {str(e)}")
        return jsonify({'error': 'Failed to fetch document types'}), 500

@document_bp.route('/by-property/<int:property_id>', methods=['GET'])
@jwt_required()
def get_documents_by_property(property_id):
    """
    Get documents by property
    ---
    tags:
      - Documents
    summary: Get all documents for a specific property
    description: Get all documents for a specific property. For property managers.
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
        name: search
        type: string
      - in: query
        name: type
        type: string
    responses:
      200:
        description: Documents retrieved successfully
        schema:
          type: object
          properties:
            documents:
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
      404:
        description: Property not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Verify property exists
        from models.property import Property
        property_obj = Property.query.get(property_id)
        if not property_obj:
            return jsonify({'error': f'Property with id {property_id} not found'}), 404
        
        # Get user role
        user_role_str = str(current_user.role).upper() if current_user.role else ''
        if isinstance(current_user.role, UserRole):
            user_role_str = current_user.role.value.upper()
        elif isinstance(current_user.role, str):
            user_role_str = current_user.role.upper()
        
        # Allow managers, property managers, and tenants (for their own property)
        # Note: This endpoint is for main domain access (subdomain doesn't have admin role)
        if user_role_str not in ['MANAGER', 'PROPERTY_MANAGER', 'ADMIN']:
            # Check if user is a tenant and this is their property
            if user_role_str == 'TENANT':
                try:
                    from models.tenant import Tenant
                    tenant_profile = Tenant.query.filter_by(user_id=current_user.id).first()
                    if not tenant_profile or tenant_profile.property_id != property_id:
                        return jsonify({'error': 'Access denied. You can only view documents for your property.'}), 403
                except Exception:
                    return jsonify({'error': 'Access denied'}), 403
            else:
                return jsonify({'error': 'Access denied'}), 403
        
        # Get query parameters
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 50, type=int), 100)
        search = request.args.get('search', '')
        doc_type = request.args.get('type')
        unit_id = request.args.get('unit_id', type=int)
        
        # Base query - filter by property_id
        query = Document.query.filter(Document.property_id == property_id)
        
        # Filter by unit if provided (documents uploaded by tenants in that unit)
        if unit_id:
            try:
                from sqlalchemy import text
                # Get user_ids of tenants that have this unit
                tenant_users = db.session.execute(text(
                    """
                    SELECT DISTINCT t.user_id 
                    FROM tenant_units tu
                    INNER JOIN tenants t ON tu.tenant_id = t.id
                    WHERE tu.unit_id = :unit_id
                    """
                ), {'unit_id': unit_id}).fetchall()
                user_ids_list = [row[0] for row in tenant_users if row[0]] if tenant_users else []
                
                # Filter documents uploaded by tenants in this unit
                if user_ids_list:
                    query = query.filter(Document.uploaded_by.in_(user_ids_list))
                else:
                    # No tenants in this unit, return empty
                    query = query.filter(Document.id == -1)  # Impossible condition
            except Exception as unit_error:
                current_app.logger.warning(f"Error filtering by unit: {str(unit_error)}")
                # Continue without unit filter
        
        # Apply search filter
        if search:
            query = query.filter(Document.name.ilike(f'%{search}%'))
        
        # Apply type filter
        if doc_type:
            query = query.filter(Document.document_type == str(doc_type).lower())
        
        # Order by creation date (newest first)
        query = query.order_by(Document.created_at.desc())
        
        # Paginate
        documents = query.paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return jsonify({
            'documents': [doc.to_dict() for doc in documents.items],
            'property': {
                'id': property_obj.id,
                'name': getattr(property_obj, 'name', None) or getattr(property_obj, 'title', None) or getattr(property_obj, 'building_name', None)
            },
            'total': documents.total,
            'pages': documents.pages,
            'current_page': page,
            'per_page': per_page,
            'has_next': documents.has_next,
            'has_prev': documents.has_prev
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Get documents by property error: {str(e)}", exc_info=True)
        return jsonify({'error': 'Failed to fetch documents'}), 500

@document_bp.route('/all', methods=['GET'])
def get_all_documents():
    """
    Get all documents
    ---
    tags:
      - Documents
    summary: Get all documents across all properties
    description: Get all documents across all properties. For main domain access - accessible without JWT for cross-domain access.
    parameters:
      - in: header
        name: X-API-Key
        type: string
        description: API key for cross-domain access (optional)
      - in: query
        name: api_key
        type: string
        description: API key as query parameter (optional)
      - in: query
        name: page
        type: integer
        default: 1
      - in: query
        name: per_page
        type: integer
        default: 20
      - in: query
        name: property_id
        type: integer
    responses:
      200:
        description: Documents retrieved successfully
        schema:
          type: object
          properties:
            documents:
              type: array
              items:
                type: object
            total:
              type: integer
            pages:
              type: integer
      400:
        description: Validation error
      500:
        description: Server error
    """
    try:
        # Optional: Add API key check for security (can be configured via environment variable)
        # For now, allow access from localhost (main domain backend)
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        expected_api_key = os.environ.get('CROSS_DOMAIN_API_KEY')
        if expected_api_key and api_key != expected_api_key:
            # If API key is configured but not provided or incorrect, deny access
            return jsonify({'error': 'Invalid or missing API key'}), 401
        # Get query parameters
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 100, type=int), 500)
        search = request.args.get('search', '')
        doc_type = request.args.get('type')
        property_id = request.args.get('property_id', type=int)
        
        # Base query - get all documents
        query = Document.query
        
        # Filter by property_id if provided
        if property_id:
            query = query.filter(Document.property_id == property_id)
        
        # Apply search filter
        if search:
            query = query.filter(Document.name.ilike(f'%{search}%'))
        
        # Apply type filter
        if doc_type:
            query = query.filter(Document.document_type == str(doc_type).lower())
        
        # Order by creation date (newest first)
        query = query.order_by(Document.created_at.desc())
        
        # Paginate
        documents = query.paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        # Enhance documents with property and uploader information
        enhanced_docs = []
        for doc in documents.items:
            doc_dict = doc.to_dict()
            # Add property name if available
            if doc.property_id:
                try:
                    from models.property import Property
                    prop = Property.query.get(doc.property_id)
                    if prop:
                        doc_dict['property_name'] = getattr(prop, 'name', None) or getattr(prop, 'title', None) or getattr(prop, 'building_name', None)
                        doc_dict['property_subdomain'] = getattr(prop, 'portal_subdomain', None)
                except Exception:
                    pass
            # Add uploader name if available
            if doc.uploaded_by:
                try:
                    uploader = User.query.get(doc.uploaded_by)
                    if uploader:
                        doc_dict['uploader_name'] = f"{getattr(uploader, 'first_name', '')} {getattr(uploader, 'last_name', '')}".strip()
                        doc_dict['uploader_email'] = getattr(uploader, 'email', None)
                        doc_dict['uploader_role'] = str(getattr(uploader, 'role', ''))
                except Exception:
                    pass
            # Mark as subdomain document
            doc_dict['source'] = 'subdomain'
            enhanced_docs.append(doc_dict)
        
        return jsonify({
            'documents': enhanced_docs,
            'total': documents.total,
            'pages': documents.pages,
            'current_page': page,
            'per_page': per_page,
            'has_next': documents.has_next,
            'has_prev': documents.has_prev
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Get all documents error: {str(e)}", exc_info=True)
        return jsonify({'error': 'Failed to fetch documents'}), 500

@document_bp.route('/test', methods=['GET'])
def test_documents():
    """Test endpoint for document functionality."""
    return jsonify({
        'status': 'ok',
        'message': 'Document routes are working',
        'available_endpoints': [
            'GET /documents/',
            'POST /documents/',
            'GET /documents/types',
            'GET /documents/<id>',
            'PUT /documents/<id>',
            'DELETE /documents/<id>',
            'GET /documents/<id>/download',
            'GET /documents/all (for main domain access)'
        ]
    }), 200
