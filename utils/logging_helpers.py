"""
Logging helpers for structured logging across the sub-domain system.
"""

from flask import current_app, request
from flask_jwt_extended import get_jwt_identity


def log_property_access_attempt(user_id, property_id, subdomain=None, action=None, success=True):
    """
    Log property access attempts for security auditing.
    
    Args:
        user_id: ID of the user attempting access
        property_id: ID of the property being accessed
        subdomain: Subdomain from the request (optional)
        action: Action being performed (optional)
        success: Whether the access was successful (default: True)
    """
    try:
        # Extract subdomain from request if not provided
        if not subdomain:
            origin = request.headers.get('Origin', '')
            host = request.headers.get('Host', '')
            if origin or host:
                import re
                subdomain_match = re.search(r'([a-zA-Z0-9-]+)\.localhost', origin or host)
                if subdomain_match:
                    subdomain = subdomain_match.group(1).lower()
        
        status = 'SUCCESS' if success else 'DENIED'
        action_str = f" - Action: {action}" if action else ""
        
        current_app.logger.info(
            f"Property access attempt: user_id={user_id}, property_id={property_id}, "
            f"subdomain={subdomain or 'N/A'}, status={status}{action_str}"
        )
    except Exception as e:
        # Don't fail the request if logging fails
        current_app.logger.warning(f"Failed to log property access attempt: {str(e)}")


def log_property_operation(user_id, property_id, operation, resource_type=None, resource_id=None):
    """
    Log property-related operations for auditing.
    
    Args:
        user_id: ID of the user performing the operation
        property_id: ID of the property
        operation: Operation being performed (e.g., 'create', 'update', 'delete', 'read')
        resource_type: Type of resource (e.g., 'task', 'tenant', 'document')
        resource_id: ID of the resource (optional)
    """
    try:
        resource_info = f", resource={resource_type}" if resource_type else ""
        resource_id_info = f":{resource_id}" if resource_id else ""
        
        current_app.logger.info(
            f"Property operation: user_id={user_id}, property_id={property_id}, "
            f"operation={operation}{resource_info}{resource_id_info}"
        )
    except Exception as e:
        current_app.logger.warning(f"Failed to log property operation: {str(e)}")

