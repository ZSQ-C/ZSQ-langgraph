"""
对话相关 Pydantic schemas
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户消息")


class ChatEvent(BaseModel):
    """SSE事件格式"""
    event: str = Field(..., description="事件类型: node_start/node_end/stream/chunk/error/complete")
    data: Any = Field(..., description="事件数据")


class MessageResponse(BaseModel):
    id: str
    role: str = Field(..., description="角色: user/assistant/system/tool")
    content: str
    timestamp: Optional[datetime] = None

    class Config:
        from_attributes = True


class ApproveRequest(BaseModel):
    approved: bool = Field(..., description="是否批准")
    comment: Optional[str] = Field(None, description="审核意见")


class RejectRequest(BaseModel):
    reason: str = Field(..., description="拒绝原因")
