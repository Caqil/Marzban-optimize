# Additional functions to add to app/db/crud.py

from bson import ObjectId
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union
from motor.motor_asyncio import AsyncIOMotorCollection
from app.db.base import get_database, COLLECTIONS
from app.db.models import (
    Admin, Node, NodeUsage, NodeUserUsage, System, User, UserTemplate,
    NotificationReminder, AdminUsageLogs, UserUsageResetLogs, PyObjectId
)
from app.models.admin import AdminModify
from app.models.node import NodeModify, NodeStatus, NodeUsageResponse
from app.models.user import UserStatus, UserUsageResponse

# Additional CRUD functions that were missing

async def get_admins(offset: Optional[int] = None, limit: Optional[int] = None, username: Optional[str] = None) -> List[Admin]:
    """Get all admins with optional filtering."""
    admins_col = get_database()[COLLECTIONS["admins"]]
    
    query = {}
    if username:
        query["username"] = {"$regex": username, "$options": "i"}
    
    cursor = admins_col.find(query)
    
    if offset:
        cursor = cursor.skip(offset)
    if limit:
        cursor = cursor.limit(limit)
    
    admins_docs = await cursor.to_list(length=None)
    return [Admin(**doc) for doc in admins_docs]


async def update_admin(admin: Admin, modify: AdminModify) -> Admin:
    """Update admin details."""
    admins_col = get_database()[COLLECTIONS["admins"]]
    
    update_data = modify.model_dump(exclude_unset=True)
    if 'password' in update_data:
        # Hash the password if provided
        from app.models.admin import AdminInDB
        admin_data = AdminInDB(**admin.model_dump())
        admin_data.set_password(update_data['password'])
        update_data['hashed_password'] = admin_data.hashed_password
        del update_data['password']
    
    await admins_col.update_one(
        {"_id": admin.id},
        {"$set": update_data}
    )
    
    updated_doc = await admins_col.find_one({"_id": admin.id})
    return Admin(**updated_doc)


async def remove_admin(admin: Admin) -> None:
    """Remove an admin."""
    admins_col = get_database()[COLLECTIONS["admins"]]
    await admins_col.delete_one({"_id": admin.id})


async def reset_admin_usage(admin: Admin) -> Admin:
    """Reset admin usage."""
    admins_col = get_database()[COLLECTIONS["admins"]]
    admin_usage_logs_col = get_database()[COLLECTIONS["admin_usage_logs"]]
    
    # Log current usage before reset
    log = AdminUsageLogs(
        admin_id=admin.id,
        used_traffic_at_reset=admin.users_usage
    )
    await admin_usage_logs_col.insert_one(log.model_dump(by_alias=True))
    
    # Reset usage
    await admins_col.update_one(
        {"_id": admin.id},
        {"$set": {"users_usage": 0}}
    )
    
    updated_doc = await admins_col.find_one({"_id": admin.id})
    return Admin(**updated_doc)


async def disable_all_active_users(admin_username: str) -> None:
    """Disable all active users under a specific admin."""
    users_col = get_database()[COLLECTIONS["users"]]
    admin = await get_admin(admin_username)
    
    if admin:
        await users_col.update_many(
            {"admin_id": admin.id, "status": UserStatus.active},
            {"$set": {"status": UserStatus.disabled}}
        )


async def activate_all_disabled_users(admin_username: str) -> None:
    """Activate all disabled users under a specific admin."""
    users_col = get_database()[COLLECTIONS["users"]]
    admin = await get_admin(admin_username)
    
    if admin:
        await users_col.update_many(
            {"admin_id": admin.id, "status": UserStatus.disabled},
            {"$set": {"status": UserStatus.active}}
        )


async def reset_user_data_usage(user: User) -> User:
    """Reset user data usage."""
    users_col = get_database()[COLLECTIONS["users"]]
    user_usage_logs_col = get_database()[COLLECTIONS["user_usage_logs"]]
    
    # Log current usage before reset
    log = UserUsageResetLogs(
        user_id=user.id,
        used_traffic_at_reset=user.used_traffic
    )
    await user_usage_logs_col.insert_one(log.model_dump(by_alias=True))
    
    # Reset usage
    await users_col.update_one(
        {"_id": user.id},
        {"$set": {"used_traffic": 0}}
    )
    
    updated_doc = await users_col.find_one({"_id": user.id})
    return User(**updated_doc)


