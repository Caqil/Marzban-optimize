# app/routers/user.py
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Union

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app import logger, xray, db
from app.dependencies import get_expired_users_list, get_validated_user, validate_dates
from app.models.admin import Admin
from app.models.user import (
    UserCreate,
    UserModify,
    UserResponse,
    UsersResponse,
    UserStatus,
    UsersUsagesResponse,
    UserUsagesResponse,
)
from app.utils import report, responses

router = APIRouter(tags=["User"], prefix="/api", responses={401: responses._401})


@router.post("/user", response_model=UserResponse, responses={400: responses._400, 409: responses._409})
async def add_user(
    new_user: UserCreate,
    bg: BackgroundTasks,
    admin: Admin = Depends(Admin.get_current),
):
    """
    Add a new user

    - **username**: 3 to 32 characters, can include a-z, 0-9, and underscores.
    - **status**: User's status, defaults to `active`. Special rules if `on_hold`.
    - **expire**: UTC timestamp for account expiration. Use `0` for unlimited.
    - **data_limit**: Max data usage in bytes (e.g., `1073741824` for 1GB). `0` means unlimited.
    - **data_limit_reset_strategy**: Defines how/if data limit resets. `no_reset` means it never resets.
    - **proxies**: Dictionary of protocol settings (e.g., `vmess`, `vless`).
    - **inbounds**: Dictionary of protocol tags to specify inbound connections.
    - **note**: Optional text field for additional user information or notes.
    - **on_hold_timeout**: UTC timestamp when `on_hold` status should start or end.
    - **on_hold_expire_duration**: Duration (in seconds) for how long the user should stay in `on_hold` status.
    - **next_plan**: Next user plan (resets after use).
    """

    for proxy_type in new_user.proxies:
        if not xray.config.inbounds_by_protocol.get(proxy_type):
            raise HTTPException(
                status_code=400,
                detail=f"Protocol {proxy_type} is disabled on your server",
            )

    try:
        dbuser = await db.create_user(new_user, admin.username)
    except Exception as e:
        if "duplicate" in str(e).lower():
            raise HTTPException(status_code=409, detail="User already exists")
        raise HTTPException(status_code=400, detail=str(e))

    bg.add_task(xray.operations.add_user, dbuser=dbuser)
    user = UserResponse.model_validate(dbuser)
    bg.add_task(report.user_created, user=user, user_id=str(dbuser.id), by=admin, user_admin=await db.get_admin_by_id(dbuser.admin_id) if dbuser.admin_id else None)
    logger.info(f'New user "{dbuser.username}" added')
    return user


@router.get("/user/{username}", response_model=UserResponse, responses={403: responses._403, 404: responses._404})
async def get_user(dbuser: UserResponse = Depends(get_validated_user)):
    """Get user information"""
    return dbuser


@router.put("/user/{username}", response_model=UserResponse, responses={400: responses._400, 403: responses._403, 404: responses._404})
async def modify_user(
    modified_user: UserModify,
    bg: BackgroundTasks,
    dbuser: UserResponse = Depends(get_validated_user),
    admin: Admin = Depends(Admin.get_current),
):
    """
    Modify an existing user

    - **username**: Cannot be changed. Used to identify the user.
    - **status**: User's new status. Can be 'active', 'disabled', 'on_hold', 'limited', or 'expired'.
    - **expire**: UTC timestamp for new account expiration. Set to `0` for unlimited, `null` for no change.
    - **data_limit**: New max data usage in bytes (e.g., `1073741824` for 1GB). Set to `0` for unlimited, `null` for no change.
    - **data_limit_reset_strategy**: New strategy for data limit reset. Options include 'daily', 'weekly', 'monthly', or 'no_reset'.
    - **proxies**: Dictionary of new protocol settings (e.g., `vmess`, `vless`). Empty dictionary means no change.
    - **inbounds**: Dictionary of new protocol tags to specify inbound connections. Empty dictionary means no change.
    - **note**: New optional text for additional user information or notes. `null` means no change.
    - **on_hold_timeout**: New UTC timestamp for when `on_hold` status should start or end. Only applicable if status is changed to 'on_hold'.
    - **on_hold_expire_duration**: New duration (in seconds) for how long the user should stay in `on_hold` status. Only applicable if status is changed to 'on_hold'.
    - **next_plan**: Next user plan (resets after use).

    Note: Fields set to `null` or omitted will not be modified.
    """

    for proxy_type in modified_user.proxies:
        if not xray.config.inbounds_by_protocol.get(proxy_type):
            raise HTTPException(
                status_code=400,
                detail=f"Protocol {proxy_type} is disabled on your server",
            )

    old_status = dbuser.status
    dbuser = await db.update_user(dbuser, modified_user)
    user = UserResponse.model_validate(dbuser)

    if user.status in [UserStatus.active, UserStatus.on_hold]:
        bg.add_task(xray.operations.update_user, dbuser=dbuser)
    else:
        bg.add_task(xray.operations.remove_user, dbuser=dbuser)

    bg.add_task(report.user_updated, user=user, user_admin=await db.get_admin_by_id(dbuser.admin_id) if dbuser.admin_id else None, by=admin)

    logger.info(f'User "{user.username}" modified')

    if user.status != old_status:
        bg.add_task(
            report.status_change,
            username=user.username,
            status=user.status,
            user=user,
            user_admin=await db.get_admin_by_id(dbuser.admin_id) if dbuser.admin_id else None,
            by=admin,
        )
        logger.info(
            f'User "{dbuser.username}" status changed from {old_status} to {user.status}'
        )

    return user


