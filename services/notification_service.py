"""
Notification Service for creating tenant notifications.
This service handles the creation of notifications for various events.
"""

from datetime import datetime, timezone
from app import db
from models.notification import Notification, NotificationType, NotificationPriority
from models.tenant import Tenant
from models.user import User
from flask import current_app

class NotificationService:
    """Service for creating and managing tenant notifications."""
    
    @staticmethod
    def create_notification(tenant_id, notification_type, title, message, **kwargs):
        """
        Create a notification for a tenant.
        
        Args:
            tenant_id: ID of the tenant
            notification_type: Type of notification (NotificationType enum or string)
            title: Notification title
            message: Notification message
            **kwargs: Additional fields (priority, related_entity_type, related_entity_id, action_url)
        
        Returns:
            Notification object or None if creation failed
        """
        try:
            # Get tenant to verify it exists and get user_id
            tenant = Tenant.query.get(tenant_id)
            if not tenant:
                current_app.logger.warning(f"Tenant {tenant_id} not found for notification creation")
                return None
            
            user_id = tenant.user_id
            
            # Create notification
            notification = Notification(
                user_id=user_id,
                notification_type=notification_type,
                title=title,
                message=message,
                tenant_id=tenant_id,
                recipient_type='tenant',
                **kwargs
            )
            
            db.session.add(notification)
            db.session.commit()
            
            current_app.logger.info(f"Created notification {notification.id} for tenant {tenant_id}")
            return notification
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error creating notification: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def create_pm_notification(property_manager_id, notification_type, title, message, **kwargs):
        """
        Create a notification for a property manager.
        
        Args:
            property_manager_id: ID of the property manager (user_id)
            notification_type: Type of notification (NotificationType enum or string)
            title: Notification title
            message: Notification message
            **kwargs: Additional fields (priority, related_entity_type, related_entity_id, action_url, tenant_id)
        
        Returns:
            Notification object or None if creation failed
        """
        try:
            user = User.query.get(property_manager_id)
            if not user:
                current_app.logger.warning(f"User {property_manager_id} not found for PM notification creation")
                return None
            
            notification = Notification(
                user_id=property_manager_id,
                notification_type=notification_type,
                title=title,
                message=message,
                recipient_type='property_manager',
                tenant_id=kwargs.get('tenant_id'),
                **{k: v for k, v in kwargs.items() if k != 'tenant_id'}
            )
            
            db.session.add(notification)
            db.session.commit()
            
            current_app.logger.info(f"Created PM notification {notification.id} for user {property_manager_id}")
            return notification
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error creating PM notification: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_pm_payment_submitted(payment):
        """Create notification when a tenant submits payment proof."""
        try:
            bill = payment.bill
            tenant = bill.tenant
            if not tenant:
                return None
            
            from models.property import Property
            property_obj = Property.query.get(bill.unit.property_id if bill.unit else None)
            if not property_obj:
                return None
            
            manager_user_id = getattr(property_obj, 'manager_id', None) or getattr(property_obj, 'owner_id', None)
            if not manager_user_id:
                return None
            
            title = f"Payment Proof Submitted"
            message = f"Tenant {tenant.user.full_name if tenant.user else 'Unknown'} submitted payment proof of ₱{payment.amount:,.2f} for bill '{bill.title}'. Please review and approve."
            
            priority = NotificationPriority.HIGH if bill.is_overdue else NotificationPriority.MEDIUM
            
            return NotificationService.create_pm_notification(
                property_manager_id=manager_user_id,
                notification_type=NotificationType.PAYMENT_SUBMITTED,
                title=title,
                message=message,
                priority=priority,
                related_entity_type='payment',
                related_entity_id=payment.id,
                tenant_id=tenant.id,
                action_url=f'/bills/{bill.id}'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_pm_payment_submitted: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_pm_new_request(request):
        """Create notification when a tenant creates a new maintenance request."""
        try:
            tenant = request.tenant
            if not tenant:
                return None
            
            from models.property import Property
            property_obj = Property.query.get(request.property_id)
            if not property_obj:
                return None
            
            manager_user_id = getattr(property_obj, 'manager_id', None) or getattr(property_obj, 'owner_id', None)
            if not manager_user_id:
                return None
            
            # Determine priority based on request priority
            priority_map = {
                'low': NotificationPriority.LOW,
                'medium': NotificationPriority.MEDIUM,
                'high': NotificationPriority.HIGH,
                'urgent': NotificationPriority.URGENT
            }
            priority = priority_map.get(request.priority.lower(), NotificationPriority.MEDIUM)
            
            title = f"New Maintenance Request"
            message = f"Tenant {tenant.user.full_name if tenant.user else 'Unknown'} submitted a new {request.priority} priority maintenance request: '{request.title}'."
            
            return NotificationService.create_pm_notification(
                property_manager_id=manager_user_id,
                notification_type=NotificationType.NEW_MAINTENANCE_REQUEST,
                title=title,
                message=message,
                priority=priority,
                related_entity_type='request',
                related_entity_id=request.id,
                tenant_id=tenant.id,
                action_url=f'/requests/{request.id}'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_pm_new_request: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_pm_feedback_submitted(feedback):
        """Create notification when a tenant submits feedback."""
        try:
            from models.property import Property
            property_obj = Property.query.get(feedback.property_id) if hasattr(feedback, 'property_id') and feedback.property_id else None
            if not property_obj:
                return None
            
            manager_user_id = getattr(property_obj, 'manager_id', None) or getattr(property_obj, 'owner_id', None)
            if not manager_user_id:
                return None
            
            tenant_name = "Unknown"
            tenant_id = None
            if hasattr(feedback, 'tenant_id') and feedback.tenant_id:
                tenant = Tenant.query.get(feedback.tenant_id)
                if tenant and tenant.user:
                    tenant_name = tenant.user.full_name
                    tenant_id = tenant.id
            
            title = f"New Feedback Submitted"
            message = f"{tenant_name} submitted feedback. Type: {getattr(feedback, 'feedback_type', 'General')}."
            
            return NotificationService.create_pm_notification(
                property_manager_id=manager_user_id,
                notification_type=NotificationType.FEEDBACK_SUBMITTED,
                title=title,
                message=message,
                priority=NotificationPriority.MEDIUM,
                related_entity_type='feedback',
                related_entity_id=feedback.id,
                tenant_id=tenant_id,
                action_url=f'/feedback/{feedback.id}'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_pm_feedback_submitted: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_pm_bill_overdue(bill):
        """Create notification for property manager when a bill becomes overdue."""
        try:
            tenant = bill.tenant
            if not tenant:
                return None
            
            from models.property import Property
            unit = bill.unit
            if not unit:
                return None
            
            property_obj = Property.query.get(unit.property_id)
            if not property_obj:
                return None
            
            manager_user_id = getattr(property_obj, 'manager_id', None) or getattr(property_obj, 'owner_id', None)
            if not manager_user_id:
                return None
            
            days_overdue = bill.days_overdue
            
            title = f"Overdue Bill Alert"
            message = f"Bill '{bill.title}' for tenant {tenant.user.full_name if tenant.user else 'Unknown'} is overdue by {days_overdue} day(s). Amount due: ₱{bill.amount_due:,.2f}."
            
            priority = NotificationPriority.URGENT if days_overdue > 7 else NotificationPriority.HIGH
            
            return NotificationService.create_pm_notification(
                property_manager_id=manager_user_id,
                notification_type=NotificationType.BILL_OVERDUE_ALERT,
                title=title,
                message=message,
                priority=priority,
                related_entity_type='bill',
                related_entity_id=bill.id,
                tenant_id=tenant.id,
                action_url=f'/bills/{bill.id}'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_pm_bill_overdue: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_bill_created(bill):
        """Create notification when a bill is created."""
        try:
            tenant = bill.tenant
            if not tenant:
                return None
            
            # Determine priority based on bill type and due date
            from datetime import date
            days_until_due = (bill.due_date - date.today()).days if bill.due_date else None
            priority = NotificationPriority.MEDIUM
            if days_until_due is not None and days_until_due <= 3:
                priority = NotificationPriority.HIGH
            elif days_until_due is not None and days_until_due <= 1:
                priority = NotificationPriority.URGENT
            
            title = f"New {bill.bill_type.title()} Bill"
            message = f"A new {bill.bill_type} bill has been issued: {bill.title}. Amount: ₱{bill.amount:,.2f}. Due date: {bill.due_date.strftime('%B %d, %Y')}."
            
            return NotificationService.create_notification(
                tenant_id=tenant.id,
                notification_type=NotificationType.BILL_CREATED,
                title=title,
                message=message,
                priority=priority,
                related_entity_type='bill',
                related_entity_id=bill.id,
                action_url=f'/tenant/bills/{bill.id}'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_bill_created: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_bill_overdue(bill):
        """Create notification when a bill becomes overdue."""
        try:
            tenant = bill.tenant
            if not tenant:
                return None
            
            days_overdue = bill.days_overdue
            
            title = f"Overdue Bill: {bill.title}"
            message = f"Your {bill.bill_type} bill '{bill.title}' is overdue by {days_overdue} day(s). Amount due: ₱{bill.amount_due:,.2f}. Please pay immediately."
            
            priority = NotificationPriority.URGENT if days_overdue > 7 else NotificationPriority.HIGH
            
            return NotificationService.create_notification(
                tenant_id=tenant.id,
                notification_type=NotificationType.BILL_OVERDUE,
                title=title,
                message=message,
                priority=priority,
                related_entity_type='bill',
                related_entity_id=bill.id,
                action_url=f'/tenant/bills/{bill.id}'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_bill_overdue: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_bill_paid(bill):
        """Create notification when a bill is paid."""
        try:
            tenant = bill.tenant
            if not tenant:
                return None
            
            title = f"Bill Paid: {bill.title}"
            message = f"Your {bill.bill_type} bill '{bill.title}' has been marked as paid. Thank you for your payment!"
            
            return NotificationService.create_notification(
                tenant_id=tenant.id,
                notification_type=NotificationType.BILL_PAID,
                title=title,
                message=message,
                priority=NotificationPriority.LOW,
                related_entity_type='bill',
                related_entity_id=bill.id,
                action_url=f'/tenant/bills/{bill.id}'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_bill_paid: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_payment_approved(payment):
        """Create notification when a payment is approved."""
        try:
            bill = payment.bill
            tenant = bill.tenant
            if not tenant:
                return None
            
            title = f"Payment Approved"
            base_message = f"Your payment of ₱{payment.amount:,.2f} for bill '{bill.title}' has been approved."
            
            # Add Statement information if bill is paid and we have period dates
            statement_info = ""
            try:
                # Check if bill is fully paid
                bill_status = str(bill.status).lower() if bill.status else 'pending'
                is_paid = bill_status == 'paid' or (hasattr(bill, 'amount_due') and bill.amount_due == 0)
                
                if is_paid:
                    start_date = None
                    end_date = None
                    bill_type = str(bill.bill_type).lower() if bill.bill_type else ''
                    
                    # For rent bills, try to get dates from tenant_units
                    if bill_type == 'rent':
                        try:
                            from models.tenant import TenantUnit
                            from sqlalchemy import text
                            from datetime import date
                            
                            # Get active tenant_unit for this tenant and unit
                            tenant_unit_result = db.session.execute(text(
                                """
                                SELECT tu.rent_start_date, tu.rent_end_date, tu.move_in_date, tu.move_out_date
                                FROM tenant_units tu
                                WHERE tu.tenant_id = :tenant_id 
                                  AND tu.unit_id = :unit_id
                                  AND (tu.move_out_date IS NULL OR tu.move_out_date >= CURDATE())
                                ORDER BY tu.created_at DESC
                                LIMIT 1
                                """
                            ), {
                                'tenant_id': tenant.id,
                                'unit_id': bill.unit_id
                            }).first()
                            
                            if tenant_unit_result:
                                # Use rent_start_date/rent_end_date first, then move_in_date/move_out_date
                                start_date = tenant_unit_result[0] or tenant_unit_result[2]  # rent_start_date or move_in_date
                                end_date = tenant_unit_result[1] or tenant_unit_result[3]  # rent_end_date or move_out_date
                        except Exception as tu_error:
                            current_app.logger.warning(f"Error getting tenant_units for statement: {str(tu_error)}")
                    
                    # Fallback to bill period dates
                    if not start_date or not end_date:
                        if bill.period_start:
                            start_date = bill.period_start
                        if bill.period_end:
                            end_date = bill.period_end
                    
                    # Format dates if available
                    if start_date and end_date:
                        # Ensure dates are date objects
                        if isinstance(start_date, str):
                            from datetime import datetime
                            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
                        if isinstance(end_date, str):
                            from datetime import datetime
                            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
                        
                        # Format as MM/DD/YYYY
                        start_formatted = start_date.strftime('%m/%d/%Y')
                        end_formatted = end_date.strftime('%m/%d/%Y')
                        statement_info = f"\n\nStatement: Paid This Month ({start_formatted}) to ({end_formatted})"
            except Exception as statement_error:
                # Don't fail notification if statement generation fails
                current_app.logger.warning(f"Error generating statement for notification: {str(statement_error)}")
            
            message = base_message + statement_info
            
            return NotificationService.create_notification(
                tenant_id=tenant.id,
                notification_type=NotificationType.PAYMENT_APPROVED,
                title=title,
                message=message,
                priority=NotificationPriority.MEDIUM,
                related_entity_type='bill',
                related_entity_id=bill.id,
                action_url=f'/tenant/bills/{bill.id}'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_payment_approved: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_payment_rejected(payment, reason=None):
        """Create notification when a payment is rejected."""
        try:
            bill = payment.bill
            tenant = bill.tenant
            if not tenant:
                return None
            
            title = f"Payment Rejected"
            reason_text = f" Reason: {reason}" if reason else ""
            message = f"Your payment of ₱{payment.amount:,.2f} for bill '{bill.title}' has been rejected.{reason_text} Please review and resubmit."
            
            return NotificationService.create_notification(
                tenant_id=tenant.id,
                notification_type=NotificationType.PAYMENT_REJECTED,
                title=title,
                message=message,
                priority=NotificationPriority.HIGH,
                related_entity_type='bill',
                related_entity_id=bill.id,
                action_url=f'/tenant/bills/{bill.id}'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_payment_rejected: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_request_created(request):
        """Create notification when a maintenance request is created."""
        try:
            tenant = request.tenant
            if not tenant:
                return None
            
            # Determine priority based on request priority
            priority_map = {
                'low': NotificationPriority.LOW,
                'medium': NotificationPriority.MEDIUM,
                'high': NotificationPriority.HIGH,
                'urgent': NotificationPriority.URGENT
            }
            priority = priority_map.get(request.priority.lower(), NotificationPriority.MEDIUM)
            
            title = f"Maintenance Request Created"
            message = f"Your maintenance request '{request.title}' has been submitted and is now pending review."
            
            return NotificationService.create_notification(
                tenant_id=tenant.id,
                notification_type=NotificationType.REQUEST_CREATED,
                title=title,
                message=message,
                priority=priority,
                related_entity_type='request',
                related_entity_id=request.id,
                action_url=f'/tenant/requests/{request.id}'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_request_created: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_request_assigned(request):
        """Create notification when a maintenance request is assigned to staff."""
        try:
            tenant = request.tenant
            if not tenant:
                return None
            
            staff_name = "staff member"
            if request.assigned_staff and request.assigned_staff.user:
                staff_name = request.assigned_staff.user.full_name
            
            title = f"Maintenance Request Assigned"
            message = f"Your maintenance request '{request.title}' has been assigned to {staff_name}."
            
            return NotificationService.create_notification(
                tenant_id=tenant.id,
                notification_type=NotificationType.REQUEST_ASSIGNED,
                title=title,
                message=message,
                priority=NotificationPriority.MEDIUM,
                related_entity_type='request',
                related_entity_id=request.id,
                action_url=f'/tenant/requests/{request.id}'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_request_assigned: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_request_completed(request):
        """Create notification when a maintenance request is completed."""
        try:
            tenant = request.tenant
            if not tenant:
                return None
            
            title = f"Maintenance Request Completed"
            message = f"Your maintenance request '{request.title}' has been completed. Please provide feedback if you'd like."
            
            return NotificationService.create_notification(
                tenant_id=tenant.id,
                notification_type=NotificationType.REQUEST_COMPLETED,
                title=title,
                message=message,
                priority=NotificationPriority.MEDIUM,
                related_entity_type='request',
                related_entity_id=request.id,
                action_url=f'/tenant/requests/{request.id}'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_request_completed: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_request_cancelled(request, reason=None):
        """Create notification when a maintenance request is cancelled/rejected."""
        try:
            tenant = request.tenant
            if not tenant:
                return None
            
            title = f"Maintenance Request Cancelled"
            reason_text = f" Reason: {reason}" if reason else ""
            message = f"Your maintenance request '{request.title}' has been cancelled.{reason_text}"
            
            return NotificationService.create_notification(
                tenant_id=tenant.id,
                notification_type=NotificationType.REQUEST_UPDATED,
                title=title,
                message=message,
                priority=NotificationPriority.HIGH,
                related_entity_type='request',
                related_entity_id=request.id,
                action_url=f'/tenant/requests/{request.id}'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_request_cancelled: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_request_approved(request):
        """Create notification when a maintenance request is approved (status changed to in_progress)."""
        try:
            tenant = request.tenant
            if not tenant:
                return None
            
            title = f"Maintenance Request Approved"
            message = f"Your maintenance request '{request.title}' has been approved and is now in progress."
            
            return NotificationService.create_notification(
                tenant_id=tenant.id,
                notification_type=NotificationType.REQUEST_ASSIGNED,
                title=title,
                message=message,
                priority=NotificationPriority.MEDIUM,
                related_entity_type='request',
                related_entity_id=request.id,
                action_url=f'/tenant/requests/{request.id}'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_request_approved: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_request_updated(request, update_message=None):
        """Create notification when a maintenance request is updated."""
        try:
            tenant = request.tenant
            if not tenant:
                return None
            
            title = f"Maintenance Request Updated"
            message = update_message or f"Your maintenance request '{request.title}' has been updated. Status: {request.status}."
            
            return NotificationService.create_notification(
                tenant_id=tenant.id,
                notification_type=NotificationType.REQUEST_UPDATED,
                title=title,
                message=message,
                priority=NotificationPriority.MEDIUM,
                related_entity_type='request',
                related_entity_id=request.id,
                action_url=f'/tenant/requests/{request.id}'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_request_updated: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_announcement(announcement, tenant_id):
        """Create notification when an announcement is published."""
        try:
            tenant = Tenant.query.get(tenant_id)
            if not tenant:
                return None
            
            # Determine priority based on announcement priority
            priority_map = {
                'low': NotificationPriority.LOW,
                'medium': NotificationPriority.MEDIUM,
                'high': NotificationPriority.HIGH,
                'urgent': NotificationPriority.URGENT
            }
            priority = priority_map.get(announcement.priority.lower() if announcement.priority else 'medium', NotificationPriority.MEDIUM)
            
            # For emergency announcements, always use urgent priority
            if announcement.announcement_type and announcement.announcement_type.lower() == 'emergency':
                priority = NotificationPriority.URGENT
            
            title = f"New Announcement: {announcement.title}"
            message = announcement.content[:200] + "..." if len(announcement.content) > 200 else announcement.content
            
            return NotificationService.create_notification(
                tenant_id=tenant_id,
                notification_type=NotificationType.ANNOUNCEMENT,
                title=title,
                message=message,
                priority=priority,
                related_entity_type='announcement',
                related_entity_id=announcement.id,
                action_url=f'/tenant/announcements/{announcement.id}'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_announcement: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_lease_expiring(tenant, days_until_expiry):
        """Create notification when a lease is expiring soon."""
        try:
            priority = NotificationPriority.HIGH if days_until_expiry <= 30 else NotificationPriority.MEDIUM
            
            title = f"Lease Expiring Soon"
            message = f"Your lease will expire in {days_until_expiry} day(s). Please contact management for renewal options."
            
            return NotificationService.create_notification(
                tenant_id=tenant.id,
                notification_type=NotificationType.LEASE_EXPIRING,
                title=title,
                message=message,
                priority=priority,
                action_url='/tenant/lease'
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_lease_expiring: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def create_staff_notification(staff_user_id, notification_type, title, message, **kwargs):
        """
        Create a notification for a staff member.
        
        Args:
            staff_user_id: ID of the staff user (user_id)
            notification_type: Type of notification (NotificationType enum or string)
            title: Notification title
            message: Notification message
            **kwargs: Additional fields (priority, related_entity_type, related_entity_id, action_url)
        
        Returns:
            Notification object or None if creation failed
        """
        try:
            # Verify user exists and is a staff member
            user = User.query.get(staff_user_id)
            if not user:
                current_app.logger.warning(f"User {staff_user_id} not found for staff notification creation")
                return None
            
            # Create notification for staff
            notification = Notification(
                user_id=staff_user_id,
                notification_type=notification_type,
                title=title,
                message=message,
                recipient_type='staff',
                **kwargs
            )
            
            db.session.add(notification)
            db.session.commit()
            
            current_app.logger.info(f"Created staff notification {notification.id} for user {staff_user_id}")
            return notification
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error creating staff notification: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_staff_task_assigned(task, staff_user_id):
        """Create notification when a task is assigned to staff."""
        try:
            priority_map = {
                'low': NotificationPriority.LOW,
                'medium': NotificationPriority.MEDIUM,
                'high': NotificationPriority.HIGH,
                'urgent': NotificationPriority.URGENT
            }
            task_priority = task.priority.value if hasattr(task.priority, 'value') else str(task.priority)
            priority = priority_map.get(task_priority.lower(), NotificationPriority.MEDIUM)
            
            title = f"New Task Assigned"
            message = f"You have been assigned a new task: '{task.title}'. Priority: {task_priority.title()}."
            
            action_url = f'/staff/tasks/{task.id}' if task.id else '/staff/tasks'
            
            return NotificationService.create_staff_notification(
                staff_user_id=staff_user_id,
                notification_type=NotificationType.TASK_ASSIGNED,
                title=title,
                message=message,
                priority=priority,
                related_entity_type='task',
                related_entity_id=task.id,
                action_url=action_url
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_staff_task_assigned: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_staff_task_updated(task, staff_user_id):
        """Create notification when a task assigned to staff is updated."""
        try:
            title = f"Task Updated"
            message = f"Task '{task.title}' has been updated."
            
            action_url = f'/staff/tasks/{task.id}' if task.id else '/staff/tasks'
            
            return NotificationService.create_staff_notification(
                staff_user_id=staff_user_id,
                notification_type=NotificationType.TASK_UPDATED,
                title=title,
                message=message,
                priority=NotificationPriority.MEDIUM,
                related_entity_type='task',
                related_entity_id=task.id,
                action_url=action_url
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_staff_task_updated: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_staff_request_assigned(maintenance_request, staff_user_id):
        """Create notification when a maintenance request is assigned to staff."""
        try:
            priority_map = {
                'low': NotificationPriority.LOW,
                'medium': NotificationPriority.MEDIUM,
                'high': NotificationPriority.HIGH,
                'urgent': NotificationPriority.URGENT
            }
            request_priority = maintenance_request.priority.lower() if hasattr(maintenance_request, 'priority') else 'medium'
            priority = priority_map.get(request_priority, NotificationPriority.MEDIUM)
            
            title = f"Maintenance Request Assigned"
            message = f"You have been assigned a maintenance request: '{maintenance_request.title}'. Priority: {request_priority.title()}."
            
            action_url = f'/staff/requests/{maintenance_request.id}' if maintenance_request.id else '/staff/requests'
            
            return NotificationService.create_staff_notification(
                staff_user_id=staff_user_id,
                notification_type=NotificationType.REQUEST_ASSIGNED_TO_STAFF,
                title=title,
                message=message,
                priority=priority,
                related_entity_type='request',
                related_entity_id=maintenance_request.id,
                action_url=action_url
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_staff_request_assigned: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def notify_staff_request_updated(maintenance_request, staff_user_id):
        """Create notification when a maintenance request assigned to staff is updated."""
        try:
            title = f"Maintenance Request Updated"
            message = f"Maintenance request '{maintenance_request.title}' has been updated. Status: {maintenance_request.status.title()}."
            
            action_url = f'/staff/requests/{maintenance_request.id}' if maintenance_request.id else '/staff/requests'
            
            return NotificationService.create_staff_notification(
                staff_user_id=staff_user_id,
                notification_type=NotificationType.REQUEST_UPDATED_FOR_STAFF,
                title=title,
                message=message,
                priority=NotificationPriority.MEDIUM,
                related_entity_type='request',
                related_entity_id=maintenance_request.id,
                action_url=action_url
            )
        except Exception as e:
            current_app.logger.error(f"Error in notify_staff_request_updated: {str(e)}", exc_info=True)
            return None

