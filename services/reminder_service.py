"""
Reminder Service for automated reminders.
This service handles scheduled reminders for bills, maintenance, tasks, and leases.
Can be called periodically via cron job or scheduler.
"""

from datetime import datetime, timezone, date, timedelta
from app import db
from models.notification import NotificationType, NotificationPriority
from models.bill import Bill
from models.request import MaintenanceRequest
from models.task import Task
from models.tenant import Tenant, TenantUnit
from services.notification_service import NotificationService
from flask import current_app
from sqlalchemy import text, func, and_, or_

class ReminderService:
    """Service for creating automated reminders."""
    
    # Reminder configuration (days before due date)
    BILL_REMINDER_DAYS = [7, 3, 1]  # Remind 7 days, 3 days, and 1 day before due date
    OVERDUE_REMINDER_INTERVAL = 1  # Remind daily for overdue bills
    MAINTENANCE_REMINDER_DAYS = [3, 1]  # Remind 3 days and 1 day before scheduled maintenance
    TASK_REMINDER_DAYS = [3, 1]  # Remind 3 days and 1 day before task deadline
    LEASE_EXPIRING_DAYS = [30, 14, 7, 3]  # Remind 30, 14, 7, and 3 days before lease expiration
    
    @staticmethod
    def send_bill_due_reminders():
        """
        Send reminders for bills approaching due date.
        Sends reminders at configured intervals (7, 3, 1 days before due date).
        """
        try:
            today = date.today()
            reminders_sent = 0
            
            for days_before in ReminderService.BILL_REMINDER_DAYS:
                reminder_date = today + timedelta(days=days_before)
                
                # Get bills due on reminder_date that are not paid
                # Note: amount_due is a property, so we filter by status and calculate in Python
                bills_query = Bill.query.filter(
                    Bill.due_date == reminder_date,
                    Bill.status.in_(['pending', 'partial'])  # Only remind for unpaid bills
                )
                bills = bills_query.all()
                
                # Filter by amount_due > 0 (calculated property)
                bills = [b for b in bills if b.amount_due > 0]
                
                for bill in bills:
                    try:
                        # Check if reminder already sent for this bill and days_before
                        # We'll track this by checking recent notifications
                        existing_notification = db.session.execute(text(
                            """
                            SELECT id FROM notifications
                            WHERE related_entity_type = 'bill'
                              AND related_entity_id = :bill_id
                              AND notification_type = 'bill_due_reminder'
                              AND message LIKE :message_pattern
                              AND created_at >= DATE_SUB(NOW(), INTERVAL 2 DAY)
                            LIMIT 1
                            """
                        ), {
                            'bill_id': bill.id,
                            'message_pattern': f'%{days_before} day%'
                        }).first()
                        
                        if existing_notification:
                            continue  # Already sent reminder for this interval
                        
                        tenant = bill.tenant
                        if not tenant:
                            continue
                        
                        # Format due date
                        due_date_str = bill.due_date.strftime('%B %d, %Y')
                        
                        # Create reminder message
                        if days_before == 1:
                            message = f"⏰ Reminder: Your bill '{bill.title}' (₱{bill.amount_due:,.2f}) is due TOMORROW ({due_date_str}). Please submit payment to avoid late fees."
                            priority = NotificationPriority.HIGH
                        elif days_before == 3:
                            message = f"📅 Reminder: Your bill '{bill.title}' (₱{bill.amount_due:,.2f}) is due in 3 days ({due_date_str}). Please prepare payment."
                            priority = NotificationPriority.MEDIUM
                        else:
                            message = f"📋 Reminder: Your bill '{bill.title}' (₱{bill.amount_due:,.2f}) is due in {days_before} days ({due_date_str})."
                            priority = NotificationPriority.MEDIUM
                        
                        NotificationService.create_notification(
                            tenant_id=tenant.id,
                            notification_type=NotificationType.BILL_DUE_REMINDER,
                            title=f"Bill Due in {days_before} Day{'s' if days_before > 1 else ''}",
                            message=message,
                            priority=priority,
                            related_entity_type='bill',
                            related_entity_id=bill.id,
                            action_url=f'/tenant/bills/{bill.id}'
                        )
                        reminders_sent += 1
                    except Exception as bill_error:
                        current_app.logger.warning(f"Error sending reminder for bill {bill.id}: {str(bill_error)}")
                        continue
            
            current_app.logger.info(f"Sent {reminders_sent} bill due reminders")
            return reminders_sent
        except Exception as e:
            current_app.logger.error(f"Error in send_bill_due_reminders: {str(e)}", exc_info=True)
            return 0
    
    @staticmethod
    def send_overdue_bill_reminders():
        """
        Send daily reminders for overdue bills.
        """
        try:
            today = date.today()
            reminders_sent = 0
            
            # Get overdue bills (due date < today, status not paid)
            bills = Bill.query.filter(
                Bill.due_date < today,
                Bill.status.in_(['pending', 'partial', 'overdue'])
            ).all()
            
            # Filter by amount_due > 0 (calculated property)
            bills = [b for b in bills if b.amount_due > 0]
            
            for bill in bills:
                try:
                    # Check if reminder already sent today
                    existing_notification = db.session.execute(text(
                        """
                        SELECT id FROM notifications
                        WHERE related_entity_type = 'bill'
                          AND related_entity_id = :bill_id
                          AND notification_type = 'bill_overdue'
                          AND DATE(created_at) = CURDATE()
                        LIMIT 1
                        """
                    ), {'bill_id': bill.id}).first()
                    
                    if existing_notification:
                        continue  # Already sent today
                    
                    tenant = bill.tenant
                    if not tenant:
                        continue
                    
                    days_overdue = (today - bill.due_date).days
                    due_date_str = bill.due_date.strftime('%B %d, %Y')
                    
                    message = f"⚠️ URGENT: Your bill '{bill.title}' (₱{bill.amount_due:,.2f}) is {days_overdue} day{'s' if days_overdue > 1 else ''} overdue (Due: {due_date_str}). Please pay immediately to avoid further penalties."
                    
                    NotificationService.create_notification(
                        tenant_id=tenant.id,
                        notification_type=NotificationType.BILL_OVERDUE,
                        title="⚠️ Overdue Bill",
                        message=message,
                        priority=NotificationPriority.URGENT,
                        related_entity_type='bill',
                        related_entity_id=bill.id,
                        action_url=f'/tenant/bills/{bill.id}'
                    )
                    reminders_sent += 1
                except Exception as bill_error:
                    current_app.logger.warning(f"Error sending overdue reminder for bill {bill.id}: {str(bill_error)}")
                    continue
            
            current_app.logger.info(f"Sent {reminders_sent} overdue bill reminders")
            return reminders_sent
        except Exception as e:
            current_app.logger.error(f"Error in send_overdue_bill_reminders: {str(e)}", exc_info=True)
            return 0
    
    @staticmethod
    def send_maintenance_schedule_reminders():
        """
        Send reminders for scheduled maintenance.
        Sends reminders at configured intervals before scheduled_date.
        """
        try:
            today = date.today()
            reminders_sent = 0
            
            for days_before in ReminderService.MAINTENANCE_REMINDER_DAYS:
                reminder_date = today + timedelta(days=days_before)
                
                # Get maintenance requests scheduled on reminder_date that are not completed
                requests = MaintenanceRequest.query.filter(
                    MaintenanceRequest.scheduled_date.isnot(None),
                    func.date(MaintenanceRequest.scheduled_date) == reminder_date,
                    MaintenanceRequest.status.in_(['pending', 'in_progress'])
                ).all()
                
                for request in requests:
                    try:
                        # Check if reminder already sent
                        existing_notification = db.session.execute(text(
                            """
                            SELECT id FROM notifications
                            WHERE related_entity_type = 'request'
                              AND related_entity_id = :request_id
                              AND notification_type = 'maintenance_schedule_reminder'
                              AND message LIKE :message_pattern
                              AND created_at >= DATE_SUB(NOW(), INTERVAL 2 DAY)
                            LIMIT 1
                            """
                        ), {
                            'request_id': request.id,
                            'message_pattern': f'%{days_before} day%'
                        }).first()
                        
                        if existing_notification:
                            continue
                        
                        tenant = request.tenant if hasattr(request, 'tenant') else None
                        if not tenant:
                            continue
                        
                        scheduled_date_str = request.scheduled_date.strftime('%B %d, %Y at %I:%M %p') if request.scheduled_date else 'TBD'
                        
                        if days_before == 1:
                            message = f"🔧 Reminder: Maintenance for '{request.title}' is scheduled TOMORROW ({scheduled_date_str}). Please ensure access to your unit."
                            priority = NotificationPriority.HIGH
                        else:
                            message = f"🔧 Reminder: Maintenance for '{request.title}' is scheduled in {days_before} days ({scheduled_date_str})."
                            priority = NotificationPriority.MEDIUM
                        
                        NotificationService.create_notification(
                            tenant_id=tenant.id,
                            notification_type=NotificationType.MAINTENANCE_SCHEDULE_REMINDER,
                            title=f"Maintenance Scheduled in {days_before} Day{'s' if days_before > 1 else ''}",
                            message=message,
                            priority=priority,
                            related_entity_type='request',
                            related_entity_id=request.id,
                            action_url=f'/tenant/requests/{request.id}'
                        )
                        reminders_sent += 1
                    except Exception as req_error:
                        current_app.logger.warning(f"Error sending maintenance reminder for request {request.id}: {str(req_error)}")
                        continue
            
            current_app.logger.info(f"Sent {reminders_sent} maintenance schedule reminders")
            return reminders_sent
        except Exception as e:
            current_app.logger.error(f"Error in send_maintenance_schedule_reminders: {str(e)}", exc_info=True)
            return 0
    
    @staticmethod
    def send_task_deadline_reminders():
        """
        Send reminders for tasks approaching deadline.
        Sends reminders to assigned staff members.
        """
        try:
            today = date.today()
            reminders_sent = 0
            
            for days_before in ReminderService.TASK_REMINDER_DAYS:
                reminder_date = today + timedelta(days=days_before)
                
                # Get tasks due on reminder_date that are not completed
                tasks = Task.query.filter(
                    Task.due_date.isnot(None),
                    func.date(Task.due_date) == reminder_date,
                    Task.status.in_(['open', 'in_progress'])
                ).all()
                
                for task in tasks:
                    try:
                        # Check if reminder already sent
                        existing_notification = db.session.execute(text(
                            """
                            SELECT id FROM notifications
                            WHERE related_entity_type = 'task'
                              AND related_entity_id = :task_id
                              AND notification_type = 'task_deadline_reminder'
                              AND message LIKE :message_pattern
                              AND created_at >= DATE_SUB(NOW(), INTERVAL 2 DAY)
                            LIMIT 1
                            """
                        ), {
                            'task_id': task.id,
                            'message_pattern': f'%{days_before} day%'
                        }).first()
                        
                        if existing_notification:
                            continue
                        
                        if not task.assigned_to:
                            continue
                        
                        assigned_user = task.assignee
                        if not assigned_user:
                            continue
                        
                        due_date_str = task.due_date.strftime('%B %d, %Y at %I:%M %p') if task.due_date else 'TBD'
                        
                        if days_before == 1:
                            message = f"📋 Reminder: Task '{task.title}' is due TOMORROW ({due_date_str}). Please complete it soon."
                            priority = NotificationPriority.HIGH
                        else:
                            message = f"📋 Reminder: Task '{task.title}' is due in {days_before} days ({due_date_str})."
                            priority = NotificationPriority.MEDIUM
                        
                        NotificationService.create_pm_notification(
                            property_manager_id=assigned_user.id,
                            notification_type=NotificationType.TASK_DEADLINE_REMINDER,
                            title=f"Task Due in {days_before} Day{'s' if days_before > 1 else ''}",
                            message=message,
                            priority=priority,
                            recipient_type='staff',
                            related_entity_type='task',
                            related_entity_id=task.id,
                            action_url=f'/staff/tasks/{task.id}'
                        )
                        reminders_sent += 1
                    except Exception as task_error:
                        current_app.logger.warning(f"Error sending task reminder for task {task.id}: {str(task_error)}")
                        continue
            
            current_app.logger.info(f"Sent {reminders_sent} task deadline reminders")
            return reminders_sent
        except Exception as e:
            current_app.logger.error(f"Error in send_task_deadline_reminders: {str(e)}", exc_info=True)
            return 0
    
    @staticmethod
    def send_lease_expiring_reminders():
        """
        Send reminders for leases approaching expiration.
        Sends reminders to tenants and property managers.
        """
        try:
            today = date.today()
            reminders_sent = 0
            
            for days_before in ReminderService.LEASE_EXPIRING_DAYS:
                reminder_date = today + timedelta(days=days_before)
                
                # Get tenant_units expiring on reminder_date
                tenant_units = db.session.execute(text(
                    """
                    SELECT tu.id, tu.tenant_id, tu.rent_end_date, tu.move_out_date
                    FROM tenant_units tu
                    WHERE (tu.rent_end_date = :reminder_date OR tu.move_out_date = :reminder_date)
                      AND tu.is_active = 1
                    """
                ), {'reminder_date': reminder_date}).fetchall()
                
                for tu_row in tenant_units:
                    try:
                        tenant_unit_id = tu_row[0]
                        tenant_id = tu_row[1]
                        rent_end_date = tu_row[2] or tu_row[3]  # rent_end_date or move_out_date
                        
                        # Check if reminder already sent
                        existing_notification = db.session.execute(text(
                            """
                            SELECT id FROM notifications
                            WHERE tenant_id = :tenant_id
                              AND notification_type = 'lease_expiring'
                              AND message LIKE :message_pattern
                              AND created_at >= DATE_SUB(NOW(), INTERVAL 2 DAY)
                            LIMIT 1
                            """
                        ), {
                            'tenant_id': tenant_id,
                            'message_pattern': f'%{days_before} day%'
                        }).first()
                        
                        if existing_notification:
                            continue
                        
                        tenant = Tenant.query.get(tenant_id)
                        if not tenant:
                            continue
                        
                        expiration_date_str = rent_end_date.strftime('%B %d, %Y') if rent_end_date else 'TBD'
                        
                        if days_before >= 30:
                            message = f"📅 Your lease will expire in {days_before} days ({expiration_date_str}). Please contact management for renewal options."
                            priority = NotificationPriority.MEDIUM
                        elif days_before >= 14:
                            message = f"⏰ Your lease will expire in {days_before} days ({expiration_date_str}). Please discuss renewal or move-out plans with management."
                            priority = NotificationPriority.HIGH
                        else:
                            message = f"⚠️ URGENT: Your lease expires in {days_before} days ({expiration_date_str}). Please contact management immediately."
                            priority = NotificationPriority.URGENT
                        
                        NotificationService.create_notification(
                            tenant_id=tenant.id,
                            notification_type=NotificationType.LEASE_EXPIRING,
                            title=f"Lease Expiring in {days_before} Days",
                            message=message,
                            priority=priority,
                            related_entity_type='tenant_unit',
                            related_entity_id=tenant_unit_id,
                            action_url='/tenant/dashboard'
                        )
                        reminders_sent += 1
                    except Exception as lease_error:
                        current_app.logger.warning(f"Error sending lease reminder for tenant_unit {tenant_unit_id}: {str(lease_error)}")
                        continue
            
            current_app.logger.info(f"Sent {reminders_sent} lease expiring reminders")
            return reminders_sent
        except Exception as e:
            current_app.logger.error(f"Error in send_lease_expiring_reminders: {str(e)}", exc_info=True)
            return 0
    
    @staticmethod
    def send_all_reminders():
        """
        Send all types of reminders.
        This is the main function to call periodically (e.g., daily via cron).
        """
        try:
            results = {
                'bill_due_reminders': ReminderService.send_bill_due_reminders(),
                'overdue_bill_reminders': ReminderService.send_overdue_bill_reminders(),
                'maintenance_reminders': ReminderService.send_maintenance_schedule_reminders(),
                'task_reminders': ReminderService.send_task_deadline_reminders(),
                'lease_reminders': ReminderService.send_lease_expiring_reminders()
            }
            
            total = sum(results.values())
            current_app.logger.info(f"Reminder service completed. Total reminders sent: {total}")
            return results
        except Exception as e:
            current_app.logger.error(f"Error in send_all_reminders: {str(e)}", exc_info=True)
            return {'error': str(e)}

