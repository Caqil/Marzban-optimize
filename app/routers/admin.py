# app/routers/admin.py
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from app import xray, db
from app.dependencies import get_admin_by_username, validate_admin
from app.models.admin import Admin, AdminCreate, AdminModify, Token
from app.utils import report, responses
from app.utils.jwt import create_admin_token
from config import LOGIN_NOTIFY_WHITE_LIST

router = APIRouter(tags=["Admin"], prefix="/api", responses={401: responses._401})


def get_client_ip(request: Request) -> str:
    """Extract the client's IP address from the request headers or client."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "Unknown"


@router.post("/admin/token", response_model=Token)
async def admin_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    """Authenticate an admin and issue a token."""
    client_ip = get_client_ip(request)

    dbadmin = await validate_admin(form_data.username, form_data.password)
    if not dbadmin:
        await report.login(form_data.username, form_data.password, client_ip, False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if client_ip not in LOGIN_NOTIFY_WHITE_LIST:
        await report.login(form_data.username, "🔒", client_ip, True)

    return Token(access_token=create_admin_token(form_data.username, dbadmin.is_sudo))


@router.post(
    "/admin",
    response_model=Admin,
    responses={403: responses._403, 409: responses._409},
)
async def create_admin(
    new_admin: AdminCreate,
    admin: Admin = Depends(Admin.check_sudo_admin),
):
    """Create a new admin if the current admin has sudo privileges."""
    try:
        dbadmin = await db.create_admin(new_admin)
    except Exception as e:
        if "duplicate" in str(e).lower():
            raise HTTPException(status_code=409, detail="Admin already exists")
        raise HTTPException(status_code=400, detail=str(e))

    return dbadmin


@router.put(
    "/admin/{username}",
    response_model=Admin,
    responses={403: responses._403},
)
async def modify_admin(
    modified_admin: AdminModify,
    dbadmin: Admin = Depends(get_admin_by_username),
    current_admin: Admin = Depends(Admin.check_sudo_admin),
):
    """Modify an existing admin's details."""
    if (dbadmin.username != current_admin.username) and dbadmin.is_sudo:
        raise HTTPException(
            status_code=403,
            detail="You're not allowed to edit another sudoer's account. Use marzban-cli instead.",
        )

    updated_admin = await db.update_admin(dbadmin, modified_admin)

    return updated_admin


@router.delete(
    "/admin/{username}",
    responses={403: responses._403},
)
async def remove_admin(
    dbadmin: Admin = Depends(get_admin_by_username),
    current_admin: Admin = Depends(Admin.check_sudo_admin),
):
    """Remove an admin from the database."""
    if dbadmin.is_sudo:
        raise HTTPException(
            status_code=403,
            detail="You're not allowed to delete sudo accounts. Use marzban-cli instead.",
        )

    await db.remove_admin(dbadmin)
    return {"detail": "Admin removed successfully"}


@router.get("/admin", response_model=Admin)
async def get_current_admin(admin: Admin = Depends(Admin.get_current)):
    """Retrieve the current authenticated admin."""
    return admin


@router.get(
    "/admins",
    response_model=List[Admin],
    responses={403: responses._403},
)
async def get_admins(
    offset: Optional[int] = None,
    limit: Optional[int] = None,
    username: Optional[str] = None,
    admin: Admin = Depends(Admin.check_sudo_admin),
):
    """Fetch a list of admins with optional filters for pagination and username."""
    return await db.get_admins(offset, limit, username)


@router.post("/admin/{username}/users/disable", responses={403: responses._403, 404: responses._404})
async def disable_all_active_users(
    dbadmin: Admin = Depends(get_admin_by_username),
    admin: Admin = Depends(Admin.check_sudo_admin)
):
    """Disable all active users under a specific admin"""
    await db.disable_all_active_users(admin_username=dbadmin.username)
    startup_config = xray.config.include_db_users()
    xray.core.restart(startup_config)
    for node_id, node in list(xray.nodes.items()):
        if node.connected:
            xray.operations.restart_node(node_id, startup_config)
    return {"detail": "Users successfully disabled"}


@router.post("/admin/{username}/users/activate", responses={403: responses._403, 404: responses._404})
async def activate_all_disabled_users(
    dbadmin: Admin = Depends(get_admin_by_username),
    admin: Admin = Depends(Admin.check_sudo_admin)
):
    """Activate all disabled users under a specific admin"""
    await db.activate_all_disabled_users(admin_username=dbadmin.username)
    startup_config = xray.config.include_db_users()
    xray.core.restart(startup_config)
    for node_id, node in list(xray.nodes.items()):
        if node.connected:
            xray.operations.restart_node(node_id, startup_config)
    return {"detail": "Users successfully activated"}


@router.post(
    "/admin/usage/reset/{username}",
    response_model=Admin,
    responses={403: responses._403},
)
async def reset_admin_usage(
    dbadmin: Admin = Depends(get_admin_by_username),
    current_admin: Admin = Depends(Admin.check_sudo_admin)
):
    """Resets usage of admin."""
    return await db.reset_admin_usage(dbadmin)


@router.get(
    "/admin/usage/{username}",
    response_model=int,
    responses={403: responses._403},
)
async def get_admin_usage(
    dbadmin: Admin = Depends(get_admin_by_username),
    current_admin: Admin = Depends(Admin.check_sudo_admin)
):
    """Retrieve the usage of given admin."""
    return dbadmin.users_usage