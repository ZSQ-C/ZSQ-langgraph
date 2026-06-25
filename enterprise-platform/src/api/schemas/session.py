"""
会话相关 Pydantic schemas
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=200, description="会话标题")


class SessionResponse(BaseModel):
    id: str
    title: str
    user_id: str
    thread_id: str
    status: str
    last_message: Optional[str] = None
    create_time: datetime
    update_time: datetime

    class Config:
        from_attributes = True


class SessionListResponse(BaseModel):
    total: int
    items: list[SessionResponse]