async def revoke_user_sub(user: User) -> User:
    """Revoke user subscription."""
    users_col = get_database()[COLLECTIONS["users"]]
    proxies_col = get_database()[COLLECTIONS["proxies"]]
    
    # Update revoke timestamp
    await users_col.update_one(
        {"_id": user.id},
        {"$set": {"sub_revoked_at": datetime.utcnow()}}
    )
    
    # Regenerate proxy settings (new UUIDs, passwords, etc.)
    proxies = await proxies_col.find({"user_id": user.id}).to_list(length=None)
    for proxy_doc in proxies:
        # Generate new settings based on proxy type
        from app.models.proxy import ProxySettings, ProxyTypes
        proxy_type = ProxyTypes(proxy_doc["type"])
        new_settings = proxy_type.settings_model()
        new_settings.revoke()  # Generate new credentials
        
        await proxies_col.update_one(
            {"_id": proxy_doc["_id"]},
            {"$set": {"settings": new_settings.model_dump()}}
        )
    
    updated_doc = await users_col.find_one({"_id": user.id})
    return User(**updated_doc)


async def reset_user_by_next(user: User) -> Optional[User]:
    """Reset user by next plan."""
    if not user.next_plan:
        return None
    
    users_col = get_database()[COLLECTIONS["users"]]
    next_plans_col = get_database()[COLLECTIONS["next_plans"]]
    
    # Get next plan
    next_plan_doc = await next_plans_col.find_one({"user_id": user.id})
    if not next_plan_doc:
        return None
    
    update_data = {}
    
    # Apply next plan data
    if next_plan_doc.get("data_limit") is not None:
        if next_plan_doc.get("add_remaining_traffic"):
            remaining = max(0, user.data_limit - user.used_traffic) if user.data_limit else 0
            update_data["data_limit"] = next_plan_doc["data_limit"] + remaining
        else:
            update_data["data_limit"] = next_plan_doc["data_limit"]
        
        update_data["used_traffic"] = 0
    
    if next_plan_doc.get("expire") is not None:
        update_data["expire"] = next_plan_doc["expire"]
    
    # Set status to active
    update_data["status"] = UserStatus.active
    
    # Update user
    await users_col.update_one(
        {"_id": user.id},
        {"$set": update_data}
    )
    
    # Remove next plan
    await next_plans_col.delete_one({"user_id": user.id})
    
    updated_doc = await users_col.find_one({"_id": user.id})
    return User(**updated_doc)


async def reset_all_users_data_usage(admin_username: str) -> None:
    """Reset all users data usage for a specific admin."""
    users_col = get_database()[COLLECTIONS["users"]]
    
    if admin_username:
        admin = await get_admin(admin_username)
        if admin:
            await users_col.update_many(
                {"admin_id": admin.id},
                {"$set": {"used_traffic": 0}}
            )
    else:
        # Super admin - reset all users
        await users_col.update_many(
            {},
            {"$set": {"used_traffic": 0}}
        )


async def set_owner(user: User, new_admin: Admin) -> User:
    """Set new owner (admin) for a user."""
    users_col = get_database()[COLLECTIONS["users"]]
    
    await users_col.update_one(
        {"_id": user.id},
        {"$set": {"admin_id": new_admin.id}}
    )
    
    updated_doc = await users_col.find_one({"_id": user.id})
    return User(**updated_doc)


async def remove_users(users: List[User]) -> None:
    """Remove multiple users."""
    users_col = get_database()[COLLECTIONS["users"]]
    proxies_col = get_database()[COLLECTIONS["proxies"]]
    next_plans_col = get_database()[COLLECTIONS["next_plans"]]
    
    user_ids = [user.id for user in users]
    
    # Delete related documents
    await proxies_col.delete_many({"user_id": {"$in": user_ids}})
    await next_plans_col.delete_many({"user_id": {"$in": user_ids}})
    
    # Delete users
    await users_col.delete_many({"_id": {"$in": user_ids}})


async def count_online_users(hours: int) -> int:
    """Count users online in the last N hours."""
    users_col = get_database()[COLLECTIONS["users"]]
    since = datetime.utcnow() - timedelta(hours=hours)
    
    return await users_col.count_documents({
        "online_at": {"$gte": since}
    })


async def get_all_users_usages(start: datetime, end: datetime, admin_username: Optional[str] = None) -> List[UserUsageResponse]:
    """Get all users usage within date range."""
    usages_col = get_database()[COLLECTIONS["node_user_usages"]]
    users_col = get_database()[COLLECTIONS["users"]]
    nodes_col = get_database()[COLLECTIONS["nodes"]]
    
    # Build user filter if admin specified
    user_filter = {}
    if admin_username:
        admin = await get_admin(admin_username)
        if admin:
            user_filter["admin_id"] = admin.id
    
    # Get users
    users_docs = await users_col.find(user_filter).to_list(length=None)
    user_ids = [doc["_id"] for doc in users_docs]
    
    if not user_ids:
        return []
    
    # Get nodes for mapping
    nodes_docs = await nodes_col.find({}).to_list(length=None)
    nodes_map = {str(doc["_id"]): doc["name"] for doc in nodes_docs}
    nodes_map[None] = "Master"
    
    # Get usage data
    pipeline = [
        {
            "$match": {
                "user_id": {"$in": user_ids},
                "created_at": {"$gte": start, "$lte": end}
            }
        },
        {
            "$group": {
                "_id": "$node_id",
                "total_usage": {"$sum": "$used_traffic"}
            }
        }
    ]
    
    usage_docs = await usages_col.aggregate(pipeline).to_list(length=None)
    
    result = []
    for usage_doc in usage_docs:
        node_id = str(usage_doc["_id"]) if usage_doc["_id"] else None
        result.append(UserUsageResponse(
            node_id=node_id,
            node_name=nodes_map.get(node_id, "Unknown"),
            used_traffic=usage_doc["total_usage"]
        ))
    
    return result


