# app/db/crud.py
"""
Functions for managing proxy hosts, users, user templates, nodes, and administrative tasks with MongoDB.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection
import pymongo

from app.db.base import get_database, COLLECTIONS
from app.db.models import (
    Admin, AdminUsageLogs, NextPlan, Node, NodeUsage, NodeUserUsage,
    NotificationReminder, Proxy, ProxyHost, ProxyInbound, System, TLS, JWT,
    User, UserTemplate, UserUsageResetLogs, PyObjectId
)
from app.models.admin import AdminCreate, AdminModify, AdminPartialModify
from app.models.node import NodeCreate, NodeModify, NodeStatus, NodeUsageResponse
from app.models.proxy import ProxyHost as ProxyHostModify
from app.models.user import (
    ReminderType, UserCreate, UserDataLimitResetStrategy,
    UserModify, UserResponse, UserStatus, UserUsageResponse,
)
from app.models.user_template import UserTemplateCreate, UserTemplateModify
from app.utils.helpers import calculate_expiration_days, calculate_usage_percent
from config import NOTIFY_DAYS_LEFT, NOTIFY_REACHED_USAGE_PERCENT, USERS_AUTODELETE_DAYS

# Helper functions for database operations
def get_collection(collection_name: str) -> AsyncIOMotorCollection:
    """Get MongoDB collection"""
    db = get_database()
    return db[COLLECTIONS[collection_name]]

async def create_indexes():
    """Create necessary indexes for MongoDB collections"""
    # User indexes
    users_col = get_collection("users")
    await users_col.create_index("username", unique=True)
    await users_col.create_index("admin_id")
    await users_col.create_index("status")
    
    # Admin indexes
    admins_col = get_collection("admins")
    await admins_col.create_index("username", unique=True)
    
    # Node indexes
    nodes_col = get_collection("nodes")
    await nodes_col.create_index("name", unique=True)
    
    # ProxyInbound indexes
    inbounds_col = get_collection("proxy_inbounds")
    await inbounds_col.create_index("tag", unique=True)
    
    # Usage indexes
    node_user_usages_col = get_collection("node_user_usages") 
    await node_user_usages_col.create_index([
        ("created_at", 1), ("user_id", 1), ("node_id", 1)
    ], unique=True)
    
    node_usages_col = get_collection("node_usages")
    await node_usages_col.create_index([
        ("created_at", 1), ("node_id", 1)
    ], unique=True)

# Proxy Host Operations
async def add_default_host(inbound_tag: str):
    """Adds a default host to a proxy inbound."""
    host_data = {
        "remark": "🚀 Marz ({USERNAME}) [{PROTOCOL} - {TRANSPORT}]",
        "address": "{SERVER_IP}",
        "inbound_tag": inbound_tag
    }
    host = ProxyHost(**host_data)
    hosts_col = get_collection("proxy_hosts")
    await hosts_col.insert_one(host.model_dump(by_alias=True))

async def get_or_create_inbound(inbound_tag: str) -> ProxyInbound:
    """Retrieves or creates a proxy inbound based on the given tag."""
    inbounds_col = get_collection("proxy_inbounds")
    inbound_doc = await inbounds_col.find_one({"tag": inbound_tag})
    
    if not inbound_doc:
        inbound = ProxyInbound(tag=inbound_tag)
        await inbounds_col.insert_one(inbound.model_dump(by_alias=True))
        await add_default_host(inbound_tag)
        inbound_doc = await inbounds_col.find_one({"tag": inbound_tag})
    
    return ProxyInbound(**inbound_doc)

async def get_hosts(inbound_tag: str) -> List[ProxyHost]:
    """Retrieves hosts for a given inbound tag."""
    await get_or_create_inbound(inbound_tag)
    hosts_col = get_collection("proxy_hosts")
    hosts_docs = await hosts_col.find({"inbound_tag": inbound_tag}).to_list(length=None)
    return [ProxyHost(**doc) for doc in hosts_docs]

async def add_host(inbound_tag: str, host: ProxyHostModify) -> List[ProxyHost]:
    """Adds a new host to a proxy inbound."""
    await get_or_create_inbound(inbound_tag)
    
    host_data = host.model_dump()
    host_data["inbound_tag"] = inbound_tag
    new_host = ProxyHost(**host_data)
    
    hosts_col = get_collection("proxy_hosts")
    await hosts_col.insert_one(new_host.model_dump(by_alias=True))
    
    return await get_hosts(inbound_tag)

async def update_hosts(inbound_tag: str, modified_hosts: List[ProxyHostModify]) -> List[ProxyHost]:
    """Updates hosts for a given inbound tag."""
    await get_or_create_inbound(inbound_tag)
    
    hosts_col = get_collection("proxy_hosts")
    # Delete existing hosts for this inbound
    await hosts_col.delete_many({"inbound_tag": inbound_tag})
    
    # Insert new hosts
    if modified_hosts:
        hosts_data = []
        for host in modified_hosts:
            host_data = host.model_dump()
            host_data["inbound_tag"] = inbound_tag
            hosts_data.append(ProxyHost(**host_data).model_dump(by_alias=True))
        
        await hosts_col.insert_many(hosts_data)
    
    return await get_hosts(inbound_tag)

# User Operations
async def get_user(username: str) -> Optional[User]:
    """Retrieves a user by username."""
    users_col = get_collection("users")
    user_doc = await users_col.find_one({"username": username})
    return User(**user_doc) if user_doc else None

async def get_user_by_id(user_id: Union[str, ObjectId]) -> Optional[User]:
    """Retrieves a user by user ID."""
    users_col = get_collection("users")
    user_doc = await users_col.find_one({"_id": ObjectId(user_id)})
    return User(**user_doc) if user_doc else None

async def get_users(
    offset: Optional[int] = None,
    limit: Optional[int] = None,
    usernames: Optional[List[str]] = None,
    search: Optional[str] = None,
    status: Optional[Union[UserStatus, list]] = None,
    admin_username: Optional[str] = None,
    reset_strategy: Optional[Union[UserDataLimitResetStrategy, list]] = None,
    return_with_count: bool = False
) -> Union[List[User], Tuple[List[User], int]]:
    """Retrieves users based on various filters and options."""
    users_col = get_collection("users")
    
    # Build query
    query = {}
    
    if search:
        query["$or"] = [
            {"username": {"$regex": search, "$options": "i"}},
            {"note": {"$regex": search, "$options": "i"}}
        ]
    
    if usernames:
        query["username"] = {"$in": usernames}
    
    if status:
        if isinstance(status, list):
            query["status"] = {"$in": status}
        else:
            query["status"] = status
    
    if reset_strategy:
        if isinstance(reset_strategy, list):
            query["data_limit_reset_strategy"] = {"$in": reset_strategy}
        else:
            query["data_limit_reset_strategy"] = reset_strategy
    
    if admin_username:
        # Get admin ID first
        admin = await get_admin(admin_username)
        if admin:
            query["admin_id"] = admin.id
    
    # Get count if needed
    count = 0
    if return_with_count:
        count = await users_col.count_documents(query)
    
    # Build cursor
    cursor = users_col.find(query)
    
    # Apply pagination
    if offset:
        cursor = cursor.skip(offset)
    if limit:
        cursor = cursor.limit(limit)
    
    # Execute query
    users_docs = await cursor.to_list(length=None)
    users = [User(**doc) for doc in users_docs]
    
    if return_with_count:
        return users, count
    return users

async def get_user_usages(user: User, start: datetime, end: datetime) -> List[UserUsageResponse]:
    """Retrieves user usages within a specified date range."""
    usages = {None: UserUsageResponse(
        node_id=None,
        node_name="Master", 
        used_traffic=0
    )}
    
    # Get all nodes
    nodes_col = get_collection("nodes")
    nodes_docs = await nodes_col.find({}).to_list(length=None)
    for node_doc in nodes_docs:
        node = Node(**node_doc)
        usages[str(node.id)] = UserUsageResponse(
            node_id=str(node.id),
            node_name=node.name,
            used_traffic=0
        )
    
    # Get usage data
    node_user_usages_col = get_collection("node_user_usages")
    usage_docs = await node_user_usages_col.find({
        "user_id": user.id,
        "created_at": {"$gte": start, "$lte": end}
    }).to_list(length=None)
    
    for usage_doc in usage_docs:
        usage = NodeUserUsage(**usage_doc)
        node_key = str(usage.node_id) if usage.node_id else None
        if node_key in usages:
            usages[node_key].used_traffic += usage.used_traffic
    
    return list(usages.values())

async def get_users_count(status: UserStatus = None, admin_username: str = None) -> int:
    """Retrieves the count of users based on status and admin filters."""
    users_col = get_collection("users")
    query = {}
    
    if status:
        query["status"] = status
    
    if admin_username:
        admin = await get_admin(admin_username)
        if admin:
            query["admin_id"] = admin.id
    
    return await users_col.count_documents(query)

async def create_user(user: UserCreate, admin_username: str = None) -> User:
    """Creates a new user with provided details."""
    user_data = user.model_dump()
    
    # Handle admin relationship
    if admin_username:
        admin = await get_admin(admin_username)
        if admin:
            user_data["admin_id"] = admin.id
    
    # Create user document
    db_user = User(**user_data)
    
    # Insert user
    users_col = get_collection("users")
    result = await users_col.insert_one(db_user.model_dump(by_alias=True))
    
    # Create proxies
    proxies_col = get_collection("proxies")
    for proxy_type, settings in user.proxies.items():
        excluded_tags = user.excluded_inbounds.get(proxy_type, [])
        proxy = Proxy(
            user_id=result.inserted_id,
            type=proxy_type,
            settings=settings.model_dump(),
            excluded_inbound_tags=excluded_tags
        )
        await proxies_col.insert_one(proxy.model_dump(by_alias=True))
    
    # Create next plan if provided
    if user.next_plan:
        next_plans_col = get_collection("next_plans")
        next_plan = NextPlan(
            user_id=result.inserted_id,
            data_limit=user.next_plan.data_limit,
            expire=user.next_plan.expire,
            add_remaining_traffic=user.next_plan.add_remaining_traffic,
            fire_on_either=user.next_plan.fire_on_either
        )
        await next_plans_col.insert_one(next_plan.model_dump(by_alias=True))
    
    # Return created user
    return await get_user_by_id(result.inserted_id)

async def remove_user(user: User) -> User:
    """Removes a user from the database."""
    users_col = get_collection("users")
    proxies_col = get_collection("proxies")
    next_plans_col = get_collection("next_plans")
    
    # Delete related documents
    await proxies_col.delete_many({"user_id": user.id})
    await next_plans_col.delete_many({"user_id": user.id})
    
    # Delete user
    await users_col.delete_one({"_id": user.id})
    
    return user

async def update_user(user: User, modify: UserModify) -> User:
    """Updates a user with new details."""
    update_data = {}
    
    # Handle basic fields
    for field, value in modify.model_dump(exclude_unset=True).items():
        if field not in ['proxies', 'excluded_inbounds', 'next_plan']:
            update_data[field] = value
    
    # Update edit timestamp
    update_data["edit_at"] = datetime.utcnow()
    
    # Update user document
    users_col = get_collection("users")
    await users_col.update_one(
        {"_id": user.id},
        {"$set": update_data}
    )
    
    # Handle proxies update
    if modify.proxies:
        proxies_col = get_collection("proxies")
        
        # Delete existing proxies
        await proxies_col.delete_many({"user_id": user.id})
        
        # Insert new proxies
        for proxy_type, settings in modify.proxies.items():
            excluded_tags = modify.excluded_inbounds.get(proxy_type, [])
            proxy = Proxy(
                user_id=user.id,
                type=proxy_type,
                settings=settings.model_dump(),
                excluded_inbound_tags=excluded_tags
            )
            await proxies_col.insert_one(proxy.model_dump(by_alias=True))
    
    # Handle next plan update
    next_plans_col = get_collection("next_plans")
    await next_plans_col.delete_many({"user_id": user.id})
    
    if modify.next_plan:
        next_plan = NextPlan(
            user_id=user.id,
            data_limit=modify.next_plan.data_limit,
            expire=modify.next_plan.expire,
            add_remaining_traffic=modify.next_plan.add_remaining_traffic,
            fire_on_either=modify.next_plan.fire_on_either
        )
        await next_plans_col.insert_one(next_plan.model_dump(by_alias=True))
    
    return await get_user_by_id(user.id)

# Admin Operations
async def get_admin(username: str) -> Optional[Admin]:
    """Retrieves an admin by username."""
    admins_col = get_collection("admins")
    admin_doc = await admins_col.find_one({"username": username})
    return Admin(**admin_doc) if admin_doc else None

async def create_admin(admin: AdminCreate) -> Admin:
    """Creates a new admin in the database."""
    admin_data = admin.model_dump()
    admin_data["hashed_password"] = admin.hashed_password
    
    db_admin = Admin(**admin_data)
    
    admins_col = get_collection("admins")
    result = await admins_col.insert_one(db_admin.model_dump(by_alias=True))
    
    return await get_admin_by_id(result.inserted_id)

async def get_admin_by_id(admin_id: Union[str, ObjectId]) -> Optional[Admin]:
    """Retrieves an admin by their ID."""
    admins_col = get_collection("admins")
    admin_doc = await admins_col.find_one({"_id": ObjectId(admin_id)})
    return Admin(**admin_doc) if admin_doc else None

# System Operations
async def get_system_usage() -> Optional[System]:
    """Retrieves system usage information."""
    system_col = get_collection("system")
    system_doc = await system_col.find_one({})
    return System(**system_doc) if system_doc else None

async def get_jwt_secret_key() -> str:
    """Retrieves the JWT secret key."""
    jwt_col = get_collection("jwt")
    jwt_doc = await jwt_col.find_one({})
    if not jwt_doc:
        # Create default JWT secret
        import os
        jwt_data = JWT(secret_key=os.urandom(32).hex())
        await jwt_col.insert_one(jwt_data.model_dump(by_alias=True))
        return jwt_data.secret_key
    return jwt_doc["secret_key"]

async def get_tls_certificate() -> Optional[TLS]:
    """Retrieves the TLS certificate."""
    tls_col = get_collection("tls")
    tls_doc = await tls_col.find_one({})
    return TLS(**tls_doc) if tls_doc else None

# Node Operations  
async def get_node(name: str) -> Optional[Node]:
    """Retrieves a node by its name."""
    nodes_col = get_collection("nodes")
    node_doc = await nodes_col.find_one({"name": name})
    return Node(**node_doc) if node_doc else None

async def get_node_by_id(node_id: Union[str, ObjectId]) -> Optional[Node]:
    """Retrieves a node by its ID."""
    nodes_col = get_collection("nodes")
    node_doc = await nodes_col.find_one({"_id": ObjectId(node_id)})
    return Node(**node_doc) if node_doc else None

async def create_node(node: NodeCreate) -> Node:
    """Creates a new node in the database."""
    db_node = Node(**node.model_dump())
    
    nodes_col = get_collection("nodes")
    result = await nodes_col.insert_one(db_node.model_dump(by_alias=True))
    
    return await get_node_by_id(result.inserted_id)

# Notification Operations
async def create_notification_reminder(
    reminder_type: ReminderType, 
    expires_at: datetime, 
    user_id: Union[str, ObjectId], 
    threshold: Optional[int] = None
) -> NotificationReminder:
    """Creates a new notification reminder."""
    reminder = NotificationReminder(
        type=reminder_type,
        expires_at=expires_at,
        user_id=ObjectId(user_id),
        threshold=threshold
    )
    
    reminders_col = get_collection("notification_reminders")
    result = await reminders_col.insert_one(reminder.model_dump(by_alias=True))
    
    reminder_doc = await reminders_col.find_one({"_id": result.inserted_id})
    return NotificationReminder(**reminder_doc)

async def get_notification_reminder(
    user_id: Union[str, ObjectId], 
    reminder_type: ReminderType, 
    threshold: Optional[int] = None
) -> Optional[NotificationReminder]:
    """Retrieves a notification reminder for a user."""
    query = {
        "user_id": ObjectId(user_id),
        "type": reminder_type
    }
    
    if threshold is not None:
        query["threshold"] = threshold
    
    reminders_col = get_collection("notification_reminders")
    reminder_doc = await reminders_col.find_one(query)
    
    if not reminder_doc:
        return None
    
    reminder = NotificationReminder(**reminder_doc)
    
    # Check if expired
    if reminder.expires_at and reminder.expires_at < datetime.utcnow():
        await reminders_col.delete_one({"_id": reminder.id})
        return None
    
    return reminder

async def delete_notification_reminder(reminder: NotificationReminder) -> None:
    """Deletes a specific notification reminder."""
    reminders_col = get_collection("notification_reminders")
    await reminders_col.delete_one({"_id": reminder.id})