# app/db/__init__.py
from typing import AsyncGenerator
from motor.motor_asyncio import AsyncIOMotorDatabase

from .base import mongodb, connect_to_mongo, close_mongo_connection, get_database
from .crud import *
from .models import *

class GetDB:
    """Context Manager for MongoDB database"""
    def __init__(self):
        self.db = None

    async def __aenter__(self):
        self.db = get_database()
        return self.db

    async def __aexit__(self, exc_type, exc_value, traceback):
        # MongoDB doesn't need explicit cleanup like SQLAlchemy
        pass

async def get_db() -> AsyncGenerator[AsyncIOMotorDatabase, None]:
    """Dependency for FastAPI to get database instance"""
    db = get_database()
    try:
        yield db
    finally:
        # No cleanup needed for MongoDB
        pass

__all__ = [
    # Connection functions
    "connect_to_mongo",
    "close_mongo_connection", 
    "get_database",
    "GetDB",
    "get_db",
    
    # CRUD functions
    "get_or_create_inbound",
    "get_user",
    "get_user_by_id", 
    "get_users",
    "get_users_count",
    "create_user",
    "remove_user",
    "update_user",
    "get_system_usage",
    "get_jwt_secret_key",
    "get_tls_certificate",
    "get_admin",
    "create_admin",
    "get_admin_by_id",
    "create_notification_reminder",
    "get_notification_reminder",
    "delete_notification_reminder",
    "get_node",
    "get_node_by_id",
    "create_node",
    
    # Models
    "User",
    "Admin",
    "System",
    "JWT",
    "TLS",
    "Node",
    "Proxy",
    "ProxyHost",
    "ProxyInbound",
    "UserTemplate",
    "NotificationReminder",
    "NextPlan",
]