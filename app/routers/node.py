import asyncio
import time
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, WebSocket
from starlette.websockets import WebSocketDisconnect

from app import logger, xray, db
from app.dependencies import get_dbnode, validate_dates
from app.models.admin import Admin
from app.models.node import (
    NodeCreate,
    NodeModify,
    NodeResponse,
    NodeSettings,
    NodeStatus,
    NodesUsageResponse,
)
from app.models.proxy import ProxyHost
from app.utils import responses

router = APIRouter(
    tags=["Node"], prefix="/api", responses={401: responses._401, 403: responses._403}
)


async def add_host_if_needed(new_node: NodeCreate):
    """Add a host if specified in the new node settings."""
    if new_node.add_as_new_host:
        host = ProxyHost(
            remark=f"{new_node.name} ({{USERNAME}}) [{{PROTOCOL}} - {{TRANSPORT}}]",
            address=new_node.address,
        )
        for inbound_tag in xray.config.inbounds_by_tag:
            await db.add_host(inbound_tag, host)
        xray.hosts.update()


@router.get("/node/settings", response_model=NodeSettings)
async def get_node_settings(admin: Admin = Depends(Admin.check_sudo_admin)):
    """Retrieve the current node settings, including TLS certificate."""
    tls = await db.get_tls_certificate()
    return NodeSettings(certificate=tls.certificate)


@router.post("/node", response_model=NodeResponse, responses={409: responses._409})
async def add_node(
    new_node: NodeCreate,
    bg: BackgroundTasks,
    _: Admin = Depends(Admin.check_sudo_admin),
):
    """Add a new node to the database and optionally add it as a host."""
    try:
        dbnode = await db.create_node(new_node)
    except Exception as e:
        if "duplicate" in str(e).lower():
            raise HTTPException(
                status_code=409, detail=f'Node "{new_node.name}" already exists'
            )
        raise HTTPException(status_code=400, detail=str(e))

    bg.add_task(xray.operations.connect_node, node_id=str(dbnode.id))
    bg.add_task(add_host_if_needed, new_node)

    logger.info(f'New node "{dbnode.name}" added')
    return dbnode


@router.get("/node/{node_id}", response_model=NodeResponse)
async def get_node(
    dbnode: NodeResponse = Depends(get_dbnode),
    _: Admin = Depends(Admin.check_sudo_admin),
):
    """Retrieve details of a specific node by its ID."""
    return dbnode


@router.websocket("/node/{node_id}/logs")
async def node_logs(node_id: str, websocket: WebSocket):
    token = websocket.query_params.get("token") or websocket.headers.get(
        "Authorization", ""
    ).removeprefix("Bearer ")
    
    # Validate admin token
    from app.utils.jwt import get_admin_payload
    admin_data = get_admin_payload(token)
    if not admin_data:
        return await websocket.close(reason="Unauthorized", code=4401)

    if not admin_data.get("is_sudo"):
        return await websocket.close(reason="You're not allowed", code=4403)

    # Convert string node_id to int for xray.nodes lookup
    try:
        node_id_int = int(node_id)
    except ValueError:
        return await websocket.close(reason="Invalid node ID", code=4400)

    if not xray.nodes.get(node_id_int):
        return await websocket.close(reason="Node not found", code=4404)

    if not xray.nodes[node_id_int].connected:
        return await websocket.close(reason="Node is not connected", code=4400)

    interval = websocket.query_params.get("interval")
    if interval:
        try:
            interval = float(interval)
        except ValueError:
            return await websocket.close(reason="Invalid interval value", code=4400)
        if interval > 10:
            return await websocket.close(
                reason="Interval must be more than 0 and at most 10 seconds", code=4400
            )

    await websocket.accept()

    cache = ""
    last_sent_ts = 0
    node = xray.nodes[node_id_int]
    with node.get_logs() as logs:
        while True:
            if not node == xray.nodes[node_id_int]:
                break

            if interval and time.time() - last_sent_ts >= interval and cache:
                try:
                    await websocket.send_text(cache)
                except (WebSocketDisconnect, RuntimeError):
                    break
                cache = ""
                last_sent_ts = time.time()

            if not logs:
                try:
                    await asyncio.wait_for(websocket.receive(), timeout=0.2)
                    continue
                except asyncio.TimeoutError:
                    continue
                except (WebSocketDisconnect, RuntimeError):
                    break

            log = logs.popleft()

            if interval:
                cache += f"{log}\n"
                continue

            try:
                await websocket.send_text(log)
            except (WebSocketDisconnect, RuntimeError):
                break


@router.get("/nodes", response_model=List[NodeResponse])
async def get_nodes(_: Admin = Depends(Admin.check_sudo_admin)):
    """Retrieve a list of all nodes. Accessible only to sudo admins."""
    return await db.get_nodes()


@router.put("/node/{node_id}", response_model=NodeResponse)
async def modify_node(
    modified_node: NodeModify,
    bg: BackgroundTasks,
    dbnode: NodeResponse = Depends(get_dbnode),
    _: Admin = Depends(Admin.check_sudo_admin),
):
    """Update a node's details. Only accessible to sudo admins."""
    updated_node = await db.update_node(dbnode, modified_node)
    xray.operations.remove_node(str(updated_node.id))
    if updated_node.status != NodeStatus.disabled:
        bg.add_task(xray.operations.connect_node, node_id=str(updated_node.id))

    logger.info(f'Node "{dbnode.name}" modified')
    return dbnode


@router.post("/node/{node_id}/reconnect")
async def reconnect_node(
    bg: BackgroundTasks,
    dbnode: NodeResponse = Depends(get_dbnode),
    _: Admin = Depends(Admin.check_sudo_admin),
):
    """Trigger a reconnection for the specified node. Only accessible to sudo admins."""
    bg.add_task(xray.operations.connect_node, node_id=str(dbnode.id))
    return {"detail": "Reconnection task scheduled"}


@router.delete("/node/{node_id}")
async def remove_node(
    dbnode: NodeResponse = Depends(get_dbnode),
    admin: Admin = Depends(Admin.check_sudo_admin),
):
    """Delete a node and remove it from xray in the background."""
    await db.remove_node(dbnode)
    xray.operations.remove_node(str(dbnode.id))

    logger.info(f'Node "{dbnode.name}" deleted')
    return {}


@router.get("/nodes/usage", response_model=NodesUsageResponse)
async def get_usage(
    start: str = "",
    end: str = "",
    _: Admin = Depends(Admin.check_sudo_admin),
):
    """Retrieve usage statistics for nodes within a specified date range."""
    start, end = validate_dates(start, end)

    usages = await db.get_nodes_usage(start, end)

    return {"usages": usages}