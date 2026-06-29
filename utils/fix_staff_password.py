"""
Utility script to fix/rehash staff passwords.
Run this script directly to fix a staff account password.

Usage:
    python fix_staff_password.py <email_or_username> <new_password>

Example:
    python fix_staff_password.py andi "Andi!23"
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from models.user import User
import bcrypt

def fix_staff_password(email_or_username, new_password):
    """Fix password for a staff account."""
    app = create_app()
    
    with app.app_context():
        # Find user by email or username
        user = User.query.filter(
            (User.email == email_or_username.lower()) | 
            (User.username == email_or_username.lower())
        ).first()
        
        if not user:
            print(f"ERROR: User not found with email/username: {email_or_username}")
            return False
        
        print(f"Found user: id={user.id}, email={user.email}, username={user.username}, role={user.role}")
        
        # Check if user is staff
        if not user.is_staff():
            print(f"WARNING: User is not a staff member (role: {user.role})")
            response = input("Continue anyway? (yes/no): ")
            if response.lower() != 'yes':
                print("Aborted.")
                return False
        
        # Check current password hash
        old_hash = user.password_hash
        print(f"Current password_hash: {old_hash[:30] + '...' if old_hash and len(old_hash) > 30 else old_hash}")
        
        if old_hash:
            is_bcrypt = old_hash.startswith('$2b$') or old_hash.startswith('$2a$') or old_hash.startswith('$2y$')
            print(f"Current hash is bcrypt format: {is_bcrypt}")
        
        # Set new password (will be hashed automatically)
        user.set_password(new_password)
        
        # Commit to database
        db.session.commit()
        
        print(f"✓ Password updated successfully!")
        print(f"New password_hash: {user.password_hash[:30] + '...'}")
        
        # Verify the password works
        if user.check_password(new_password):
            print("✓ Password verification successful!")
            return True
        else:
            print("✗ ERROR: Password verification failed after update!")
            return False

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    
    email_or_username = sys.argv[1]
    new_password = sys.argv[2]
    
    print(f"Fixing password for: {email_or_username}")
    print("=" * 50)
    
    success = fix_staff_password(email_or_username, new_password)
    
    if success:
        print("=" * 50)
        print("Password fix completed successfully!")
        print(f"You can now login with email/username: {email_or_username}")
        print(f"Password: {new_password}")
        sys.exit(0)
    else:
        print("=" * 50)
        print("Password fix failed!")
        sys.exit(1)

