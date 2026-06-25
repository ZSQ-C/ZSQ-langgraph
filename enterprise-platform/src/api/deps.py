"""
FastAPI 依赖注入模块

提供统一的依赖注入函数，供路由使用：
- get_current_user: 获取当前登录用户
- get_db_session: 获取数据库会话
- get_rbac_engine: 获取RBAC权限引擎
- get_audit_writer: 获取审计日志写入器
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.middleware import get_current_user
from src.db.database import admin_session, read_only_session
from src.security.audit import AuditWriter
from src.security.rbac import RBACEngine

# 全局单例（RBACEngine 和 AuditWriter 是无状态的，可复用）
_rbac_engine: RBACEngine | None = None
_audit_writer: AuditWriter | None = None


def get_rbac_engine() -> RBACEngine:
    """获取 RBAC 权限校验引擎（单例）"""
    global _rbac_engine
    if _rbac_engine is None:
        _rbac_engine = RBACEngine()
    return _rbac_engine


def get_audit_writer() -> AuditWriter:
    """获取审计日志写入器（单例）"""
    global _audit_writer
    if _audit_writer is None:
        _audit_writer = AuditWriter()
    return _audit_writer


async def get_read_only_db() -> AsyncGenerator[AsyncSession, None]:
    """获取只读数据库会话（用于查询）"""
    async with read_only_session() as session:
        yield session


async def get_admin_db() -> AsyncGenerator[AsyncSession, None]:
    """获取管理数据库会话（用于写入）"""
    async with admin_session() as session:
        yield session
