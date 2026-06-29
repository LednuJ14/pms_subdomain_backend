from flask import current_app
from flask_mail import Message
from app import mail

def send_password_reset_email(email, first_name, reset_token):
    """Send password reset email to user."""
    try:
        subject = "Reset Your JACS Property Management Password"
        
        # Frontend URL for password reset
        reset_url = f"http://localhost:8080/login?token={reset_token}"
        
        html_body = f"""
        <html>
        <head>
            <style>
                .container {{ max-width: 600px; margin: 0 auto; font-family: Arial, sans-serif; }}
                .header {{ background-color: #3b82f6; color: white; padding: 20px; text-align: center; }}
                .content {{ padding: 20px; background-color: #f9f9f9; }}
                .button {{ display: inline-block; background-color: #3b82f6; color: white; 
                          padding: 12px 24px; text-decoration: none; border-radius: 5px; margin: 20px 0; }}
                .footer {{ padding: 20px; text-align: center; color: #666; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>JACS Property Management</h1>
                </div>
                <div class="content">
                    <h2>Password Reset Request</h2>
                    <p>Hi {first_name},</p>
                    <p>We received a request to reset your password for your JACS Property Management account.</p>
                    <p>Click the button below to reset your password:</p>
                    <a href="{reset_url}" class="button">Reset Password</a>
                    <p>If the button doesn't work, copy and paste this link into your browser:</p>
                    <p style="word-break: break-all; color: #3b82f6;">{reset_url}</p>
                    <p><strong>This link will expire in 1 hour.</strong></p>
                    <p>If you didn't request this password reset, please ignore this email or contact support if you have concerns.</p>
                </div>
                <div class="footer">
                    <p>© 2025 JACS Property Management System. All rights reserved.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        text_body = f"""
        JACS Property Management - Password Reset
        
        Hi {first_name},
        
        We received a request to reset your password for your JACS Property Management account.
        
        Click the following link to reset your password:
        {reset_url}
        
        This link will expire in 1 hour.
        
        If you didn't request this password reset, please ignore this email.
        
        © 2025 JACS Property Management System
        """
        
        msg = Message(
            subject=subject,
            sender=current_app.config['MAIL_USERNAME'],
            recipients=[email],
            body=text_body,
            html=html_body
        )
        
        mail.send(msg)
        return True
        
    except Exception as e:
        current_app.logger.error(f"Failed to send password reset email: {str(e)}")
        return False

def send_welcome_email(email, first_name, role):
    """Send welcome email to new users."""
    try:
        subject = "Welcome to JACS Property Management System"
        
        html_body = f"""
        <html>
        <head>
            <style>
                .container {{ max-width: 600px; margin: 0 auto; font-family: Arial, sans-serif; }}
                .header {{ background-color: #10b981; color: white; padding: 20px; text-align: center; }}
                .content {{ padding: 20px; background-color: #f9f9f9; }}
                .footer {{ padding: 20px; text-align: center; color: #666; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Welcome to JACS!</h1>
                </div>
                <div class="content">
                    <h2>Account Created Successfully</h2>
                    <p>Hi {first_name},</p>
                    <p>Welcome to JACS Property Management System! Your account has been successfully created.</p>
                    <p><strong>Your Role:</strong> {role.replace('_', ' ').title()}</p>
                    <p>You can now log in to access your dashboard and start managing your property-related tasks.</p>
                    <p>If you have any questions or need assistance, please don't hesitate to contact our support team.</p>
                </div>
                <div class="footer">
                    <p>© 2025 JACS Property Management System. All rights reserved.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        msg = Message(
            subject=subject,
            sender=current_app.config['MAIL_USERNAME'],
            recipients=[email],
            html=html_body
        )
        
        mail.send(msg)
        return True
        
    except Exception as e:
        current_app.logger.error(f"Failed to send welcome email: {str(e)}")
        return False