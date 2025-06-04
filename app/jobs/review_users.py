from datetime import datetime
from typing import TYPE_CHECKING
import asyncio

from app import logger, scheduler, xray, db
from app.models.user import ReminderType, UserResponse, UserStatus
from app.utils import report
from app.utils.helpers import (calculate_expiration_days,
                               calculate_usage_percent)
from config import (JOB_REVIEW_USERS_INTERVAL, NOTIFY_DAYS_LEFT,
                    NOTIFY_REACHED_USAGE_PERCENT, WEBHOOK_ADDRESS)

if TYPE_CHECKING:
    from app.db.models import User


async def add_notification_reminders(user: "User", now: datetime = datetime.utcnow()) -> None:
    if user.data_limit:
        usage_percent = calculate_usage_percent(user.used_traffic, user.data_limit)

        for percent in sorted(NOTIFY_REACHED_USAGE_PERCENT, reverse=True):
            if usage_percent >= percent:
                existing_reminder = await db.get_notification_reminder(
                    user.id, ReminderType.data_usage, threshold=percent
                )
                if not existing_reminder:
                    await report.data_usage_percent_reached(
                        usage_percent, UserResponse.model_validate(user),
                        str(user.id), user.expire, threshold=percent
                    )
                break

    if user.expire:
        expire_days = calculate_expiration_days(user.expire)

        for days_left in sorted(NOTIFY_DAYS_LEFT):
            if expire_days <= days_left:
                existing_reminder = await db.get_notification_reminder(
                    user.id, ReminderType.expiration_date, threshold=days_left
                )
                if not existing_reminder:
                    await report.expire_days_reached(
                        expire_days, UserResponse.model_validate(user),
                        str(user.id), user.expire, threshold=days_left
                    )
                break


async def reset_user_by_next_report(user: "User"):
    user = await db.reset_user_by_next(user)
    
    if user:
        xray.operations.update_user(user)
        await report.user_data_reset_by_next(
            user=UserResponse.model_validate(user), 
            user_admin=await db.get_admin_by_id(user.admin_id) if user.admin_id else None
        )


async def review():
    now = datetime.utcnow()
    now_ts = now.timestamp()
    
    # Get active users
    active_users = await db.get_users(status=UserStatus.active)
    
    for user in active_users:
        limited = user.data_limit and user.used_traffic >= user.data_limit
        expired = user.expire and user.expire <= now_ts

        # Check if user has next plan and should be reset
        if (limited or expired) and hasattr(user, 'next_plan') and user.next_plan is not None:
            next_plan = await db.get_next_plan_by_user_id(user.id)
            if next_plan:
                if next_plan.fire_on_either:
                    await reset_user_by_next_report(user)
                    continue
                elif limited and expired:
                    await reset_user_by_next_report(user)
                    continue

        if limited:
            status = UserStatus.limited
        elif expired:
            status = UserStatus.expired
        else:
            if WEBHOOK_ADDRESS:
                await add_notification_reminders(user, now)
            continue

        # Remove user from xray and update status
        xray.operations.remove_user(user)
        await db.update_user_status(user, status)

        await report.status_change(
            username=user.username, 
            status=status,
            user=UserResponse.model_validate(user), 
            user_admin=await db.get_admin_by_id(user.admin_id) if user.admin_id else None
        )

        logger.info(f"User \"{user.username}\" status changed to {status}")

    # Review on_hold users
    on_hold_users = await db.get_users(status=UserStatus.on_hold)
    
    for user in on_hold_users:
        base_time = user.edit_at or user.created_at
        base_timestamp = base_time.timestamp()

        # Check if the user is online after or at 'base_time'
        if user.online_at and base_timestamp <= user.online_at.timestamp():
            status = UserStatus.active
        elif user.on_hold_timeout and (user.on_hold_timeout.timestamp() <= now_ts):
            # If the user didn't connect within the timeout period, change status to "Active"
            status = UserStatus.active
        else:
            continue

        await db.update_user_status(user, status)
        await db.start_user_expire(user)

        await report.status_change(
            username=user.username, 
            status=status,
            user=UserResponse.model_validate(user), 
            user_admin=await db.get_admin_by_id(user.admin_id) if user.admin_id else None
        )

        logger.info(f"User \"{user.username}\" status changed to {status}")


def review_sync():
    """Synchronous wrapper for the async review function"""
    asyncio.create_task(review())


scheduler.add_job(review_sync, 'interval',
                  seconds=JOB_REVIEW_USERS_INTERVAL,
                  coalesce=True, max_instances=1)