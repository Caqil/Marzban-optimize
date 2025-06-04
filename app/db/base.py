# app/db/base.py
import os
from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional
import asyncio
from config import MONGODB_URL, MONGODB_DATABASE_NAME

class MongoDB:
    client: Optional[AsyncIOMotorClient] = None
    database = None

mongodb = MongoDB()

async def connect_to_mongo():
    """Create database connection"""
    mongodb.client = AsyncIOMotorClient(MONGODB_URL)
    mongodb.database = mongodb.client[MONGODB_DATABASE_NAME]
    
    # Test connection
    try:
        await mongodb.client.admin.command('ping')
        print(f"Connected to MongoDB: {MONGODB_DATABASE_NAME}")
    except Exception as e:
        print(f"Failed to connect to MongoDB: {e}")
        raise

async def close_mongo_connection():
    """Close database connection"""
    if mongodb.client:
        mongodb.client.close()

def get_database():
    """Get database instance"""
    return mongodb.database

# Collections
COLLECTIONS = {
    "users": "users",
    "admins": "admins", 
    "proxies": "proxies",
    "nodes": "nodes",
    "user_templates": "user_templates",
    "proxy_inbounds": "proxy_inbounds", 
    "proxy_hosts": "proxy_hosts",
    "system": "system",
    "jwt": "jwt",
    "tls": "tls",
    "node_usages": "node_usages",
    "node_user_usages": "node_user_usages",
    "notification_reminders": "notification_reminders",
    "user_usage_logs": "user_usage_logs",
    "admin_usage_logs": "admin_usage_logs",
    "next_plans": "next_plans"
}