@router.delete("/user/{username}", responses={403: responses._403, 404: responses._404})
async def remove_user(
    bg: BackgroundTasks,
    dbuser: UserResponse = Depends(get_validated_user),
    admin: Admin = Depends(Admin.get_current),
):
    """Remove a user"""
    await db.remove_user(dbuser)
    bg.add_task(xray.operations.remove_user, dbuser=dbuser)

    bg.add_task(
        report.user_deleted, 
        username=dbuser.username, 
        user_admin=Admin.model_validate(await db.get_admin_by_id(dbuser.admin_id)) if dbuser.admin_id else None, 
        by=admin
    )

    logger.info(f'User "{dbuser.username}" deleted')
    return {"detail": "User successfully deleted"}


@router.post("/user/{username}/reset", response_model=UserResponse, responses={403: responses._403, 404: responses._404})
async def reset_user_data_usage(
    bg: BackgroundTasks,
    dbuser: UserResponse = Depends(get_validated_user),
    admin: Admin = Depends(Admin.get_current),
):
    """Reset user data usage"""
    dbuser = await db.reset_user_data_usage(dbuser)
    if dbuser.status in [UserStatus.active, UserStatus.on_hold]:
        bg.add_task(xray.operations.add_user, dbuser=dbuser)

    user = UserResponse.model_validate(dbuser)
    bg.add_task(
        report.user_data_usage_reset, 
        user=user, 
        user_admin=await db.get_admin_by_id(dbuser.admin_id) if dbuser.admin_id else None, 
        by=admin
    )

    logger.info(f'User "{dbuser.username}"\'s usage was reset')
    return dbuser


@router.post("/user/{username}/revoke_sub", response_model=UserResponse, responses={403: responses._403, 404: responses._404})
async def revoke_user_subscription(
    bg: BackgroundTasks,
    dbuser: UserResponse = Depends(get_validated_user),
    admin: Admin = Depends(Admin.get_current),
):
    """Revoke users subscription (Subscription link and proxies)"""
    dbuser = await db.revoke_user_sub(dbuser)

    if dbuser.status in [UserStatus.active, UserStatus.on_hold]:
        bg.add_task(xray.operations.update_user, dbuser=dbuser)
    user = UserResponse.model_validate(dbuser)
    bg.add_task(
        report.user_subscription_revoked, 
        user=user, 
        user_admin=await db.get_admin_by_id(dbuser.admin_id) if dbuser.admin_id else None, 
        by=admin
    )

    logger.info(f'User "{dbuser.username}" subscription revoked')

    return user


@router.get("/users", response_model=UsersResponse, responses={400: responses._400, 403: responses._403, 404: responses._404})
async def get_users(
    offset: int = None,
    limit: int = None,
    username: List[str] = Query(None),
    search: Union[str, None] = None,
    owner: Union[List[str], None] = Query(None, alias="admin"),
    status: UserStatus = None,
    sort: str = None,
    admin: Admin = Depends(Admin.get_current),
):
    """Get all users"""
    
    admin_filter = owner if admin.is_sudo else [admin.username]
    
    users, count = await db.get_users(
        offset=offset,
        limit=limit,
        search=search,
        usernames=username,
        status=status,
        admin_username=admin_filter[0] if admin_filter else None,
        return_with_count=True,
    )

    return {"users": users, "total": count}


@router.post("/users/reset", responses={403: responses._403, 404: responses._404})
async def reset_users_data_usage(admin: Admin = Depends(Admin.check_sudo_admin)):
    """Reset all users data usage"""
    await db.reset_all_users_data_usage(admin.username)
    startup_config = xray.config.include_db_users()
    xray.core.restart(startup_config)
    for node_id, node in list(xray.nodes.items()):
        if node.connected:
            xray.operations.restart_node(node_id, startup_config)
    return {"detail": "Users successfully reset."}


