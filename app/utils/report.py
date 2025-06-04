from datetime import datetime as dt
from typing import Optional


from app.db import Session, create_notification_reminder, get_admin_by_id, GetDB
from app.db.models import UserStatus, User
from app.models.admin import Admin
from app.models.user import ReminderType, UserResponse
from app.utils.notification import (Notification, ReachedDaysLeft,
                                    ReachedUsagePercent, UserCreated, UserDataResetByNext,
                                    UserDataUsageReset, UserDeleted,
                                    UserDisabled, UserEnabled, UserExpired,
                                    UserLimited, UserSubscriptionRevoked,
                                    UserUpdated, notify)

from config import (
    NOTIFY_STATUS_CHANGE,
    NOTIFY_USER_CREATED,
    NOTIFY_USER_UPDATED,
    NOTIFY_USER_DELETED,
    NOTIFY_USER_DATA_USED_RESET,
    NOTIFY_USER_SUB_REVOKED,
    NOTIFY_IF_DATA_USAGE_PERCENT_REACHED,
    NOTIFY_IF_DAYS_LEFT_REACHED,
    NOTIFY_LOGIN
)


def status_change(
        username: str, status: UserStatus, user: UserResponse, user_admin: Admin = None, by: Admin = None) -> None:
    if NOTIFY_STATUS_CHANGE:
        if status == UserStatus.limited:
            notify(UserLimited(username=username, action=Notification.Type.user_limited, user=user))
        elif status == UserStatus.expired:
            notify(UserExpired(username=username, action=Notification.Type.user_expired, user=user))
        elif status == UserStatus.disabled:
            notify(UserDisabled(username=username, action=Notification.Type.user_disabled, user=user, by=by))
        elif status == UserStatus.active:
            notify(UserEnabled(username=username, action=Notification.Type.user_enabled, user=user, by=by))


def user_created(user: UserResponse, user_id: int, by: Admin, user_admin: Admin = None) -> None:
    if NOTIFY_USER_CREATED:
        notify(UserCreated(username=user.username, action=Notification.Type.user_created, by=by, user=user))


def user_updated(user: UserResponse, by: Admin, user_admin: Admin = None) -> None:
    if NOTIFY_USER_UPDATED:
        notify(UserUpdated(username=user.username, action=Notification.Type.user_updated, by=by, user=user))


def user_deleted(username: str, by: Admin, user_admin: Admin = None) -> None:
    if NOTIFY_USER_DELETED:
        notify(UserDeleted(username=username, action=Notification.Type.user_deleted, by=by))


def user_data_usage_reset(user: UserResponse, by: Admin, user_admin: Admin = None) -> None:
    if NOTIFY_USER_DATA_USED_RESET:
        notify(UserDataUsageReset(username=user.username, action=Notification.Type.data_usage_reset, by=by, user=user))


def user_data_reset_by_next(user: UserResponse, user_admin: Admin = None) -> None:
    if NOTIFY_USER_DATA_USED_RESET:
        notify(UserDataResetByNext(username=user.username, action=Notification.Type.data_reset_by_next, user=user))


def user_subscription_revoked(user: UserResponse, by: Admin, user_admin: Admin = None) -> None:
    if NOTIFY_USER_SUB_REVOKED:
        notify(UserSubscriptionRevoked(username=user.username,
               action=Notification.Type.subscription_revoked, by=by, user=user))


def data_usage_percent_reached(
        db: Session, percent: float, user: UserResponse, user_id: int, expire: Optional[int] = None, threshold: Optional[int] = None) -> None:
    if NOTIFY_IF_DATA_USAGE_PERCENT_REACHED:
        notify(ReachedUsagePercent(username=user.username, user=user, used_percent=percent))
        create_notification_reminder(db, ReminderType.data_usage,
                                     expires_at=dt.utcfromtimestamp(expire) if expire else None, user_id=user_id, threshold=threshold)


def expire_days_reached(db: Session, days: int, user: UserResponse, user_id: int, expire: int, threshold=None) -> None:
    notify(ReachedDaysLeft(username=user.username, user=user, days_left=days))
    if NOTIFY_IF_DAYS_LEFT_REACHED:
        create_notification_reminder(
            db, ReminderType.expiration_date, expires_at=dt.utcfromtimestamp(expire),
            user_id=user_id, threshold=threshold)


def login(username: str, password: str, client_ip: str, success: bool) -> None:
    if NOTIFY_LOGIN:
        # Login notifications can be added here if needed
        pass