async def update_user_sub(user: User, user_agent: str) -> None:
    """Update user subscription info."""
    users_col = get_database()[COLLECTIONS["users"]]
    
    await users_col.update_one(
        {"_id": user.id},
        {"$set": {
            "sub_updated_at": datetime.utcnow(),
            "sub_last_user_agent": user_agent
        }}
    )


# Node CRUD operations
async def get_nodes(enabled: bool = None) -> List[Node]:
    """Get all nodes."""
    nodes_col = get_database()[COLLECTIONS["nodes"]]
    
    query = {}
    if enabled is not None:
        query["status"] = {"$ne": NodeStatus.disabled} if enabled else NodeStatus.disabled
    
    nodes_docs = await nodes_col.find(query).to_list(length=None)
    return [Node(**doc) for doc in nodes_docs]


async def update_node(node: Node, modify: NodeModify) -> Node:
    """Update node details."""
    nodes_col = get_database()[COLLECTIONS["nodes"]]
    
    update_data = modify.model_dump(exclude_unset=True)
    update_data["last_status_change"] = datetime.utcnow()
    
    await nodes_col.update_one(
        {"_id": node.id},
        {"$set": update_data}
    )
    
    updated_doc = await nodes_col.find_one({"_id": node.id})
    return Node(**updated_doc)


async def remove_node(node: Node) -> None:
    """Remove a node."""
    nodes_col = get_database()[COLLECTIONS["nodes"]]
    await nodes_col.delete_one({"_id": node.id})


async def update_node_status(node: Node, status: NodeStatus, message: str = None, version: str = None) -> None:
    """Update node status."""
    nodes_col = get_database()[COLLECTIONS["nodes"]]
    
    update_data = {
        "status": status,
        "last_status_change": datetime.utcnow()
    }
    
    if message:
        update_data["message"] = message
    if version:
        update_data["xray_version"] = version
    
    await nodes_col.update_one(
        {"_id": node.id},
        {"$set": update_data}
    )


async def get_nodes_usage(start: datetime, end: datetime) -> List[NodeUsageResponse]:
    """Get nodes usage within date range."""
    node_usages_col = get_database()[COLLECTIONS["node_usages"]]
    nodes_col = get_database()[COLLECTIONS["nodes"]]
    
    # Get nodes for mapping
    nodes_docs = await nodes_col.find({}).to_list(length=None)
    nodes_map = {str(doc["_id"]): doc["name"] for doc in nodes_docs}
    nodes_map[None] = "Master"
    
    # Get usage data
    pipeline = [
        {
            "$match": {
                "created_at": {"$gte": start, "$lte": end}
            }
        },
        {
            "$group": {
                "_id": "$node_id",
                "total_uplink": {"$sum": "$uplink"},
                "total_downlink": {"$sum": "$downlink"}
            }
        }
    ]
    
    usage_docs = await node_usages_col.aggregate(pipeline).to_list(length=None)
    
    result = []
    for usage_doc in usage_docs:
        node_id = str(usage_doc["_id"]) if usage_doc["_id"] else None
        result.append(NodeUsageResponse(
            node_id=node_id,
            node_name=nodes_map.get(node_id, "Unknown"),
            uplink=usage_doc["total_uplink"],
            downlink=usage_doc["total_downlink"]
        ))
    
    return result


# User Template CRUD operations
async def get_user_template_by_id(template_id: str) -> Optional[UserTemplate]:
    """Get user template by ID."""
    templates_col = get_database()[COLLECTIONS["user_templates"]]
    template_doc = await templates_col.find_one({"_id": ObjectId(template_id)})
    return UserTemplate(**template_doc) if template_doc else None


async def get_user_templates(offset: Optional[int] = None, limit: Optional[int] = None) -> List[UserTemplate]:
    """Get user templates."""
    templates_col = get_database()[COLLECTIONS["user_templates"]]
    
    cursor = templates_col.find({})
    
    if offset:
        cursor = cursor.skip(offset)
    if limit:
        cursor = cursor.limit(limit)
    
    templates_docs = await cursor.to_list(length=None)
    return [UserTemplate(**doc) for doc in templates_docs]


