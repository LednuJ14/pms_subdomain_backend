#!/usr/bin/env python3
"""
PMS Property Management System Backend
Main application entry point
"""

from app import create_app, db
from flask_migrate import upgrade
from flask import jsonify
import os

# Create Flask application
app = create_app()

if __name__ == '__main__':
    # Auto-apply database migrations or create tables if they don't exist
    with app.app_context():
        # Always create missing tables first
        db.create_all()
        print("✓ Database base tables verified/created.")
        
        try:
            # Then apply any schema migrations if they exist
            upgrade()
            print("✓ Database schema updated via migrations.")
        except Exception as e:
            print(f"Migration error (this is fine if no migrations exist): {e}")

    # Run the application
    # Use a distinct default port to avoid clashing with main-domain backend
    port = int(os.environ.get('PORT', 5001))
    debug = os.environ.get('FLASK_ENV') == 'development'
    
    print("="*50)
    print("🏢 PMS Property Management System")
    print("🚀 Starting Flask Backend Server...")
    print(f"📡 Running on: http://localhost:{port}")
    print(f"🔧 Environment: {os.environ.get('FLASK_ENV', 'development')}")
    print(f"🐛 Debug Mode: {debug}")
    
    # Check if auto-reminders are enabled
    enable_reminders = os.environ.get('ENABLE_AUTO_REMINDERS', 'false').lower() in ['true', 'on', '1']
    if enable_reminders:
        print("⏰ Auto-Reminders: ✅ ENABLED (will run daily at 9:00 AM)")
    else:
        print("⏰ Auto-Reminders: ❌ DISABLED (set ENABLE_AUTO_REMINDERS=true to enable)")
    
    print("="*50)
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=debug
    )