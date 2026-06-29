"""
Centralized error response helpers for consistent error handling across all routes.
This ensures uniform error messages and error codes throughout the sub-domain system.
"""

from flask import jsonify


def property_context_required():
    """
    Return a standardized error response when property context is missing.
    This error occurs when a property manager tries to access an endpoint
    without providing property_id through subdomain, header, or JWT.
    """
    return jsonify({
        'error': 'Property context is required. Please access through a property subdomain.',
        'code': 'PROPERTY_CONTEXT_REQUIRED'
    }), 400


def property_access_denied():
    """
    Return a standardized error response when property access is denied.
    This error occurs when a property manager tries to access a property they don't own.
    """
    return jsonify({
        'error': 'Access denied. You do not own this property.',
        'code': 'PROPERTY_ACCESS_DENIED'
    }), 403


def property_not_found():
    """
    Return a standardized error response when property is not found.
    """
    return jsonify({
        'error': 'Property not found.',
        'code': 'PROPERTY_NOT_FOUND'
    }), 404


def property_mismatch():
    """
    Return a standardized error response when property_id in request doesn't match subdomain.
    """
    return jsonify({
        'error': 'Property ID mismatch. Please access through the correct subdomain.',
        'code': 'PROPERTY_MISMATCH'
    }), 400


def user_not_found():
    """
    Return a standardized error response when user is not found.
    """
    return jsonify({
        'error': 'User not found.',
        'code': 'USER_NOT_FOUND'
    }), 404


def unauthorized():
    """
    Return a standardized error response for unauthorized access.
    """
    return jsonify({
        'error': 'Unauthorized access.',
        'code': 'UNAUTHORIZED'
    }), 401


def forbidden():
    """
    Return a standardized error response for forbidden access.
    """
    return jsonify({
        'error': 'Access denied.',
        'code': 'FORBIDDEN'
    }), 403