# System and stats functions
async def update_system_usage(uplink: int, downlink: int) -> None:
    """Update system usage."""
    system_col = get_database()[COLLECTIONS["system"]]
    
    # Create system record if it doesn't exist
    existing = await system_col.find_one({})
    if not existing:
        system = System(uplink=uplink, downlink=downlink)
        await system_col.insert_one(system.model_dump(by_alias=True))
    else:
        await system_col.update_one(
            {"_id": existing["_id"]},
            {"$inc": {"uplink": uplink, "downlink": downlink}}
        )


# Usage tracking functions for jobs
async def get_user_admin_mapping() -> Dict[str, str]:
    """Get mapping of user_id to admin_id."""
    users_col = get_database()[COLLECTIONS["users"]]
    
    pipeline = [
        {"$match": {"admin_id": {"$exists": True, "$ne": None}}},
        {"$project": {"admin_id": 1}}
    ]
    
    users_docs = await users_col.aggregate(pipeline).to_list(length=None)
    return {str(doc["_id"]): str(doc["admin_id"]) for doc in users_docs}


async def update_users_usage(users_usage: List[Dict]) -> None:
    """Update multiple users usage."""
    users_col = get_database()[COLLECTIONS["users"]]
    
    for usage in users_usage:
        await users_col.update_one(
            {"_id": ObjectId(usage["uid"])},
            {"$inc": {"used_traffic": usage["value"]}, "$set": {"online_at": datetime.utcnow()}}
        )


async def update_admins_usage(admin_usage: Dict[str, int]) -> None:
    """Update multiple admins usage."""
    admins_col = get_database()[COLLECTIONS["admins"]]
    
    for admin_id, usage in admin_usage.items():
        await admins_col.update_one(
            {"_id": ObjectId(admin_id)},
            {"$inc": {"users_usage": usage}}
        )


async def get_node_user_usages_for_hour(node_id: Optional[str], created_at: datetime) -> List[NodeUserUsage]:
    """Get node user usages for a specific hour."""
    usages_col = get_database()[COLLECTIONS["node_user_usages"]]
    
    query = {
        "created_at": created_at,
        "node_id": ObjectId(node_id) if node_id else None
    }
    
    usages_docs = await usages_col.find(query).to_list(length=None)
    return [NodeUserUsage(**doc) for doc in usages_docs]


async def create_node_user_usages(user_ids: List[str], node_id: Optional[str], created_at: datetime) -> None:
    """Create node user usage records."""
    usages_col = get_database()[COLLECTIONS["node_user_usages"]]
    
    usages_data = []
    for user_id in user_ids:
        usage = NodeUserUsage(
            user_id=ObjectId(user_id),
            node_id=ObjectId(node_id) if node_id else None,
            created_at=created_at,
            used_traffic=0
        )
        usages_data.append(usage.model_dump(by_alias=True))
    
    if usages_data:
        await usages_col.insert_many(usages_data)


async def update_node_user_usage(user_id: str, node_id: Optional[str], created_at: datetime, usage: int) -> None:
    """Update node user usage."""
    usages_col = get_database()[COLLECTIONS["node_user_usages"]]
    
    await usages_col.update_one(
        {
            "user_id": ObjectId(user_id),
            "node_id": ObjectId(node_id) if node_id else None,
            "created_at": created_at
        },
        {"$inc": {"used_traffic": usage}}
    )


async def get_node_usage_for_hour(node_id: Optional[str], created_at: datetime) -> Optional[NodeUsage]:
    """Get node usage for a specific hour."""
    usages_col = get_database()[COLLECTIONS["node_usages"]]
    
    usage_doc = await usages_col.find_one({
        "node_id": ObjectId(node_id) if node_id else None,
        "created_at": created_at
    })
    
    return NodeUsage(**usage_doc) if usage_doc else None


async def create_node_usage(node_id: Optional[str], created_at: datetime) -> None:
    """Create node usage record."""
    usages_col = get_database()[COLLECTIONS["node_usages"]]
    
    usage = NodeUsage(
        node_id=ObjectId(node_id) if node_id else None,
        created_at=created_at,
        uplink=0,
        downlink=0
    )
    
    await usages_col.insert_one(usage.model_dump(by_alias=True))


async def update_node_usage(node_id: Optional[str], created_at: datetime, uplink: int, downlink: int) -> None:
    """Update node usage."""
    usages_col = get_database()[COLLECTIONS["node_usages"]]
    
    await usages_col.update_one(
        {
            "node_id": ObjectId(node_id) if node_id else None,
            "created_at": created_at
        },
        {"$inc": {"uplink": uplink, "downlink": downlink}}
    )