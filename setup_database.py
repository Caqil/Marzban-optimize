#!/usr/bin/env python3
"""
Standalone MongoDB Database Setup Script
This script initializes MongoDB for Marzban without importing the main app modules
"""

import asyncio
import os
import sys
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

# Add the project root to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import config directly
try:
    from config import MONGODB_URL, MONGODB_DATABASE_NAME
except ImportError:
    # Fallback defaults if config import fails
    MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    MONGODB_DATABASE_NAME = os.getenv("MONGODB_DATABASE_NAME", "marzban")

# Collection names
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

def generate_certificate():
    """Generate self-signed certificate"""
    try:
        from OpenSSL import crypto
        
        k = crypto.PKey()
        k.generate_key(crypto.TYPE_RSA, 4096)
        cert = crypto.X509()
        cert.get_subject().CN = "Gozargah"
        cert.gmtime_adj_notBefore(0)
        cert.gmtime_adj_notAfter(100*365*24*60*60)
        cert.set_issuer(cert.get_subject())
        cert.set_pubkey(k)
        cert.sign(k, 'sha512')
        cert_pem = crypto.dump_certificate(crypto.FILETYPE_PEM, cert).decode("utf-8")
        key_pem = crypto.dump_privatekey(crypto.FILETYPE_PEM, k).decode("utf-8")

        return {
            "cert": cert_pem,
            "key": key_pem
        }
    except ImportError:
        print("⚠️  Warning: pyOpenSSL not available, skipping TLS certificate generation")
        return None