@router.get("/user/{username}/usage", response_model=UserUsagesResponse, responses={403: responses._403, 404: responses._404})
async def get_user_usage(
    dbuser: UserResponse = Depends(get_validated_user),
    start: str = "",
    end: str = "",
):
    """Get users usage"""
    start, end = validate_dates(start, end)

    usages = await db.get_user_usages(dbuser, start, end)

    return {"usages": usages, "username": dbuser.username}


@router.post("/user/{username}/active-next", response_model=UserResponse, responses={403: responses._403, 404: responses._404})
async def active_next_plan(
    bg: BackgroundTasks,
    dbuser: UserResponse = Depends(get_validated_user),
):
    """Reset user by next plan"""
    dbuser = await db.reset_user_by_next(dbuser)

    if not dbuser or not dbuser.next_plan:
        raise HTTPException(
            status_code=404,
            detail=f"User doesn't have next plan",
        )

    if dbuser.status in [UserStatus.active, UserStatus.on_hold]:
        bg.add_task(xray.operations.add_user, dbuser=dbuser)

    user = UserResponse.model_validate(dbuser)
    bg.add_task(
        report.user_data_reset_by_next, 
        user=user, 
        user_admin=await db.get_admin_by_id(dbuser.admin_id) if dbuser.admin_id else None,
    )

    logger.info(f'User "{dbuser.username}"\'s usage was reset by next plan')
    return dbuser


@router.get("/users/usage", response_model=UsersUsagesResponse)
async def get_users_usage(
    start: str = "",
    end: str = "",
    owner: Union[List[str], None] = Query(None, alias="admin"),
    admin: Admin = Depends(Admin.get_current),
):
    """Get all users usage"""
    start, end = validate_dates(start, end)

    admin_filter = owner if admin.is_sudo else [admin.username]
    
    usages = await db.get_all_users_usages(
        start=start, 
        end=end, 
        admin_username=admin_filter[0] if admin_filter else None
    )

    return {"usages": usages}


@router.put("/user/{username}/set-owner", response_model=UserResponse)
async def set_owner(
    admin_username: str,
    dbuser: UserResponse = Depends(get_validated_user),
    admin: Admin = Depends(Admin.check_sudo_admin),
):
    """Set a new owner (admin) for a user."""
    new_admin = await db.get_admin(admin_username)
    if not new_admin:
        raise HTTPException(status_code=404, detail="Admin not found")

    dbuser = await db.set_owner(dbuser, new_admin)
    user = UserResponse.model_validate(dbuser)

    logger.info(f'{user.username} owner successfully set to {admin.username}')

    return user


@router.get("/users/expired", response_model=List[str])
async def get_expired_users(
    expired_after: Optional[datetime] = Query(None, example="2024-01-01T00:00:00"),
    expired_before: Optional[datetime] = Query(None, example="2024-01-31T23:59:59"),
    admin: Admin = Depends(Admin.get_current),
):
    """
    Get users who have expired within the specified date range.

    - **expired_after** UTC datetime (optional)
    - **expired_before** UTC datetime (optional)
    - At least one of expired_after or expired_before must be provided for filtering
    - If both are omitted, returns all expired users
    """

    expired_after, expired_before = validate_dates(expired_after, expired_before)

    expired_users = await get_expired_users_list(admin, expired_after, expired_before)
    return [u.username for u in expired_users]


@router.delete("/users/expired", response_model=List[str])
async def delete_expired_users(
    bg: BackgroundTasks,
    expired_after: Optional[datetime] = Query(None, example="2024-01-01T00:00:00"),
    expired_before: Optional[datetime] = Query(None, example="2024-01-31T23:59:59"),
    admin: Admin = Depends(Admin.get_current),
):
    """
    Delete users who have expired within the specified date range.

    - **expired_after** UTC datetime (optional)
    - **expired_before** UTC datetime (optional)
    - At least one of expired_after or expired_before must be provided
    """
    expired_after, expired_before = validate_dates(expired_after, expired_before)

    expired_users = await get_expired_users_list(admin, expired_after, expired_before)
    removed_users = [u.username for u in expired_users]

    if not removed_users:
        raise HTTPException(
            status_code=404, detail="No expired users found in the specified date range"
        )

    await db.remove_users(expired_users)

    for removed_user in removed_users:
        logger.info(f'User "{removed_user}" deleted')
        bg.add_task(
            report.user_deleted,
            username=removed_user,
            user_admin=next(
                (Admin.model_validate(await db.get_admin_by_id(u.admin_id)) for u in expired_users if u.username == removed_user and u.admin_id), None
            ),
            by=admin,
        )

    return removed_users