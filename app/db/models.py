# app/db/models.py
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from bson import ObjectId
from app.models.node import NodeStatus
from app.models.proxy import (
    ProxyHostALPN,
    ProxyHostFingerprint, 
    ProxyHostSecurity,
    ProxyTypes,
)
from app.models.user import ReminderType, UserDataLimitResetStrategy, UserStatus

from typing import Any, Dict
from pydantic import field_validator
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema

from typing import Any, Dict
from pydantic import field_validator
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema

class PyObjectId(ObjectId):
    @classmethod
    def __get_pydantic_core_schema__(
        cls, 
        source_type: Any, 
        handler
    ) -> core_schema.CoreSchema:
        return core_schema.union_schema([
            # Check if it's an instance first
            core_schema.is_instance_schema(ObjectId),
            # Then check if it's a valid ObjectId string
            core_schema.chain_schema([
                core_schema.str_schema(),
                core_schema.no_info_plain_validator_function(cls.validate),
            ])
        ])

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: core_schema.CoreSchema, handler
    ) -> JsonSchemaValue:
        return {"type": "string", "format": "objectid"}

    @classmethod
    def validate(cls, v: Any) -> ObjectId:
        if isinstance(v, ObjectId):
            return v
        if isinstance(v, str):
            if ObjectId.is_valid(v):
                return ObjectId(v)
        raise ValueError("Invalid ObjectId")

class MongoBaseModel(BaseModel):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str}
    )

class Admin(MongoBaseModel):
    username: str = Field(..., unique=True, index=True)
    hashed_password: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_sudo: bool = False
    password_reset_at: Optional[datetime] = None
    users_usage: int = 0

class User(MongoBaseModel):
    username: str = Field(..., unique=True, index=True)
    status: UserStatus = UserStatus.active
    used_traffic: int = 0
    data_limit: Optional[int] = None
    data_limit_reset_strategy: UserDataLimitResetStrategy = UserDataLimitResetStrategy.no_reset
    expire: Optional[int] = None
    admin_id: Optional[PyObjectId] = None
    sub_revoked_at: Optional[datetime] = None
    sub_updated_at: Optional[datetime] = None
    sub_last_user_agent: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    note: Optional[str] = None
    online_at: Optional[datetime] = None
    on_hold_expire_duration: Optional[int] = None
    on_hold_timeout: Optional[datetime] = None
    auto_delete_in_days: Optional[int] = None
    edit_at: Optional[datetime] = None
    last_status_change: datetime = Field(default_factory=datetime.utcnow)

class NextPlan(MongoBaseModel):
    user_id: PyObjectId
    data_limit: int
    expire: Optional[int] = None
    add_remaining_traffic: bool = False
    fire_on_either: bool = True

class UserTemplate(MongoBaseModel):
    name: str = Field(..., unique=True)
    data_limit: int = 0
    expire_duration: int = 0  # in seconds
    username_prefix: Optional[str] = None
    username_suffix: Optional[str] = None
    inbound_tags: List[str] = []

class UserUsageResetLogs(MongoBaseModel):
    user_id: PyObjectId
    used_traffic_at_reset: int
    reset_at: datetime = Field(default_factory=datetime.utcnow)

class AdminUsageLogs(MongoBaseModel):
    admin_id: PyObjectId
    used_traffic_at_reset: int
    reset_at: datetime = Field(default_factory=datetime.utcnow)

class Proxy(MongoBaseModel):
    user_id: PyObjectId
    type: ProxyTypes
    settings: Dict[str, Any]
    excluded_inbound_tags: List[str] = []

class ProxyInbound(MongoBaseModel):
    tag: str = Field(..., unique=True, index=True)

class ProxyHost(MongoBaseModel):
    remark: str
    address: str
    port: Optional[int] = None
    path: Optional[str] = None
    sni: Optional[str] = None
    host: Optional[str] = None
    security: ProxyHostSecurity = ProxyHostSecurity.inbound_default
    alpn: ProxyHostALPN = ProxyHostALPN.none
    fingerprint: ProxyHostFingerprint = ProxyHostFingerprint.none
    inbound_tag: str
    allowinsecure: Optional[bool] = None
    is_disabled: Optional[bool] = False
    mux_enable: bool = False
    fragment_setting: Optional[str] = None
    noise_setting: Optional[str] = None
    random_user_agent: bool = False
    use_sni_as_host: bool = False

class System(MongoBaseModel):
    uplink: int = 0
    downlink: int = 0

class JWT(MongoBaseModel):
    secret_key: str

class TLS(MongoBaseModel):
    key: str
    certificate: str

class Node(MongoBaseModel):
    name: Optional[str] = Field(..., unique=True)
    address: str
    port: int
    api_port: int
    xray_version: Optional[str] = None
    status: NodeStatus = NodeStatus.connecting
    last_status_change: datetime = Field(default_factory=datetime.utcnow)
    message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    uplink: int = 0
    downlink: int = 0
    usage_coefficient: float = 1.0

class NodeUserUsage(MongoBaseModel):
    created_at: datetime
    user_id: PyObjectId
    node_id: Optional[PyObjectId] = None
    used_traffic: int = 0
    
    class Config:
        indexes = [
            [("created_at", 1), ("user_id", 1), ("node_id", 1)]
        ]

class NodeUsage(MongoBaseModel):
    created_at: datetime
    node_id: Optional[PyObjectId] = None
    uplink: int = 0
    downlink: int = 0
    
    class Config:
        indexes = [
            [("created_at", 1), ("node_id", 1)]
        ]

class NotificationReminder(MongoBaseModel):
    user_id: PyObjectId
    type: ReminderType
    threshold: Optional[int] = None
    expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)