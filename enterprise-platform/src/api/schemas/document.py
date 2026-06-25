"""
文档相关 Pydantic schemas
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class DocumentResponse(BaseModel):
    id: str
    title: str
    file_type: Optional[str] = None
    file_path: Optional[str] = None
    parse_engine: Optional[str] = None
    page_count: int = 0
    tags: list[str] = []
    chunk_count: int = 0
    is_parsed: bool = False
    parse_error: Optional[str] = None
    uploaded_by: Optional[str] = None
    create_time: datetime
    update_time: datetime

    class Config:
        from_attributes = True


class DocumentListResponse(BaseModel):
    total: int
    items: list[DocumentResponse]


class DocumentParseResponse(BaseModel):
    document_id: str
    status: str = Field(..., description="解析状态: pending/processing/completed/failed")
    message: Optional[str] = None
