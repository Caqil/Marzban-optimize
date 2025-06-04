# app/routers/system.py
from typing import Dict, List, Union

from fastapi import APIRouter, Depends, HTTPException

from app import __version__, xray, db
from app.models.admin import Admin
from app.models.proxy import ProxyHost, ProxyInbound, ProxyTypes
from app.models.system import SystemStats
from app.models.user import UserStatus
from app.utils import responses
from app.utils.system import cpu_usage, memory_usage, realtime_bandwidth

router = APIRouter(tags=["System"], prefix="/api", responses={401: responses._401})


@router.get("/system", response_model=SystemStats)
async def get_system_stats(admin: Admin = Depends(Admin.get_current)):
    """Fetch system stats including memory, CPU, and user metrics."""
    mem = memory_usage()
    cpu = cpu_usage()
    system = await db.get_system_usage()
    
    admin_filter = None if admin.is_sudo else admin.username

    total_user = await db.get_users_count(admin_username=admin_filter)
    users_active = await db.get_users_count(
        status=UserStatus.active, admin_username=admin_filter
    )
    users_disabled = await db.get_users_count(
        status=UserStatus.disabled, admin_username=admin_filter
    )
    users_on_hold = await db.get_users_count(
        status=UserStatus.on_hold, admin_username=admin_filter
    )
    users_expired = await db.get_users_count(
        status=UserStatus.expired, admin_username=admin_filter
    )
    users_limited = await db.get_users_count(
        status=UserStatus.limited, admin_username=admin_filter
    )
    online_users = await db.count_online_users(24)
    realtime_bandwidth_stats = realtime_bandwidth()

    return SystemStats(
        version=__version__,
        mem_total=mem.total,
        mem_used=mem.used,
        cpu_cores=cpu.cores,
        cpu_usage=cpu.percent,
        total_user=total_user,
        online_users=online_users,
        users_active=users_active,
        users_disabled=users_disabled,
        users_expired=users_expired,
        users_limited=users_limited,
        users_on_hold=users_on_hold,
        incoming_bandwidth=system.uplink if system else 0,
        outgoing_bandwidth=system.downlink if system else 0,
        incoming_bandwidth_speed=realtime_bandwidth_stats.incoming_bytes,
        outgoing_bandwidth_speed=realtime_bandwidth_stats.outgoing_bytes,
    )


@router.get("/inbounds", response_model=Dict[ProxyTypes, List[ProxyInbound]])
async def get_inbounds(admin: Admin = Depends(Admin.get_current)):
    """Retrieve inbound configurations grouped by protocol."""
    return xray.config.inbounds_by_protocol


@router.get(
    "/hosts", response_model=Dict[str, List[ProxyHost]], responses={403: responses._403}
)
async def get_hosts(admin: Admin = Depends(Admin.check_sudo_admin)):
    """Get a list of proxy hosts grouped by inbound tag."""
    hosts = {}
    for tag in xray.config.inbounds_by_tag:
        hosts[tag] = await db.get_hosts(tag)
    return hosts


@router.put(
    "/hosts", response_model=Dict[str, List[ProxyHost]], responses={403: responses._403}
)
async def modify_hosts(
    modified_hosts: Dict[str, List[ProxyHost]],
    admin: Admin = Depends(Admin.check_sudo_admin),
):
    """Modify proxy hosts and update the configuration."""
    for inbound_tag in modified_hosts:
        if inbound_tag not in xray.config.inbounds_by_tag:
            raise HTTPException(
                status_code=400, detail=f"Inbound {inbound_tag} doesn't exist"
            )

    for inbound_tag, hosts in modified_hosts.items():
        await db.update_hosts(inbound_tag, hosts)

    xray.hosts.update()

    hosts_result = {}
    for tag in xray.config.inbounds_by_tag:
        hosts_result[tag] = await db.get_hosts(tag)
    return hosts_result