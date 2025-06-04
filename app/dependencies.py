# app/dependencies.py
from typing import Optional, Union, List
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.models.admin import AdminInDB, AdminValidationResult, Admin
from app.models.user import UserResponse, UserStatus
from app import db
from config import SUDOERS
from fastapi import Depends, HTTPException
from datetime import datetime, timezone, timedelta
from app.utils.jwt import get_subscription_payload


async def validate_admin(username: str, password: str) -> Optional[AdminValidationResult]:
    """Validate admin credentials with environment variables or database."""
    if SUDOERS.get(username) == password:
        return AdminValidationResult(username=username, is_sudo=True)

    dbadmin = await db.get_admin(username)
    if dbadmin and AdminInDB.model_validate(dbadmin).verify_password(password):
        return AdminValidationResult(username=dbadmin.username, is_sudo=dbadmin.is_sudo)

    return None


async def get_admin_by_username(username: str):
    """Fetch an admin by username from the database."""
    dbadmin = await db.get_admin(username)
    if not dbadmin:
        raise HTTPException(status_code=404, detail="Admin not found")
    return dbadmin


async def get_dbnode(node_id: str):
    """Fetch a node by its ID from the database, raising a 404 error if not found."""
    dbnode = await db.get_node_by_id(node_id)
    if not dbnode:
        raise HTTPException(status_code=404, detail="Node not found")
    return dbnode


def validate_dates(start: Optional[Union[str, datetime]], end: Optional[Union[str, datetime]]) -> tuple[datetime, datetime]:
    """Validate if start and end dates are correct and if end is after start."""
    try:
        if start:
            start_date = start if isinstance(start, datetime) else datetime.fromisoformat(
                start).astimezone(timezone.utc)
        else:
            start_date = datetime.now(timezone.utc) - timedelta(days=30)
        if end:
            end_date = end if isinstance(end, datetime) else datetime.fromisoformat(end).astimezone(timezone.utc)
            if start_date and end_date < start_date:
                raise HTTPException(status_code=400, detail="Start date must be before end date")
        else:
            end_date = datetime.now(timezone.utc)

        return start_date, end_date
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date range or format")


async def get_user_template(template_id: str):
    """Fetch a User Template by its ID, raise 404 if not found."""
    dbuser_template = await db.get_user_template_by_id(template_id)
    if not dbuser_template:
        raise HTTPException(status_code=404, detail="User Template not found")
    return dbuser_template


async def get_validated_sub(token: str) -> UserResponse:
    sub = get_subscription_payload(token)
    if not sub:
        raise HTTPException(status_code=404, detail="Not Found")

    dbuser = await db.get_user(sub['username'])
    if not dbuser or dbuser.created_at > sub['created_at']:
        raise HTTPException(status_code=404, detail="Not Found")

    if dbuser.sub_revoked_at and dbuser.sub_revoked_at > sub['created_at']:
        raise HTTPException(status_code=404, detail="Not Found")

    return dbuser


async def get_validated_user(username: str, admin: Admin = Depends(Admin.get_current)) -> UserResponse:
    dbuser = await db.get_user(username)
    if not dbuser:
        raise HTTPException(status_code=404, detail="User not found")

    if not (admin.is_sudo or (dbuser.admin_id and str(dbuser.admin_id) == admin.username)):
        raise HTTPException(status_code=403, detail="You're not allowed")

    return dbuser


async def get_expired_users_list(
    admin: Admin, 
    expired_after: Optional[datetime] = None,
    expired_before: Optional[datetime] = None
) -> List[UserResponse]:
    expired_before = expired_before or datetime.now(timezone.utc)
    expired_after = expired_after or datetime.min.replace(tzinfo=timezone.utc)

    admin_filter = None if admin.is_sudo else admin.username
    
    dbusers = await db.get_users(
        status=[UserStatus.expired, UserStatus.limited],
        admin_username=admin_filter
    )

    return [
        u for u in dbusers
        if u.expire and expired_after.timestamp() <= u.expire <= expired_before.timestamp()
    ]