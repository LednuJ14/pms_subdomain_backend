from flask import Flask, request, jsonify, current_app
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from flask_mail import Mail
from flasgger import Swagger
from config.config import config
import os

# Initialize extensions
db = SQLAlchemy()
migrate = Migrate()
jwt = JWTManager()
mail = Mail()
swagger = Swagger()

def create_app(config_name=None):
    """Application factory pattern."""
    app = Flask(__name__)
    
    # Load configuration
    config_name = config_name or os.environ.get('FLASK_ENV', 'development')
    app.config.from_object(config[config_name])
    
    # Initialize Cloudinary
    import cloudinary
    cloudinary.config(
        cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
        api_key=os.environ.get('CLOUDINARY_API_KEY'),
        api_secret=os.environ.get('CLOUDINARY_API_SECRET')
    )
    
    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    mail.init_app(app)
    
    # JWT configuration - ensure all identities are treated as strings
    @jwt.user_identity_loader
    def user_identity_lookup(user_id):
        return str(user_id)
    
    @jwt.user_lookup_loader
    def user_lookup_callback(_jwt_header, jwt_data):
        identity = jwt_data["sub"]
        # Convert string identity back to int for database lookup
        try:
            user_id = int(identity)
            from models.user import User
            return User.query.filter_by(id=user_id).one_or_none()
        except (ValueError, TypeError):
            return None
    
    # Configure CORS - Must be before registering blueprints
    # Allow all localhost origins including subdomains
    import re
    
    # List of explicitly allowed origins (Flask-CORS will use this)
    allowed_origins_list = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://localhost:8081",
        "http://admin.localhost:8080",  # Explicitly add admin subdomain
        "http://admin.localhost:5173",  # Explicitly add admin subdomain for Vite
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8080",
        "http://127.0.0.1:8081",
    ]

    # Regex pattern for localhost subdomains (for manual validation)
    localhost_regex = re.compile(r'https?://([a-zA-Z0-9-]+\.)?localhost(:\d+)?')

    # Regex pattern for dev tunnel URLs (e.g., *.devtunnels.ms, *.asse.devtunnels.ms)
    devtunnel_regex = re.compile(r'https?://[a-zA-Z0-9-]+\.(devtunnels\.ms|asse\.devtunnels\.ms)(/.*)?')

    # Regex pattern for production vicirotechnologies.com subdomains
    viciro_regex = re.compile(r'https?://([a-zA-Z0-9-]+\.)*vicirotechnologies\.com')

    # Validator function for manual checks (used in error handlers)
    def cors_origin_validator(origin):
        """Validate if origin is allowed (localhost with any subdomain, dev tunnels, or production)."""
        if not origin:
            return False
        if localhost_regex.match(origin):
            return True
        if devtunnel_regex.match(origin):
            return True
        if viciro_regex.match(origin):
            return True
        return origin in allowed_origins_list

    # Use a list of origins - Flask-CORS will handle regex patterns in the list
    cors_origins = allowed_origins_list.copy()

    # Add regex pattern for localhost subdomains
    cors_origins.append(r'https?://([a-zA-Z0-9-]+\.)?localhost(:\d+)?')

    # Add regex pattern for dev tunnels
    cors_origins.append(r'https?://[a-zA-Z0-9-]+\.(devtunnels\.ms|asse\.devtunnels\.ms)(/.*)?')

    # Add regex pattern for production *.vicirotechnologies.com
    cors_origins.append(r'https?://([a-zA-Z0-9-]+\.)*vicirotechnologies\.com')
    
    CORS(app, 
         origins=cors_origins,  # Use list with regex patterns
         methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
         allow_headers=["Content-Type", "Authorization", "Access-Control-Allow-Credentials", "X-Subdomain", "X-Property-ID"],
         supports_credentials=True)
    
    # Handle preflight requests globally and allow subdomain origins
    @app.before_request
    def handle_preflight():
        if request.method == "OPTIONS":
            response = app.make_default_options_response()
            headers = response.headers
            origin = request.headers.get('Origin', '')
            # Allow localhost with any subdomain using validator
            if origin and cors_origin_validator(origin):
                headers['Access-Control-Allow-Origin'] = origin
                headers['Access-Control-Allow-Credentials'] = 'true'
            headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
            headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Subdomain, X-Property-ID'
            headers['Access-Control-Max-Age'] = '86400'
            return response
    
    # Also handle CORS for actual requests (including error responses)
    # This ensures CORS headers are added even when Flask-CORS doesn't handle it
    @app.after_request
    def after_request(response):
        origin = request.headers.get('Origin', '')
        # Allow localhost with any subdomain using validator
        if origin and cors_origin_validator(origin):
            # Override Flask-CORS headers to allow subdomains
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Access-Control-Allow-Credentials'] = 'true'
        # Always add these headers
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Subdomain, X-Property-ID'
        return response
    
    # Register SQLAlchemy event listeners for automatic tenant registration
    from sqlalchemy import event
    from models.tenant import TenantUnit
    
    @event.listens_for(TenantUnit, 'after_insert')
    def tenant_unit_created(mapper, connection, target):
        """
        Automatically register tenant to property when TenantUnit is created.
        This ensures tenants can login to the property subdomain.
        """
        try:
            # Get the unit and property
            from models.property import Unit, Property
            unit = Unit.query.get(target.unit_id)
            if unit and unit.property_id:
                # Tenant is now automatically registered to the property
                # through the TenantUnit -> Unit -> Property relationship
                # No additional table needed - we can query this relationship
                current_app.logger.info(
                    f"Tenant {target.tenant_id} automatically registered to property {unit.property_id} "
                    f"via unit {target.unit_id}"
                )
        except Exception as e:
            current_app.logger.warning(f"Error in tenant_unit_created event: {str(e)}")
            # Don't raise - this is just logging, don't crash the system
    
    @event.listens_for(TenantUnit, 'after_update')
    def tenant_unit_updated(mapper, connection, target):
        """
        Handle tenant unit updates (e.g., when lease becomes active/inactive).
        """
        try:
            if hasattr(target, 'is_active'):
                from models.property import Unit
                unit = Unit.query.get(target.unit_id)
                if unit:
                    status = "activated" if target.is_active else "deactivated"
                    current_app.logger.info(
                        f"Tenant {target.tenant_id} lease {status} for property {unit.property_id}"
                    )
        except Exception as e:
            current_app.logger.warning(f"Error in tenant_unit_updated event: {str(e)}")
    
    # Register blueprints
    from routes.auth_routes import auth_bp
    from routes.user_routes import user_bp
    from routes.property_routes import property_bp
    from routes.tenant_routes import tenant_bp
    from routes.staff_routes import staff_bp
    from routes.billing_routes import billing_bp
    from routes.request_routes import request_bp
    from routes.announcement_routes import announcement_bp
    from routes.document_routes import document_bp
    from routes.task_routes import task_bp
    from routes.analytics_routes import analytics_bp
    from routes.feedback_routes import feedback_bp
    from routes.notification_routes import notification_bp
    from routes.chat_routes import chat_bp
    from routes.contract_routes import contract_bp
    
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(user_bp, url_prefix='/api/users')
    app.register_blueprint(property_bp, url_prefix='/api/properties')
    app.register_blueprint(tenant_bp, url_prefix='/api/tenants')
    app.register_blueprint(staff_bp, url_prefix='/api/staff')
    app.register_blueprint(billing_bp, url_prefix='/api/billing')
    app.register_blueprint(request_bp, url_prefix='/api/requests')
    app.register_blueprint(announcement_bp, url_prefix='/api/announcements')
    app.register_blueprint(document_bp, url_prefix='/api/documents')
    app.register_blueprint(task_bp, url_prefix='/api/tasks')
    app.register_blueprint(analytics_bp, url_prefix='/api/analytics')
    app.register_blueprint(feedback_bp, url_prefix='/api/feedback')
    app.register_blueprint(notification_bp, url_prefix='/api/notifications')
    app.register_blueprint(chat_bp, url_prefix='/api/chats')
    app.register_blueprint(contract_bp, url_prefix='/api/contracts')

    # Initialize reminder scheduler (optional - can be disabled for testing)
    # Set ENABLE_AUTO_REMINDERS=true in environment to enable automatic daily reminders
    if not app.config.get('TESTING') and app.config.get('ENABLE_AUTO_REMINDERS', False):
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
            import atexit
            
            scheduler = BackgroundScheduler()
            
            def send_reminders_job():
                """Background job to send all reminders."""
                with app.app_context():
                    try:
                        from services.reminder_service import ReminderService
                        results = ReminderService.send_all_reminders()
                        app.logger.info(f"Auto-reminders sent successfully: {results}")
                    except Exception as e:
                        app.logger.error(f"Error in auto-reminder job: {str(e)}", exc_info=True)
            
            # Schedule to run daily at 9:00 AM
            scheduler.add_job(
                send_reminders_job,
                trigger=CronTrigger(hour=9, minute=0),
                id='daily_reminders',
                name='Send daily reminders',
                replace_existing=True
            )
            
            scheduler.start()
            app.scheduler = scheduler
            
            # Shutdown scheduler on app exit
            atexit.register(lambda: scheduler.shutdown())
            
            app.logger.info("✅ Reminder scheduler initialized - reminders will run daily at 9:00 AM")
        except ImportError:
            app.logger.warning("⚠️ APScheduler not installed. Auto-reminders disabled. Install with: pip install apscheduler")
        except Exception as e:
            app.logger.warning(f"⚠️ Failed to initialize reminder scheduler: {str(e)}. Reminders can still be triggered manually via API.")
    
    # Configure Swagger / OpenAPI documentation
    # This is scoped to /api routes only and should not affect existing behavior.
    swagger_template = {
        "swagger": "2.0",
        "info": {
            "title": "PMS Property Management System Sub-domain API",
            "description": "Interactive API documentation for the sub-domain backend.\n\n"
                           "Note: This documentation is generated automatically from the existing Flask routes "
                           "and may not include every detail of request/response payloads.",
            "version": "1.0.0",
        },
        "basePath": "/",
        "schemes": ["http", "https"],
        "securityDefinitions": {
            "Bearer": {
                "type": "apiKey",
                "name": "Authorization",
                "in": "header",
                "description": "JWT Authorization header using the Bearer scheme. Example: \"Authorization: Bearer {token}\""
            }
        },
        "security": [
            {
                "Bearer": []
            }
        ]
    }

    swagger_config = {
        "headers": [],
        "specs": [
            {
                "endpoint": "apispec_subdomain",
                "route": "/api/swagger.json",
                # Limit to API routes only so other Flask endpoints are untouched
                "rule_filter": lambda rule: rule.rule.startswith("/api/"),
                "model_filter": lambda tag: True,
            }
        ],
        "static_url_path": "/flasgger_static",
        "swagger_ui": True,
        # Swagger UI will be served at /api/docs/
        "specs_route": "/api/docs/",
    }

    Swagger(app, template=swagger_template, config=swagger_config)

    # Create upload directories
    upload_dir = os.path.join(app.instance_path, app.config['UPLOAD_FOLDER'])
    os.makedirs(upload_dir, exist_ok=True)
    
    # JWT Error Handlers - Must be after CORS setup
    @jwt.expired_token_loader
    def expired_token_callback(jwt_header, jwt_payload):
        return jsonify({'error': 'Token has expired', 'code': 'TOKEN_EXPIRED'}), 401
    
    @jwt.invalid_token_loader
    def invalid_token_callback(error):
        return jsonify({'error': 'Invalid token', 'code': 'INVALID_TOKEN'}), 401
    
    @jwt.unauthorized_loader
    def missing_token_callback(error):
        return jsonify({'error': 'Authorization token is missing', 'code': 'MISSING_TOKEN'}), 401
    
    # Error handlers
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({'error': 'Resource not found'}), 404

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({'error': 'Internal server error'}), 500
    
    # Handle all exceptions to ensure CORS headers are added
    @app.errorhandler(Exception)
    def handle_exception(e):
        # Log the error
        current_app.logger.error(f"Unhandled exception: {str(e)}", exc_info=True)
        
        # For tenant routes, return 200 with empty tenants array to prevent CORS issues
        # This allows the frontend to handle errors gracefully
        if request.path and '/tenants' in request.path and request.method == 'GET':
            response = jsonify({
                'tenants': [],
                'error': 'Failed to load tenants',
                'error_details': str(e) if current_app.config.get('DEBUG', False) else None
            })
            response.status_code = 200
        else:
            # Return error response with CORS headers for other routes
            response = jsonify({'error': 'An error occurred', 'message': str(e) if current_app.config.get('DEBUG') else 'Internal server error'})
            response.status_code = 500
        
        # Add CORS headers manually - ALWAYS add them, even on errors
        origin = request.headers.get('Origin', '')
        # Always add CORS headers, even if origin validation fails (for debugging)
        if origin:
            # Validate origin, but allow common localhost patterns
            if cors_origin_validator(origin) or 'localhost' in origin.lower():
                response.headers['Access-Control-Allow-Origin'] = origin
                response.headers['Access-Control-Allow-Credentials'] = 'true'
            else:
                # Still add CORS headers for localhost-like origins even if not in validator
                if 'localhost' in origin.lower() or '127.0.0.1' in origin:
                    response.headers['Access-Control-Allow-Origin'] = origin
                    response.headers['Access-Control-Allow-Credentials'] = 'true'
        else:
            # If no origin, allow all (for debugging - restrict in production)
            response.headers['Access-Control-Allow-Origin'] = '*'
        
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Subdomain, X-Property-ID'
        
        return response
    
    # Health check endpoint
    @app.route('/api/health')
    def health_check():
        return {'status': 'healthy', 'message': 'PMS Property Management System API is running'}, 200
    
    return app