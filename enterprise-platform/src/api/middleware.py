"""
JWT 认证中间件

使用 PyJWT 实现无状态 JWT 认证：
- 从 Authorization Header 提取 Bearer token
- 解析 token 获得 user_id, dept, role
- 注入到 request.state 供下游使用
- 提供 get_current_user 依赖注入函数
"""

import logging
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import settings

logger = logging.getLogger(__name__)

security_scheme = HTTPBearer(auto_error=False)

# 用于标记免认证路径
PUBLIC_PATHS = {
    "/api/auth/login",
    "/api/health",
    "/docs",
    "/openapi.json",
    "/redoc",
}


def create_access_token(user_id: str, username: str, dept: str, role: str) -> str:
    """
    创建 JWT 访问令牌

    Args:
        user_id: 用户ID
        username: 用户名
        dept: 部门
        role: 角色名

    Returns:
        JWT 令牌字符串
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "username": username,
        "dept": dept,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str) -> str:
    """
    创建 JWT 刷新令牌（有效期更长）

    Args:
        user_id: 用户ID

    Returns:
        JWT 刷新令牌字符串
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(days=7),
        "type": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """
    解码并验证 JWT 令牌

    Args:
        token: JWT 令牌字符串

    Returns:
        解码后的 payload 字典

    Raises:
        HTTPException: 令牌无效或过期
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def jwt_auth_middleware(request: Request, call_next):
    """
    FastAPI 中间件：拦截所有请求，验证 JWT 令牌

    将解析后的用户信息注入 request.state.user
    """
    # 跳过公开路径
    if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/docs") or request.url.path.startswith("/openapi"):
        return await call_next(request)

    # 跳过 OPTIONS 预检请求
    if request.method == "OPTIONS":
        return await call_next(request)

    # 提取并验证令牌
    auth_header = request.headers.get("Authorization", "")
    token = None

    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        # 也支持 query parameter 方式（SSE 需要）
        token = request.query_params.get("token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)

    # 注入到 request.state
    request.state.user_id = payload["sub"]
    request.state.username = payload.get("username", "")
    request.state.dept = payload.get("dept", "")
    request.state.role = payload.get("role", "")

    logger.debug(f"认证用户: {request.state.username} (ID: {request.state.user_id})")

    response = await call_next(request)
    return response


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
) -> dict:
    """
    FastAPI 依赖注入：获取当前登录用户信息

    用法:
        @app.get("/protected")
        async def protected_route(current_user: dict = Depends(get_current_user)):
            ...

    Returns:
        {"user_id": str, "username": str, "dept": str, "role": str}
    """
    # 优先从 request.state 获取（中间件已解析）
    if hasattr(request.state, "user_id"):
        return {
            "user_id": request.state.user_id,
            "username": request.state.username,
            "dept": request.state.dept,
            "role": request.state.role,
        }

    # 回退：手动解析 token
    token = None
    if credentials:
        token = credentials.credentials
    else:
        token = request.query_params.get("token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_token(token)
    return {
        "user_id": payload["sub"],
        "username": payload.get("username", ""),
        "dept": payload.get("dept", ""),
        "role": payload.get("role", ""),
    }
