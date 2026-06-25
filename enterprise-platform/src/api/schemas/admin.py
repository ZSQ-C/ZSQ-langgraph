"""
管理后台相关 Pydantic schemas
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=100, description="用户名")
    password: str = Field(..., min_length=1, max_length=255, description="密码")
    dept: str = Field(..., min_length=1, max_length=200, description="部门")
    role_id: str = Field(..., description="角色ID")


class UserUpdate(BaseModel):
    username: Optional[str] = Field(None, min_length=1, max_length=100)
    password: Optional[str] = Field(None, min_length=1, max_length=255)
    dept: Optional[str] = Field(None, min_length=1, max_length=200)
    role_id: Optional[str] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    id: str
    username: str
    dept: str
    role_id: str
    role_name: Optional[str] = None
    is_active: bool = True
    create_time: datetime
    update_time: datetime

    class Config:
        from_attributes = True


class RoleCreate(BaseModel):
    role_name: str = Field(..., min_length=1, max_length=50, description="角色名称")
    table_permissions: dict[str, list[str]] = Field(default_factory=dict)
    field_permissions: dict[str, list[str]] = Field(default_factory=dict)
    row_conditions: dict[str, str] = Field(default_factory=dict)
    doc_tags_allowed: list[str] = Field(default_factory=list)
    can_export: bool = False
    max_query_rows: int = 1000


class RoleUpdate(BaseModel):
    role_name: Optional[str] = None
    table_permissions: Optional[dict[str, list[str]]] = None
    field_permissions: Optional[dict[str, list[str]]] = None
    row_conditions: Optional[dict[str, str]] = None
    doc_tags_allowed: Optional[list[str]] = None
    can_export: Optional[bool] = None
    max_query_rows: Optional[int] = None


class RoleResponse(BaseModel):
    id: str
    role_name: str
    table_permissions: dict[str, list[str]]
    field_permissions: dict[str, list[str]]
    row_conditions: dict[str, str]
    doc_tags_allowed: list[str] = []
    can_export: bool = False
    max_query_rows: int = 1000
    create_time: datetime
    update_time: datetime

    class Config:
        from_attributes = True


class AuditLogResponse(BaseModel):
    id: str
    thread_id: str
    user_id: str
    session_id: Optional[str] = None
    original_query: str
    query_complexity: Optional[str] = None
    risk_level: Optional[str] = None
    generated_sql: Optional[str] = None
    executed_sql: Optional[str] = None
    sql_safe: Optional[bool] = None
    permission_pass: Optional[bool] = None
    human_reviewed: bool = False
    human_approved: Optional[bool] = None
    reviewer_id: Optional[str] = None
    review_comment: Optional[str] = None
    execution_success: Optional[bool] = None
    execution_time_ms: Optional[int] = None
    row_count: Optional[int] = None
    error_message: Optional[str] = None
    masked_fields: Optional[list[str]] = None
    critic_score: Optional[float] = None
    create_time: datetime

    class Config:
        from_attributes = True


class AuditLogListResponse(BaseModel):
    total: int
    items: list[AuditLogResponse]
