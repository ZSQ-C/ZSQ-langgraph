"""
认证路由 - 登录、刷新令牌

POST /login     - 用户名+密码验证，返回JWT
POST /refresh   - 刷新访问令牌
"""

import logging

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings

from src.api.deps import get_admin_db
from src.api.middleware import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
)
from src.api.schemas.auth import LoginRequest, RefreshRequest, TokenResponse, UserInfo
from src.db.models.role import Role
from src.db.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/login", response_model=TokenResponse, summary="用户登录")
async def login(
    request: LoginRequest,
    session: AsyncSession = Depends(get_admin_db),
):
    """
    验证用户名密码，返回 JWT 访问令牌

    - **username**: 用户名
    - **password**: 明文密码
    """
    # 查询用户
    result = await session.execute(
        select(User).where(
            User.username == request.username,
            User.is_deleted == False,
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被禁用",
        )

    # 验证密码
    if not bcrypt.checkpw(
        request.password.encode("utf-8"),
        user.password_hash.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    # 获取角色名
    role_result = await session.execute(
        select(Role.role_name).where(Role.id == user.role_id)
    )
    role_name = role_result.scalar_one_or_none() or "analyst"

    # 生成令牌
    access_token = create_access_token(
        user_id=str(user.id),
        username=user.username,
        dept=user.dept,
        role=role_name,
    )
    refresh_token = create_refresh_token(user_id=str(user.id))

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.jwt_expire_minutes * 60,
    )


@router.post("/refresh", response_model=TokenResponse, summary="刷新令牌")
async def refresh_token(
    request: RefreshRequest,
    session: AsyncSession = Depends(get_admin_db),
):
    """
    使用刷新令牌获取新的访问令牌

    - **refresh_token**: 刷新令牌
    """
    payload = decode_token(request.refresh_token)

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的刷新令牌类型",
        )

    user_id = payload["sub"]

    # 查询用户确认仍然有效
    result = await session.execute(
        select(User).where(
            User.id == user_id,
            User.is_deleted == False,
            User.is_active == True,
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
        )

    # 获取角色名
    role_result = await session.execute(
        select(Role.role_name).where(Role.id == user.role_id)
    )
    role_name = role_result.scalar_one_or_none() or "analyst"

    # 生成新的访问令牌
    access_token = create_access_token(
        user_id=str(user.id),
        username=user.username,
        dept=user.dept,
        role=role_name,
    )

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.jwt_expire_minutes * 60,
    )


@router.get("/me", response_model=UserInfo, summary="获取当前用户信息")
async def get_current_user_info(
    current_user: dict = Depends(get_current_user),
):
    """获取当前登录用户信息"""
    return UserInfo(
        user_id=current_user["user_id"],
        username=current_user["username"],
        dept=current_user["dept"],
        role_name=current_user["role"],
    )
