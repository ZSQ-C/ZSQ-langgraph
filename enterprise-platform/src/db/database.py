"""
数据库连接管理 - SQLAlchemy异步引擎（企业级）

核心特性：
1. 双引擎完全隔离：只读从库引擎 + 管理库引擎
2. 连接池完整配置：pool_size / max_overflow / pool_pre_ping / pool_recycle
3. 只读库语句超时：从连接层面设置 statement_timeout=30s，熔断慢SQL
4. 指数退避重试：连接失败自动重试3次（1s → 2s → 4s）
5. Session 上下文管理器：异常安全，保证连接释放
6. 软删除基类：id / create_time / update_time / is_deleted
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, String, false, text
from sqlalchemy.exc import OperationalError, DBAPIError

# MySQL 兼容：用 String(36) 替代 PostgreSQL UUID
from config.settings import settings
_is_mysql = "mysql" in settings.admin_db_url.lower()
if _is_mysql:
    def _uuid_column(**kwargs):
        return Column(String(36), default=lambda: str(uuid4()), **kwargs)
else:
    from sqlalchemy.dialects.postgresql import UUID
    def _uuid_column(**kwargs):
        return Column(UUID(as_uuid=True), default=uuid4, **kwargs)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, declared_attr

from config.settings import settings

logger = logging.getLogger(__name__)


# ============================================================
# 软删除基类
# ============================================================

class Base(DeclarativeBase):
    """
    ORM基类，所有模型继承此类

    通用字段：
    - id: UUID主键，自动生成
    - create_time: 创建时间
    - update_time: 更新时间，自动更新
    - is_deleted: 软删除标记，默认False
    """

    __abstract__ = True

    id = _uuid_column(primary_key=True, comment="主键ID")
    create_time = Column(
        DateTime,
        default=datetime.now,
        server_default=text("now()"),
        nullable=False,
        comment="创建时间",
    )
    update_time = Column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
        server_default=text("now()"),
        nullable=False,
        comment="更新时间",
    )
    is_deleted = Column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
        index=True,
        comment="软删除标记",
    )

    @declared_attr
    def __tablename__(cls) -> str:
        """自动生成表名：类名转小写 + s"""
        return cls.__name__.lower() + "s"


# ============================================================
# 连接池配置常量
# ============================================================

# 只读从库 — 连接池参数
READ_ONLY_POOL_SIZE = 10
READ_ONLY_MAX_OVERFLOW = 20
READ_ONLY_POOL_RECYCLE = 3600      # 连接回收时间（秒）

# 管理库 — 连接池参数
ADMIN_POOL_SIZE = 5
ADMIN_MAX_OVERFLOW = 10
ADMIN_POOL_RECYCLE = 3600

# 重试配置
MAX_RETRIES = 3
BASE_DELAY = 1.0                   # 基础延迟（秒），指数退避：1 → 2 → 4


# ============================================================
# 引擎创建
# ============================================================

def _build_connect_args(statement_timeout: int | None = None) -> dict:
    """
    构建数据库连接参数

    Args:
        statement_timeout: 语句超时秒数，None表示不设置

    Returns:
        asyncpg连接参数字典
    """
    args = {
        "server_settings": {},
        "timeout": 15,           # 连接超时
        "command_timeout": 30,   # 命令超时
    }
    if statement_timeout is not None:
        args["server_settings"]["statement_timeout"] = str(statement_timeout * 1000)
        # 同时设置 idle_in_transaction_session_timeout 防止空闲事务
        args["server_settings"]["idle_in_transaction_session_timeout"] = str(statement_timeout * 1000 * 2)
    return args


def _create_engine(
    url: str,
    pool_size: int,
    max_overflow: int,
    pool_recycle: int,
    statement_timeout: int | None = None,
) -> "AsyncEngine":
    """创建异步数据库引擎（统一工厂方法，支持PostgreSQL和SQLite）"""
    from sqlalchemy.ext.asyncio import AsyncEngine

    is_sqlite = "sqlite" in url
    is_mysql = "mysql" in url

    if is_sqlite:
        connect_args = {"check_same_thread": False}
        pool_size, max_overflow = 1, 0
        pool_pre_ping = False
    elif is_mysql:
        connect_args = {"charset": "utf8mb4"}
        pool_pre_ping = True
    else:
        connect_args = _build_connect_args(statement_timeout)
        pool_pre_ping = True

    return create_async_engine(
        url,
        echo=settings.debug,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=pool_recycle,
        pool_pre_ping=pool_pre_ping,
        pool_timeout=30,
        connect_args=connect_args,
        execution_options={
            "isolation_level": "AUTOCOMMIT" if (statement_timeout and not is_sqlite) else "READ COMMITTED",
        },
    )


# 只读从库引擎 — 带30秒语句超时
read_only_engine = _create_engine(
    url=settings.read_only_db_url,
    pool_size=READ_ONLY_POOL_SIZE,
    max_overflow=READ_ONLY_MAX_OVERFLOW,
    pool_recycle=READ_ONLY_POOL_RECYCLE,
    statement_timeout=settings.sql_timeout_seconds,  # 30秒
)

# 管理库引擎 — 无语句超时
admin_engine = _create_engine(
    url=settings.admin_db_url,
    pool_size=ADMIN_POOL_SIZE,
    max_overflow=ADMIN_MAX_OVERFLOW,
    pool_recycle=ADMIN_POOL_RECYCLE,
    statement_timeout=None,  # 管理库不限制语句超时
)


# ============================================================
# Session 工厂
# ============================================================

ReadOnlySessionFactory = async_sessionmaker(
    read_only_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,       # 只读库不需要flush
)

AdminSessionFactory = async_sessionmaker(
    admin_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ============================================================
# 指数退避重试机制
# ============================================================

async def _retry_with_backoff(
    operation_name: str,
    coro_factory,
    max_retries: int = MAX_RETRIES,
    base_delay: float = BASE_DELAY,
):
    """
    指数退避重试执行器

    Args:
        operation_name: 操作名称（用于日志）
        coro_factory: 协程工厂函数，每次重试调用生成新协程
        max_retries: 最大重试次数
        base_delay: 基础延迟秒数

    Returns:
        协程执行结果

    Raises:
        最后一次重试的异常
    """
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except (OperationalError, DBAPIError, ConnectionError, OSError) as e:
            last_exception = e
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)  # 1s → 2s → 4s
                logger.warning(
                    f"[{operation_name}] 第{attempt + 1}次尝试失败: {e}，"
                    f"{delay:.1f}秒后重试（共{max_retries}次重试机会）"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"[{operation_name}] 重试{max_retries}次后仍失败，放弃重试"
                )
        except Exception as e:
            # 非网络相关异常，不重试
            logger.error(f"[{operation_name}] 非重试异常: {type(e).__name__}: {e}")
            raise

    raise last_exception


# ============================================================
# 上下文管理器（保证异常安全）
# ============================================================

@asynccontextmanager
async def read_only_session() -> AsyncGenerator[AsyncSession, None]:
    """
    只读从库会话上下文管理器

    用法:
        async with read_only_session() as session:
            result = await session.execute(text(sql))

    特性：
    - 异常安全：finally块保证连接释放
    - 自动重试：网络抖动自动重试连接
    - 超时熔断：statement_timeout 从连接层面拦截慢SQL
    """
    async def _create_session():
        return ReadOnlySessionFactory()

    session = await _retry_with_backoff(
        "只读库连接",
        _create_session,
    )
    try:
        yield session
        # 只读库不需要commit，直接回滚清理
        await session.rollback()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@asynccontextmanager
async def admin_session() -> AsyncGenerator[AsyncSession, None]:
    """
    管理库会话上下文管理器

    用法:
        async with admin_session() as session:
            session.add(audit_log)
            await session.commit()
    """
    async def _create_session():
        return AdminSessionFactory()

    session = await _retry_with_backoff(
        "管理库连接",
        _create_session,
    )
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# ============================================================
# 数据库生命周期管理
# ============================================================

async def init_db():
    """
    初始化数据库表结构

    用法:
        await init_db()
    """
    async def _create_tables():
        async with admin_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return True

    await _retry_with_backoff("数据库初始化", _create_tables)
    logger.info("数据库表结构初始化完成")


async def close_db():
    """关闭所有数据库连接"""
    await read_only_engine.dispose()
    await admin_engine.dispose()
    logger.info("数据库连接已关闭")


async def check_db_health() -> dict[str, bool]:
    """
    数据库健康检查

    Returns:
        {"read_only": true/false, "admin": true/false}
    """
    async def _check_engine(name: str, engine):
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    results = {}
    for label, engine in [("read_only", read_only_engine), ("admin", admin_engine)]:
        try:
            results[label] = await _check_engine(label, engine)
        except Exception as e:
            logger.error(f"数据库[{label}]健康检查失败: {e}")
            results[label] = False

    return results