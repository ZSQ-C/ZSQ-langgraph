"""
会话管理路由

POST /      - 创建新会话
GET /       - 获取用户会话列表
DELETE /{id} - 删除会话（软删除）
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_admin_db
from src.api.middleware import get_current_user
from src.api.schemas.session import SessionCreate, SessionListResponse, SessionResponse
from src.db.models.session import Session

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/", response_model=SessionResponse, status_code=status.HTTP_201_CREATED, summary="创建新会话")
async def create_session(
    session_create: SessionCreate,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """
    创建新的对话会话

    - **title**: 会话标题（默认为"新对话"）
    """
    user_id = current_user["user_id"]
    thread_id = f"thread-{uuid.uuid4().hex[:16]}"

    chat_session = Session(
        title=session_create.title,
        user_id=user_id,
        thread_id=thread_id,
        status="active",
    )
    session.add(chat_session)
    await session.commit()
    await session.refresh(chat_session)

    logger.info(f"创建会话: id={chat_session.id}, user={user_id}, thread={thread_id}")

    return SessionResponse(
        id=str(chat_session.id),
        title=chat_session.title,
        user_id=str(chat_session.user_id),
        thread_id=chat_session.thread_id,
        status=chat_session.status,
        last_message=None,
        create_time=chat_session.create_time,
        update_time=chat_session.update_time,
    )


@router.get("/", response_model=SessionListResponse, summary="获取会话列表")
async def list_sessions(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """
    获取当前用户的所有会话列表（按更新时间倒序）
    """
    user_id = current_user["user_id"]

    # 查询总数
    count_result = await session.execute(
        select(func.count()).select_from(Session).where(
            Session.user_id == user_id,
            Session.is_deleted == False,
        )
    )
    total = count_result.scalar() or 0

    # 查询会话列表
    result = await session.execute(
        select(Session)
        .where(
            Session.user_id == user_id,
            Session.is_deleted == False,
        )
        .order_by(Session.update_time.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    sessions = result.scalars().all()

    items = [
        SessionResponse(
            id=str(s.id),
            title=s.title,
            user_id=str(s.user_id),
            thread_id=s.thread_id,
            status=s.status,
            last_message=s.last_message,
            create_time=s.create_time,
            update_time=s.update_time,
        )
        for s in sessions
    ]

    return SessionListResponse(total=total, items=items)


@router.delete("/{session_id}", summary="删除会话")
async def delete_session(
    session_id: str,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """
    软删除指定会话
    """
    result = await session.execute(
        select(Session).where(
            Session.id == session_id,
            Session.user_id == current_user["user_id"],
            Session.is_deleted == False,
        )
    )
    chat_session = result.scalar_one_or_none()

    if chat_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在",
        )

    # 软删除
    chat_session.is_deleted = True
    chat_session.status = "deleted"
    await session.commit()

    logger.info(f"删除会话: id={session_id}, user={current_user['user_id']}")

    return {"status": "deleted", "session_id": session_id}