class MongoDBSetup:
    def __init__(self):
        self.client = None
        self.db = None

    async def connect(self):
        """Connect to MongoDB"""
        try:
            self.client = AsyncIOMotorClient(MONGODB_URL)
            self.db = self.client[MONGODB_DATABASE_NAME]
            
            # Test connection
            await self.client.admin.command('ping')
            print(f"✓ Connected to MongoDB: {MONGODB_DATABASE_NAME}")
            return True
        except Exception as e:
            print(f"✗ Failed to connect to MongoDB: {e}")
            return False

    async def close(self):
        """Close MongoDB connection"""
        if self.client:
            self.client.close()

    async def create_collections(self):
        """Create all necessary collections"""
        print("Creating MongoDB collections...")
        
        existing_collections = await self.db.list_collection_names()
        
        for collection_name in COLLECTIONS.values():
            if collection_name not in existing_collections:
                await self.db.create_collection(collection_name)
                print(f"✓ Created collection: {collection_name}")
            else:
                print(f"✓ Collection already exists: {collection_name}")

    async def create_indexes(self):
        """Create necessary indexes for optimal performance"""
        print("Creating database indexes...")
        
        # User indexes
        users_col = self.db[COLLECTIONS["users"]]
        await users_col.create_index("username", unique=True)
        await users_col.create_index("admin_id")
        await users_col.create_index("status")
        await users_col.create_index("created_at")
        print("✓ User indexes created")
        
        # Admin indexes
        admins_col = self.db[COLLECTIONS["admins"]]
        await admins_col.create_index("username", unique=True)
        await admins_col.create_index("telegram_id", sparse=True)
        print("✓ Admin indexes created")
        
        # Node indexes
        nodes_col = self.db[COLLECTIONS["nodes"]]
        await nodes_col.create_index("name", unique=True, sparse=True)
        await nodes_col.create_index("status")
        print("✓ Node indexes created")
        
        # Proxy indexes
        proxies_col = self.db[COLLECTIONS["proxies"]]
        await proxies_col.create_index("user_id")
        await proxies_col.create_index("type")
        print("✓ Proxy indexes created")
        
        # ProxyInbound indexes
        inbounds_col = self.db[COLLECTIONS["proxy_inbounds"]]
        await inbounds_col.create_index("tag", unique=True)
        print("✓ ProxyInbound indexes created")
        
        # ProxyHost indexes
        hosts_col = self.db[COLLECTIONS["proxy_hosts"]]
        await hosts_col.create_index("inbound_tag")
        await hosts_col.create_index("is_disabled")
        print("✓ ProxyHost indexes created")
        
        # Usage indexes for performance
        node_user_usages_col = self.db[COLLECTIONS["node_user_usages"]]
        await node_user_usages_col.create_index([
            ("created_at", 1), ("user_id", 1), ("node_id", 1)
        ], unique=True)
        await node_user_usages_col.create_index("user_id")
        await node_user_usages_col.create_index("created_at")
        print("✓ NodeUserUsage indexes created")
        
        node_usages_col = self.db[COLLECTIONS["node_usages"]]
        await node_usages_col.create_index([
            ("created_at", 1), ("node_id", 1)
        ], unique=True)
        await node_usages_col.create_index("created_at")
        print("✓ NodeUsage indexes created")
        
        # UserTemplate indexes
        templates_col = self.db[COLLECTIONS["user_templates"]]
        await templates_col.create_index("name", unique=True)
        print("✓ UserTemplate indexes created")
        
        # NotificationReminder indexes
        reminders_col = self.db[COLLECTIONS["notification_reminders"]]
        await reminders_col.create_index("user_id")
        await reminders_col.create_index("type")
        await reminders_col.create_index("expires_at")
        print("✓ NotificationReminder indexes created")
        
        # Usage logs indexes
        user_logs_col = self.db[COLLECTIONS["user_usage_logs"]]
        await user_logs_col.create_index("user_id")
        await user_logs_col.create_index("reset_at")
        print("✓ UserUsageResetLogs indexes created")
        
        admin_logs_col = self.db[COLLECTIONS["admin_usage_logs"]]
        await admin_logs_col.create_index("admin_id")
        await admin_logs_col.create_index("reset_at")
        print("✓ AdminUsageLogs indexes created")
        
        # NextPlan indexes
        next_plans_col = self.db[COLLECTIONS["next_plans"]]
        await next_plans_col.create_index("user_id", unique=True)
        print("✓ NextPlan indexes created")

    async def setup_default_data(self):
        """Initialize MongoDB database with default data"""
        print("Setting up default database collections and data...")
        
        # Create System collection with default values
        system_col = self.db[COLLECTIONS["system"]]
        existing_system = await system_col.find_one({})
        if not existing_system:
            system_doc = {
                "_id": ObjectId(),
                "uplink": 0,
                "downlink": 0
            }
            await system_col.insert_one(system_doc)
            print("✓ System collection initialized")
        else:
            print("✓ System collection already exists")
        
        # Create JWT collection with random secret
        jwt_col = self.db[COLLECTIONS["jwt"]]
        existing_jwt = await jwt_col.find_one({})
        if not existing_jwt:
            jwt_doc = {
                "_id": ObjectId(),
                "secret_key": os.urandom(32).hex()
            }
            await jwt_col.insert_one(jwt_doc)
            print("✓ JWT secret generated")
        else:
            print("✓ JWT secret already exists")
        
        # Create TLS collection with self-signed certificate
        tls_col = self.db[COLLECTIONS["tls"]]
        existing_tls = await tls_col.find_one({})
        if not existing_tls:
            tls_data = generate_certificate()
            if tls_data:
                tls_doc = {
                    "_id": ObjectId(),
                    "key": tls_data['key'],
                    "certificate": tls_data['cert']
                }
                await tls_col.insert_one(tls_doc)
                print("✓ TLS certificate generated")
            else:
                print("⚠️  TLS certificate skipped (pyOpenSSL not available)")
        else:
            print("✓ TLS certificate already exists")

    async def run_setup(self):
        """Run the complete setup process"""
        print("=" * 50)
        print("MongoDB Database Setup for Marzban")
        print("=" * 50)
        
        try:
            # Connect to MongoDB
            if not await self.connect():
                return False
            
            # Create collections
            await self.create_collections()
            
            # Create indexes
            await self.create_indexes()
            
            # Setup default data
            await self.setup_default_data()
            
            print("=" * 50)
            print("Database setup completed successfully!")
            print("You can now start Marzban.")
            print("=" * 50)
            return True
            
        except Exception as e:
            print(f"Setup failed: {e}")
            return False
        finally:
            await self.close()

async def main():
    """Main setup function"""
    setup = MongoDBSetup()
    success = await setup.run_setup()
    return 0 if success else 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)