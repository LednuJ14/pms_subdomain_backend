from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timezone

from app import db
from models.task import Task, TaskPriority, TaskStatus
from models.user import User, UserRole
from utils.error_responses import (
    property_context_required,
    property_access_denied,
    property_not_found
)
from utils.logging_helpers import log_property_access_attempt, log_property_operation

task_bp = Blueprint('tasks', __name__)

from models.user import User

def is_super_admin(user_id):
    if not user_id: return False
    user = User.query.get(user_id)
    return user and getattr(user, 'role', '') == 'ADMIN'


def get_current_user():
    """Helper function to get current user from JWT token."""
    current_user_id = get_jwt_identity()
    return User.query.get(current_user_id)

@task_bp.route('/my-tasks', methods=['GET'])
@jwt_required()
def get_my_tasks():
    """
    Get my tasks
    ---
    tags:
      - Tasks
    summary: Get tasks assigned to the current staff member
    description: Retrieve tasks assigned to the current staff member
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
        description: Tasks retrieved successfully
        schema:
          type: object
          properties:
            tasks:
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
        current_user_id = get_jwt_identity()
        user = User.query.get(current_user_id)
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        # Use the helper method that handles both enum and string role values
        if not user.is_staff():
            return jsonify({'error': 'Only staff can access their tasks'}), 403
        
        # Get staff profile (optional check)
        staff_profile = user.staff_profile
        # Note: Staff profile might not exist, but that's okay
        # Tasks are assigned to User IDs, not Staff profile IDs
        
        # Get tasks assigned to this user (staff member)
        tasks = Task.query.filter_by(assigned_to=user.id).order_by(Task.created_at.desc()).all()
        
        return jsonify({
            'tasks': [task.to_dict() for task in tasks],
            'total': len(tasks)
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Get my tasks error: {str(e)}")
        return jsonify({'error': 'Failed to load tasks'}), 500

@task_bp.route('/', methods=['GET'])
@jwt_required()
def get_tasks():
    """
    Get tasks
    ---
    tags:
      - Tasks
    summary: Get tasks filtered by user's role
    description: Get tasks filtered by user's role and permissions, and property_id from subdomain
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
        name: priority
        type: string
      - in: query
        name: assigned_to
        type: integer
    responses:
      200:
        description: Tasks retrieved successfully
        schema:
          type: object
          properties:
            tasks:
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
        property_id = None
        try:
            from routes.auth_routes import get_property_id_from_request
            property_id = get_property_id_from_request()
        except Exception:
            property_id = request.args.get('property_id', type=int) or request.headers.get('X-Property-ID', type=int)
        
        # If property_id not in request, try to get from JWT token
        if not property_id:
            try:
                from flask_jwt_extended import get_jwt
                property_id = get_jwt().get('property_id')
            except Exception:
                pass
        
        # Get query parameters
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        search = request.args.get('search', '')
        status = request.args.get('status')
        priority = request.args.get('priority')
        assigned_to = request.args.get('assigned_to')
        
        # Base query
        query = Task.query
        
        # Determine user role
        user_role = current_user.role
        if isinstance(user_role, UserRole):
            user_role_str = user_role.value
        elif isinstance(user_role, str):
            user_role_str = user_role.upper()
        else:
            user_role_str = str(user_role).upper() if user_role else 'TENANT'
        
        # Filter by user role AND property_id
        if user_role_str == 'TENANT':
            # Tenants can only see tasks assigned to them or their unit, for their property
            from models.tenant import Tenant
            tenant = Tenant.query.filter_by(user_id=current_user.id).first()
            # Tenants can only see tasks assigned to them or their tenant_id
            # Simple filter - don't complicate with property filtering for tenants
            if tenant:
                query = query.filter(
                    (Task.assigned_to == current_user.id) |
                    (Task.tenant_id == tenant.id)
                )
            else:
                query = query.filter(Task.assigned_to == current_user.id)
        elif user_role_str == 'STAFF':
            # Staff can see tasks for their property
            if property_id:
                from models.property import Unit
                from models.tenant import Tenant
                from sqlalchemy import or_
                # Filter by tasks where unit belongs to property OR tenant belongs to property
                # Use subqueries to avoid join complexity and duplicates
                # Get unit IDs and tenant IDs for this property
                unit_ids = [u[0] for u in db.session.query(Unit.id).filter(Unit.property_id == property_id).all()]
                tenant_ids = [t[0] for t in db.session.query(Tenant.id).filter(Tenant.property_id == property_id).all()]
                
                # Filter tasks where unit_id is in property's units OR tenant_id is in property's tenants
                conditions = []
                if unit_ids:
                    conditions.append(Task.unit_id.in_(unit_ids))
                if tenant_ids:
                    conditions.append(Task.tenant_id.in_(tenant_ids))
                
                if conditions:
                    query = query.filter(or_(*conditions) if len(conditions) > 1 else conditions[0])
                else:
                    query = query.filter(Task.id == -1)  # No units/tenants, return empty
        elif user_role_str in ['MANAGER', 'PROPERTY_MANAGER', 'ADMIN']:
            # Property managers can see all tasks for their property
            if not property_id:
                return property_context_required()
            
            # CRITICAL: Verify property exists and user owns it
            from models.property import Property
            property_obj = Property.query.get(property_id)
            if not property_obj:
                log_property_access_attempt(current_user.id, property_id, action='get_tasks', success=False)
                return property_not_found()
            
            if property_obj.owner_id != current_user.id and not is_super_admin(current_user.id):
                log_property_access_attempt(current_user.id, property_id, action='get_tasks', success=False)
                return property_access_denied()
            
            # Log successful property access
            log_property_access_attempt(current_user.id, property_id, action='get_tasks', success=True)
            
            # Filter tasks by property_id through units or tenants
            from models.property import Unit
            from models.tenant import Tenant
            from sqlalchemy import or_
            
            # Get unit IDs and tenant IDs for this property
            unit_ids = [u[0] for u in db.session.query(Unit.id).filter(Unit.property_id == property_id).all()]
            tenant_ids = [t[0] for t in db.session.query(Tenant.id).filter(Tenant.property_id == property_id).all()]
            
            # Filter tasks where unit_id is in property's units OR tenant_id is in property's tenants
            conditions = []
            if unit_ids:
                conditions.append(Task.unit_id.in_(unit_ids))
            if tenant_ids:
                conditions.append(Task.tenant_id.in_(tenant_ids))
            
            if conditions:
                query = query.filter(or_(*conditions) if len(conditions) > 1 else conditions[0])
            else:
                query = query.filter(Task.id == -1)  # No units/tenants, return empty
        else:
            # Unknown role - return empty result for security
            query = query.filter(Task.id == -1)
        
        # Apply search filter
        if search:
            query = query.filter(
                Task.title.ilike(f'%{search}%') |
                Task.description.ilike(f'%{search}%')
            )
        
        # Apply status filter
        if status:
            # Task.status is stored as String, not Enum, so compare as string
            status_str = str(status).lower()
            valid_statuses = ['open', 'in_progress', 'completed', 'cancelled']
            if status_str in valid_statuses:
                query = query.filter(Task.status == status_str)
            else:
                return jsonify({'error': f'Invalid task status: {status}. Valid values: {", ".join(valid_statuses)}'}), 400
        
        # Apply priority filter
        if priority:
            # Task.priority is stored as String, not Enum, so compare as string
            priority_str = str(priority).lower()
            valid_priorities = ['low', 'medium', 'high', 'urgent']
            if priority_str in valid_priorities:
                query = query.filter(Task.priority == priority_str)
            else:
                return jsonify({'error': f'Invalid task priority: {priority}. Valid values: {", ".join(valid_priorities)}'}), 400
        
        # Apply assigned_to filter
        if assigned_to:
            try:
                assigned_id = int(assigned_to) if isinstance(assigned_to, str) and assigned_to.isdigit() else assigned_to
                if isinstance(assigned_id, int):
                    query = query.filter(Task.assigned_to == assigned_id)
            except (ValueError, TypeError):
                pass
        
        # Order by priority and due date
        # Priority is a string, so we need to order by a custom expression
        from sqlalchemy import case
        priority_order = case(
            (Task.priority == 'urgent', 1),
            (Task.priority == 'high', 2),
            (Task.priority == 'medium', 3),
            (Task.priority == 'low', 4),
            else_=5
        )
        # Order by priority and due date
        # MariaDB/MySQL doesn't support NULLS LAST, so we use a CASE expression to handle NULLs
        from sqlalchemy import case
        # For due_date: NULL values should come last, so we use a CASE to put NULLs at the end
        due_date_order = case(
            (Task.due_date.is_(None), 1),  # NULL dates get value 1 (will sort after non-NULL)
            else_=0  # Non-NULL dates get value 0 (will sort first)
        )
        query = query.order_by(
            priority_order.asc(),  # Lower number = higher priority
            due_date_order.asc(),  # Non-NULL dates first (0), then NULL dates (1)
            Task.due_date.asc(),   # Then order by actual date value
            Task.created_at.desc()  # Finally by creation date (newest first)
        )
        
        # Paginate and convert to dict
        tasks = query.paginate(page=page, per_page=per_page, error_out=False)
        
        task_dicts = []
        for task in tasks.items:
            try:
                task_dict = task.to_dict()
                if task_dict:  # Only add if dict is not None/empty
                    task_dicts.append(task_dict)
            except Exception as task_err:
                # Log the error but continue processing other tasks
                current_app.logger.warning(f"Failed to convert task {task.id} to dict: {str(task_err)}")
                # Add minimal task data so the frontend doesn't break
                try:
                    task_dicts.append({
                        'id': task.id,
                        'title': getattr(task, 'title', 'Unknown Task'),
                        'description': getattr(task, 'description', ''),
                        'priority': getattr(task, 'priority', 'medium'),
                        'status': getattr(task, 'status', 'open'),
                        'assigned_to': getattr(task, 'assigned_to', None),
                        'assigned_to_name': None,
                        'created_by': getattr(task, 'created_by', None),
                        'creator_name': None,
                        'due_date': task.due_date.isoformat() if hasattr(task, 'due_date') and task.due_date else None,
                        'created_at': task.created_at.isoformat() if hasattr(task, 'created_at') and task.created_at else None,
                        'updated_at': task.updated_at.isoformat() if hasattr(task, 'updated_at') and task.updated_at else None
                    })
                except:
                    continue  # Skip this task entirely if even minimal conversion fails
        
        return jsonify({
            'tasks': task_dicts,
            'total': tasks.total,
            'pages': tasks.pages,
            'current_page': page,
            'per_page': per_page,
            'has_next': tasks.has_next,
            'has_prev': tasks.has_prev
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Get tasks error: {str(e)}")
        return jsonify({'error': 'Failed to fetch tasks'}), 500

@task_bp.route('/<int:task_id>', methods=['GET'])
@jwt_required()
def get_task(task_id):
    """
    Get task by ID
    ---
    tags:
      - Tasks
    summary: Get a specific task by ID
    description: Retrieve a specific task by ID
    security:
      - Bearer: []
    parameters:
      - in: path
        name: task_id
        type: integer
        required: true
        description: The task ID
    responses:
      200:
        description: Task retrieved successfully
        schema:
          type: object
          properties:
            task:
              type: object
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Task not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        task = Task.query.get(task_id)
        if not task:
            return jsonify({'error': 'Task not found'}), 404
        
        # Get user role (handle both enum and string)
        if isinstance(current_user.role, UserRole):
            user_role_str = current_user.role.value.upper()
        else:
            user_role_str = str(current_user.role).upper()
        
        # Check permissions
        if user_role_str == 'TENANT':
            if task.assigned_to != current_user.id and task.tenant_id != current_user.id:
                return jsonify({'error': 'Access denied'}), 403
        elif user_role_str == 'STAFF':
            # Staff can view tasks assigned to them
            if task.assigned_to != current_user.id:
                return jsonify({'error': 'Access denied. You can only view tasks assigned to you.'}), 403
        elif user_role_str in ['PROPERTY_MANAGER', 'MANAGER', 'ADMIN']:
            # CRITICAL: Verify property ownership for property managers
            from routes.auth_routes import get_property_id_from_request
            property_id = get_property_id_from_request()
            if not property_id:
                from flask_jwt_extended import get_jwt
                try:
                    property_id = get_jwt().get('property_id')
                except Exception:
                    pass
            
            if property_id:
                # Verify task belongs to this property through unit or tenant
                # Use raw SQL to avoid enum validation errors
                from sqlalchemy import text
                task_belongs_to_property = False
                
                if task.unit_id:
                    try:
                        # Check unit's property_id using raw SQL
                        result = db.session.execute(
                            text("SELECT property_id FROM units WHERE id = :unit_id"),
                            {'unit_id': task.unit_id}
                        ).first()
                        if result and result[0] == property_id:
                            task_belongs_to_property = True
                    except Exception as unit_check_error:
                        current_app.logger.warning(f"Error checking unit property: {str(unit_check_error)}")
                        # If check fails, deny access for security
                        pass
                
                if not task_belongs_to_property and task.tenant_id:
                    try:
                        # Check tenant's property_id using raw SQL
                        result = db.session.execute(
                            text("SELECT property_id FROM tenants WHERE id = :tenant_id"),
                            {'tenant_id': task.tenant_id}
                        ).first()
                        if result and result[0] == property_id:
                            task_belongs_to_property = True
                    except Exception as tenant_check_error:
                        current_app.logger.warning(f"Error checking tenant property: {str(tenant_check_error)}")
                        # If check fails, deny access for security
                        pass
                
                if not task_belongs_to_property:
                    return jsonify({
                        'error': 'Access denied. This task does not belong to your property.',
                        'code': 'PROPERTY_ACCESS_DENIED'
                    }), 403
        
        task_dict = task.to_dict()
        return jsonify({'task': task_dict}), 200
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        current_app.logger.error(f"Get task error: {str(e)}\n{error_trace}")
        return jsonify({
            'error': 'Failed to fetch task',
            'details': str(e) if current_app.config.get('DEBUG', False) else None
        }), 500

@task_bp.route('/', methods=['POST'])
@jwt_required()
def create_task():
    """
    Create task
    ---
    tags:
      - Tasks
    summary: Create a new task
    description: Create a new task. Property Manager or Staff can create tasks.
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - title
            - description
          properties:
            title:
              type: string
            description:
              type: string
            priority:
              type: string
              enum: [low, medium, high, urgent]
            status:
              type: string
              enum: [pending, in_progress, completed, cancelled]
            assigned_to:
              type: integer
            due_date:
              type: string
              format: date
    responses:
      201:
        description: Task created successfully
        schema:
          type: object
          properties:
            message:
              type: string
            task:
              type: object
      400:
        description: Validation error
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Get user role (handle both enum and string)
        if isinstance(current_user.role, UserRole):
            user_role = current_user.role.value.upper()
        else:
            user_role = str(current_user.role).upper()
        
        # Only property managers, admins and staff can create tasks
        # Handle both MANAGER and PROPERTY_MANAGER roles
        if user_role not in ['PROPERTY_MANAGER', 'MANAGER', 'STAFF', 'ADMIN']:
            return jsonify({'error': 'Access denied'}), 403
        
        # CRITICAL: For property managers, verify property ownership
        if user_role in ['PROPERTY_MANAGER', 'MANAGER', 'ADMIN']:
            from routes.auth_routes import get_property_id_from_request
            property_id = get_property_id_from_request()
            if not property_id:
                from flask_jwt_extended import get_jwt
                try:
                    property_id = get_jwt().get('property_id')
                except Exception:
                    pass
            
            if not property_id:
                return property_context_required()
            
            # Verify property ownership
            from models.property import Property
            property_obj = Property.query.get(property_id)
            if not property_obj:
                return property_not_found()
            
            if property_obj.owner_id != current_user.id and not is_super_admin(current_user.id):
                return property_access_denied()
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Validate required fields
        required_fields = ['title', 'description']
        for field in required_fields:
            if not data.get(field, '').strip():
                return jsonify({'error': f'{field.title()} is required'}), 400
        
        # Validate priority
        priority = TaskPriority.MEDIUM  # default
        if data.get('priority'):
            try:
                priority = TaskPriority(data['priority'])
            except ValueError:
                return jsonify({'error': f'Invalid priority: {data["priority"]}'}), 400
        
        # Parse due date
        due_date = None
        if data.get('due_date'):
            try:
                due_date = datetime.fromisoformat(data['due_date'].replace('Z', '+00:00'))
            except ValueError:
                return jsonify({'error': 'Invalid due date format. Use ISO format.'}), 400
        
        # Validate assigned user
        assigned_to = None
        if data.get('assigned_to'):
            if str(data['assigned_to']).isdigit():
                assigned_user = User.query.get(int(data['assigned_to']))
                if not assigned_user:
                    return jsonify({'error': 'Assigned user not found'}), 400
                assigned_to = assigned_user.id
        
        # Create task
        task = Task(
            title=data['title'].strip(),
            description=data['description'].strip(),
            priority=priority,
            status=TaskStatus.OPEN,  # Always start as open
            due_date=due_date,
            assigned_to=assigned_to,
            created_by=current_user.id,
            tenant_id=data.get('tenant_id') if data.get('tenant_id') and str(data.get('tenant_id')).isdigit() else None,
            unit_id=data.get('unit_id') if data.get('unit_id') and str(data.get('unit_id')).isdigit() else None
        )
        
        db.session.add(task)
        db.session.commit()
        
        # Notify assigned staff member if task is assigned
        if task.assigned_to:
            try:
                from services.notification_service import NotificationService
                NotificationService.notify_staff_task_assigned(task, task.assigned_to)
            except Exception as notif_error:
                current_app.logger.warning(f"Failed to create notification for task {task.id}: {str(notif_error)}")
        
        current_app.logger.debug(f"Task created: {task.id} by user {current_user.id}")
        
        return jsonify({
            'message': 'Task created successfully',
            'task': task.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Create task error: {str(e)}")
        return jsonify({'error': 'Failed to create task'}), 500

@task_bp.route('/<int:task_id>', methods=['PUT'])
@jwt_required()
def update_task(task_id):
    """
    Update task
    ---
    tags:
      - Tasks
    summary: Update a task
    description: Update a task. Property Manager or assigned Staff can update tasks.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: task_id
        type: integer
        required: true
        description: The task ID
      - in: body
        name: body
        schema:
          type: object
          properties:
            title:
              type: string
            description:
              type: string
            priority:
              type: string
              enum: [low, medium, high, urgent]
            status:
              type: string
              enum: [pending, in_progress, completed, cancelled]
            assigned_to:
              type: integer
            due_date:
              type: string
              format: date
    responses:
      200:
        description: Task updated successfully
        schema:
          type: object
          properties:
            message:
              type: string
            task:
              type: object
      400:
        description: Validation error
      401:
        description: Unauthorized
      403:
        description: Access denied
      404:
        description: Task not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        task = Task.query.get(task_id)
        if not task:
            return jsonify({'error': 'Task not found'}), 404
        
        # Get user role (handle both enum and string)
        if isinstance(current_user.role, UserRole):
            user_role_str = current_user.role.value.upper()
        else:
            user_role_str = str(current_user.role).upper()
        
        # Check permissions - property managers and staff can update any task
        # Tenants can only update tasks assigned to them (and only certain fields)
        if user_role_str == 'TENANT':
            if task.assigned_to != current_user.id:
                return jsonify({'error': 'Access denied'}), 403
        elif user_role_str in ['PROPERTY_MANAGER', 'MANAGER', 'ADMIN']:
            # CRITICAL: Verify property ownership for property managers
            from routes.auth_routes import get_property_id_from_request
            property_id = get_property_id_from_request()
            if not property_id:
                from flask_jwt_extended import get_jwt
                try:
                    property_id = get_jwt().get('property_id')
                except Exception:
                    pass
            
            if property_id:
                # Verify task belongs to this property through unit or tenant
                # Use raw SQL to avoid enum validation errors
                from sqlalchemy import text
                task_belongs_to_property = False
                
                if task.unit_id:
                    try:
                        # Check unit's property_id using raw SQL
                        result = db.session.execute(
                            text("SELECT property_id FROM units WHERE id = :unit_id"),
                            {'unit_id': task.unit_id}
                        ).first()
                        if result and result[0] == property_id:
                            task_belongs_to_property = True
                    except Exception as unit_check_error:
                        current_app.logger.warning(f"Error checking unit property: {str(unit_check_error)}")
                        # If check fails, deny access for security
                        pass
                
                if not task_belongs_to_property and task.tenant_id:
                    try:
                        # Check tenant's property_id using raw SQL
                        result = db.session.execute(
                            text("SELECT property_id FROM tenants WHERE id = :tenant_id"),
                            {'tenant_id': task.tenant_id}
                        ).first()
                        if result and result[0] == property_id:
                            task_belongs_to_property = True
                    except Exception as tenant_check_error:
                        current_app.logger.warning(f"Error checking tenant property: {str(tenant_check_error)}")
                        # If check fails, deny access for security
                        pass
                
                if not task_belongs_to_property:
                    return jsonify({
                        'error': 'Access denied. This task does not belong to your property.',
                        'code': 'PROPERTY_ACCESS_DENIED'
                    }), 403
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Get user role (handle both enum and string)
        if isinstance(current_user.role, UserRole):
            user_role_str = current_user.role.value.upper()
        else:
            user_role_str = str(current_user.role).upper()
        
        # Update fields based on user role
        if user_role_str in ['PROPERTY_MANAGER', 'MANAGER', 'STAFF']:
            # Full update permissions
            if 'title' in data:
                task.title = data['title'].strip()
            
            if 'description' in data:
                task.description = data['description'].strip()
            
            if 'priority' in data:
                try:
                    task.priority = TaskPriority(data['priority'])
                except ValueError:
                    return jsonify({'error': f'Invalid priority: {data["priority"]}'}), 400
            
            if 'due_date' in data:
                if data['due_date']:
                    try:
                        task.due_date = datetime.fromisoformat(data['due_date'].replace('Z', '+00:00'))
                    except ValueError:
                        return jsonify({'error': 'Invalid due date format. Use ISO format.'}), 400
                else:
                    task.due_date = None
            
            if 'assigned_to' in data:
                old_assigned_to = task.assigned_to
                if data['assigned_to'] and str(data['assigned_to']).isdigit():
                    assigned_user = User.query.get(int(data['assigned_to']))
                    if not assigned_user:
                        return jsonify({'error': 'Assigned user not found'}), 400
                    task.assigned_to = assigned_user.id
                    # Notify newly assigned staff member
                    if task.assigned_to != old_assigned_to:
                        try:
                            from services.notification_service import NotificationService
                            NotificationService.notify_staff_task_assigned(task, task.assigned_to)
                        except Exception as notif_error:
                            current_app.logger.warning(f"Failed to create notification for task {task.id}: {str(notif_error)}")
                else:
                    task.assigned_to = None
            
            if 'tenant_id' in data:
                task.tenant_id = data['tenant_id'] if data['tenant_id'] and str(data['tenant_id']).isdigit() else None
            
            if 'unit_id' in data:
                task.unit_id = data['unit_id'] if data['unit_id'] and str(data['unit_id']).isdigit() else None
        
        # Status can be updated by both staff/property managers and assigned tenants
        if 'status' in data:
            try:
                # Normalize status value - handle both enum names and values
                status_value = str(data['status']).lower().strip()
                
                # Map common variations to correct enum values
                status_map = {
                    'in progress': 'in_progress',
                    'in-progress': 'in_progress',
                    'inprogress': 'in_progress'
                }
                if status_value in status_map:
                    status_value = status_map[status_value]
                
                # Validate and convert to enum
                new_status = TaskStatus(status_value)
                
                # Tenants can only change status to in_progress or completed
                if user_role_str == 'TENANT':
                    if new_status not in [TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED]:
                        return jsonify({'error': 'Tenants can only set status to in_progress or completed'}), 403
                
                # Store as string value (not enum object) since column is String type
                task.status = new_status.value
                
                # Set completion date when task is completed
                if new_status == TaskStatus.COMPLETED:
                    task.completed_at = datetime.now(timezone.utc)
                elif task.completed_at:  # Reset completion date if status changed from completed
                    task.completed_at = None
                    
            except ValueError as ve:
                current_app.logger.error(f"Invalid status value: {data['status']}, error: {str(ve)}")
                return jsonify({'error': f'Invalid status: {data["status"]}. Valid values: open, in_progress, completed, cancelled'}), 400
        
        db.session.commit()
        
        # Notify assigned staff member if task is assigned and was updated
        if task.assigned_to and ('title' in data or 'description' in data or 'priority' in data or 'due_date' in data):
            try:
                from services.notification_service import NotificationService
                NotificationService.notify_staff_task_updated(task, task.assigned_to)
            except Exception as notif_error:
                current_app.logger.warning(f"Failed to create notification for task {task.id}: {str(notif_error)}")
        
        current_app.logger.debug(f"Task updated: {task_id} by user {current_user.id}")
        
        return jsonify({
            'message': 'Task updated successfully',
            'task': task.to_dict()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Update task error: {str(e)}")
        return jsonify({'error': 'Failed to update task'}), 500

@task_bp.route('/<int:task_id>', methods=['DELETE'])
@jwt_required()
def delete_task(task_id):
    """
    Delete task
    ---
    tags:
      - Tasks
    summary: Delete a task
    description: Delete a task. Property Manager only.
    security:
      - Bearer: []
    parameters:
      - in: path
        name: task_id
        type: integer
        required: true
        description: The task ID
    responses:
      200:
        description: Task deleted successfully
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
        description: Task not found
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Only property managers and staff can delete tasks
        if current_user.role not in [UserRole.PROPERTY_MANAGER, UserRole.STAFF]:
            return jsonify({'error': 'Access denied'}), 403
        
        task = Task.query.get(task_id)
        if not task:
            return jsonify({'error': 'Task not found'}), 404
        
        # CRITICAL: For property managers, verify property ownership
        if current_user.role == UserRole.PROPERTY_MANAGER:
            from routes.auth_routes import get_property_id_from_request
            property_id = get_property_id_from_request()
            if not property_id:
                from flask_jwt_extended import get_jwt
                try:
                    property_id = get_jwt().get('property_id')
                except Exception:
                    pass
            
            if property_id:
                # Verify task belongs to this property through unit or tenant
                # Use raw SQL to avoid enum validation errors
                from sqlalchemy import text
                task_belongs_to_property = False
                
                if task.unit_id:
                    try:
                        # Check unit's property_id using raw SQL
                        result = db.session.execute(
                            text("SELECT property_id FROM units WHERE id = :unit_id"),
                            {'unit_id': task.unit_id}
                        ).first()
                        if result and result[0] == property_id:
                            task_belongs_to_property = True
                    except Exception as unit_check_error:
                        current_app.logger.warning(f"Error checking unit property: {str(unit_check_error)}")
                        # If check fails, deny access for security
                        pass
                
                if not task_belongs_to_property and task.tenant_id:
                    try:
                        # Check tenant's property_id using raw SQL
                        result = db.session.execute(
                            text("SELECT property_id FROM tenants WHERE id = :tenant_id"),
                            {'tenant_id': task.tenant_id}
                        ).first()
                        if result and result[0] == property_id:
                            task_belongs_to_property = True
                    except Exception as tenant_check_error:
                        current_app.logger.warning(f"Error checking tenant property: {str(tenant_check_error)}")
                        # If check fails, deny access for security
                        pass
                
                if not task_belongs_to_property:
                    return jsonify({
                        'error': 'Access denied. This task does not belong to your property.',
                        'code': 'PROPERTY_ACCESS_DENIED'
                    }), 403
        
        # Delete task
        db.session.delete(task)
        db.session.commit()
        
        current_app.logger.debug(f"Task deleted: {task_id} by user {current_user.id}")
        
        return jsonify({'message': 'Task deleted successfully'}), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete task error: {str(e)}")
        return jsonify({'error': 'Failed to delete task'}), 500

@task_bp.route('/stats', methods=['GET'])
@jwt_required()
def get_task_stats():
    """
    Get task statistics
    ---
    tags:
      - Tasks
    summary: Get task statistics
    description: Retrieve task statistics filtered by user's role and property
    security:
      - Bearer: []
    responses:
      200:
        description: Task statistics retrieved successfully
        schema:
          type: object
          properties:
            total:
              type: integer
            by_status:
              type: object
            by_priority:
              type: object
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'User not found'}), 404
        
        # Base query
        query = Task.query
        
        # Filter by user role
        if current_user.role == UserRole.TENANT:
            query = query.filter(
                (Task.assigned_to == current_user.id) |
                (Task.tenant_id == current_user.id)
            )
        elif current_user.role == UserRole.PROPERTY_MANAGER:
            # CRITICAL: Filter by property for property managers
            from routes.auth_routes import get_property_id_from_request
            property_id = get_property_id_from_request()
            if not property_id:
                from flask_jwt_extended import get_jwt
                try:
                    property_id = get_jwt().get('property_id')
                except Exception:
                    pass
            
            if property_id:
                from models.property import Unit
                from models.tenant import Tenant
                from sqlalchemy import or_
                
                unit_ids = [u[0] for u in db.session.query(Unit.id).filter(Unit.property_id == property_id).all()]
                tenant_ids = [t[0] for t in db.session.query(Tenant.id).filter(Tenant.property_id == property_id).all()]
                
                conditions = []
                if unit_ids:
                    conditions.append(Task.unit_id.in_(unit_ids))
                if tenant_ids:
                    conditions.append(Task.tenant_id.in_(tenant_ids))
                
                if conditions:
                    query = query.filter(or_(*conditions) if len(conditions) > 1 else conditions[0])
                else:
                    query = query.filter(Task.id == -1)  # No units/tenants, return empty
        
        # Count by status
        stats = {}
        for status in TaskStatus:
            stats[status.value] = query.filter(Task.status == status).count()
        
        # Count by priority
        priority_stats = {}
        for priority in TaskPriority:
            priority_stats[priority.value] = query.filter(Task.priority == priority).count()
        
        # Overdue tasks
        overdue_count = query.filter(
            Task.due_date < datetime.now(timezone.utc),
            Task.status != TaskStatus.COMPLETED
        ).count()
        
        return jsonify({
            'status_stats': stats,
            'priority_stats': priority_stats,
            'overdue_count': overdue_count,
            'total_tasks': query.count()
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"Get task stats error: {str(e)}")
        return jsonify({'error': 'Failed to fetch task statistics'}), 500

@task_bp.route('/enums', methods=['GET'])
@jwt_required()
def get_task_enums():
    """
    Get task enums
    ---
    tags:
      - Tasks
    summary: Get available task statuses and priorities
    description: Retrieve available task statuses and priorities
    security:
      - Bearer: []
    responses:
      200:
        description: Task enums retrieved successfully
        schema:
          type: object
          properties:
            statuses:
              type: array
              items:
                type: string
            priorities:
              type: array
              items:
                type: string
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    try:
        return jsonify({
            'statuses': [{'value': status.value, 'label': status.value.replace('_', ' ').title()} for status in TaskStatus],
            'priorities': [{'value': priority.value, 'label': priority.value.title()} for priority in TaskPriority]
        }), 200
    except Exception as e:
        current_app.logger.error(f"Get task enums error: {str(e)}")
        return jsonify({'error': 'Failed to fetch task enums'}), 500

@task_bp.route('/test', methods=['GET'])
def test_tasks():
    """Test endpoint for task functionality."""
    return jsonify({
        'status': 'ok',
        'message': 'Task routes are working',
        'task_count': Task.query.count(),
        'available_endpoints': [
            'GET /tasks/',
            'POST /tasks/',
            'GET /tasks/enums',
            'GET /tasks/stats',
            'GET /tasks/<id>',
            'PUT /tasks/<id>',
            'DELETE /tasks/<id>'
        ]
    }), 200
