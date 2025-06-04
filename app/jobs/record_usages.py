# app/jobs/record_usages.py
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from operator import attrgetter
from typing import Union
import asyncio

from app import scheduler, xray, db
from config import (
    DISABLE_RECORDING_NODE_USAGE,
    JOB_RECORD_NODE_USAGES_INTERVAL,
    JOB_RECORD_USER_USAGES_INTERVAL,
)
from xray_api import XRay as XRayAPI
from xray_api import exc as xray_exc


async def record_user_stats(params: list, node_id: Union[str, None],
                           consumption_factor: int = 1):
    if not params:
        return

    created_at = datetime.fromisoformat(datetime.utcnow().strftime('%Y-%m-%dT%H:00:00'))

    # Get existing usage records for this hour
    existing_usages = await db.get_node_user_usages_for_hour(node_id, created_at)
    existing_user_ids = {str(usage.user_id) for usage in existing_usages}
    
    # Create missing user usage records
    uids_to_insert = set()
    for p in params:
        uid = p['uid']
        if uid not in existing_user_ids:
            uids_to_insert.add(uid)
    
    if uids_to_insert:
        await db.create_node_user_usages(list(uids_to_insert), node_id, created_at)
    
    # Update usage records
    for p in params:
        await db.update_node_user_usage(
            p['uid'], 
            node_id, 
            created_at, 
            p['value'] * consumption_factor
        )


async def record_node_stats(params: dict, node_id: Union[str, None]):
    if not params:
        return

    created_at = datetime.fromisoformat(datetime.utcnow().strftime('%Y-%m-%dT%H:00:00'))

    # Check if node usage record exists for this hour
    existing_usage = await db.get_node_usage_for_hour(node_id, created_at)
    
    if not existing_usage:
        await db.create_node_usage(node_id, created_at)
    
    # Update usage
    await db.update_node_usage(node_id, created_at, params['up'], params['down'])


def get_users_stats(api: XRayAPI):
    try:
        params = defaultdict(int)
        for stat in filter(attrgetter('value'), api.get_users_stats(reset=True, timeout=30)):
            params[stat.name.split('.', 1)[0]] += stat.value
        params = list({"uid": uid, "value": value} for uid, value in params.items())
        return params
    except xray_exc.XrayError:
        return []


def get_outbounds_stats(api: XRayAPI):
    try:
        params = [{"up": stat.value, "down": 0} if stat.link == "uplink" else {"up": 0, "down": stat.value}
                  for stat in filter(attrgetter('value'), api.get_outbounds_stats(reset=True, timeout=10))]
        return params
    except xray_exc.XrayError:
        return []


def record_user_usages():
    api_instances = {None: xray.api}
    usage_coefficient = {None: 1}  # default usage coefficient for the main api instance

    for node_id, node in list(xray.nodes.items()):
        if node.connected and node.started:
            api_instances[node_id] = node.api
            usage_coefficient[node_id] = node.usage_coefficient  # fetch the usage coefficient

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {node_id: executor.submit(get_users_stats, api) for node_id, api in api_instances.items()}
    api_params = {node_id: future.result() for node_id, future in futures.items()}

    users_usage = defaultdict(int)
    for node_id, params in api_params.items():
        coefficient = usage_coefficient.get(node_id, 1)  # get the usage coefficient for the node
        for param in params:
            users_usage[param['uid']] += int(param['value'] * coefficient)  # apply the usage coefficient
    users_usage = list({"uid": uid, "value": value} for uid, value in users_usage.items())
    
    if not users_usage:
        return

    # Run async operations
    async def update_users_usage():
        # Get user-admin mapping
        user_admin_map = await db.get_user_admin_mapping()
        
        admin_usage = defaultdict(int)
        for user_usage in users_usage:
            admin_id = user_admin_map.get(user_usage["uid"])
            if admin_id:
                admin_usage[admin_id] += user_usage["value"]

        # Update users usage
        await db.update_users_usage(users_usage)
        
        # Update admin usage
        if admin_usage:
            await db.update_admins_usage(admin_usage)

        if DISABLE_RECORDING_NODE_USAGE:
            return

        # Record node-specific usage
        for node_id, params in api_params.items():
            await record_user_stats(params, node_id, usage_coefficient[node_id])
    
    # Run the async function
    asyncio.create_task(update_users_usage())


def record_node_usages():
    api_instances = {None: xray.api}
    for node_id, node in list(xray.nodes.items()):
        if node.connected and node.started:
            api_instances[node_id] = node.api

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {node_id: executor.submit(get_outbounds_stats, api) for node_id, api in api_instances.items()}
    api_params = {node_id: future.result() for node_id, future in futures.items()}

    total_up = 0
    total_down = 0
    for node_id, params in api_params.items():
        for param in params:
            total_up += param['up']
            total_down += param['down']
    if not (total_up or total_down):
        return

    # Run async operations
    async def update_system_usage():
        # Update system usage
        await db.update_system_usage(total_up, total_down)
        
        if DISABLE_RECORDING_NODE_USAGE:
            return

        # Record individual node usage
        for node_id, params in api_params.items():
            for param in params:
                await record_node_stats(param, node_id)
    
    # Run the async function
    asyncio.create_task(update_system_usage())


scheduler.add_job(record_user_usages, 'interval',
                  seconds=JOB_RECORD_USER_USAGES_INTERVAL,
                  coalesce=True, max_instances=1)
scheduler.add_job(record_node_usages, 'interval',
                  seconds=JOB_RECORD_NODE_USAGES_INTERVAL,
                  coalesce=True, max_instances=1)