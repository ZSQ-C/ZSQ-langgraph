"""
管理后台路由

用户管理: CRUD
角色管理: CRUD
审计日志: 只读查询
"""

import logging
import uuid

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_admin_db
from src.api.middleware import get_current_user
from src.api.schemas.admin import (
    AuditLogListResponse,
    AuditLogResponse,
    RoleCreate,
    RoleResponse,
    RoleUpdate,
    UserCreate,
    UserResponse,
    UserUpdate,
)
from src.db.models.audit_log import AuditLog
from src.db.models.role import Role
from src.db.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================
# 用户管理
# ============================================================

@router.get("/users", response_model=list[UserResponse], summary="获取用户列表")
async def list_users(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """获取所有用户列表"""
    result = await session.execute(
        select(User, Role.role_name)
        .outerjoin(Role, User.role_id == Role.id)
        .where(User.is_deleted == False)
        .order_by(User.create_time.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = result.all()

    return [
        UserResponse(
            id=str(user.id),
            username=user.username,
            dept=user.dept,
            role_id=str(user.role_id),
            role_name=role_name,
            is_active=user.is_active,
            create_time=user.create_time,
            update_time=user.update_time,
        )
        for user, role_name in rows
    ]


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED, summary="创建用户")
async def create_user(
    user_data: UserCreate,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """创建新用户"""
    # 检查用户名是否已存在
    existing = await session.execute(
        select(User).where(
            User.username == user_data.username,
            User.is_deleted == False,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"用户名已存在: {user_data.username}",
        )

    # 验证角色是否存在
    role_result = await session.execute(
        select(Role).where(
            Role.id == user_data.role_id,
            Role.is_deleted == False,
        )
    )
    role = role_result.scalar_one_or_none()
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"角色不存在: {user_data.role_id}",
        )

    # 哈希密码
    password_hash = bcrypt.hashpw(
        user_data.password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")

    # 创建用户
    user = User(
        username=user_data.username,
        password_hash=password_hash,
        dept=user_data.dept,
        role_id=user_data.role_id,
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    logger.info(f"创建用户: {user.username} (ID: {user.id})")

    return UserResponse(
        id=str(user.id),
        username=user.username,
        dept=user.dept,
        role_id=str(user.role_id),
        role_name=role.role_name,
        is_active=user.is_active,
        create_time=user.create_time,
        update_time=user.update_time,
    )


@router.get("/users/{user_id}", response_model=UserResponse, summary="获取用户详情")
async def get_user(
    user_id: str,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """获取指定用户详情"""
    result = await session.execute(
        select(User, Role.role_name)
        .outerjoin(Role, User.role_id == Role.id)
        .where(User.id == user_id, User.is_deleted == False)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    user, role_name = row
    return UserResponse(
        id=str(user.id),
        username=user.username,
        dept=user.dept,
        role_id=str(user.role_id),
        role_name=role_name,
        is_active=user.is_active,
        create_time=user.create_time,
        update_time=user.update_time,
    )


@router.put("/users/{user_id}", response_model=UserResponse, summary="更新用户")
async def update_user(
    user_id: str,
    user_data: UserUpdate,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """更新用户信息"""
    result = await session.execute(
        select(User).where(User.id == user_id, User.is_deleted == False)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    # 更新字段
    if user_data.username is not None:
        user.username = user_data.username
    if user_data.dept is not None:
        user.dept = user_data.dept
    if user_data.password is not None:
        user.password_hash = bcrypt.hashpw(
            user_data.password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
    if user_data.role_id is not None:
        user.role_id = user_data.role_id
    if user_data.is_active is not None:
        user.is_active = user_data.is_active

    await session.commit()
    await session.refresh(user)

    # 获取角色名
    role_result = await session.execute(
        select(Role.role_name).where(Role.id == user.role_id)
    )
    role_name = role_result.scalar_one_or_none()

    return UserResponse(
        id=str(user.id),
        username=user.username,
        dept=user.dept,
        role_id=str(user.role_id),
        role_name=role_name,
        is_active=user.is_active,
        create_time=user.create_time,
        update_time=user.update_time,
    )


@router.delete("/users/{user_id}", summary="删除用户")
async def delete_user(
    user_id: str,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """软删除用户"""
    result = await session.execute(
        select(User).where(User.id == user_id, User.is_deleted == False)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    user.is_deleted = True
    await session.commit()

    logger.info(f"删除用户: {user.username} (ID: {user_id})")
    return {"status": "deleted", "user_id": user_id}


# ============================================================
# 角色管理
# ============================================================

@router.get("/roles", response_model=list[RoleResponse], summary="获取角色列表")
async def list_roles(
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """获取所有角色列表"""
    result = await session.execute(
        select(Role)
        .where(Role.is_deleted == False)
        .order_by(Role.create_time.desc())
    )
    roles = result.scalars().all()

    return [
        RoleResponse(
            id=str(r.id),
            role_name=r.role_name,
            table_permissions=r.table_permissions or {},
            field_permissions=r.field_permissions or {},
            row_conditions=r.row_conditions or {},
            doc_tags_allowed=r.doc_tags_allowed or [],
            can_export=r.can_export or False,
            max_query_rows=r.max_query_rows or 1000,
            create_time=r.create_time,
            update_time=r.update_time,
        )
        for r in roles
    ]


@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED, summary="创建角色")
async def create_role(
    role_data: RoleCreate,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """创建新角色"""
    # 检查角色名是否已存在
    existing = await session.execute(
        select(Role).where(
            Role.role_name == role_data.role_name,
            Role.is_deleted == False,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"角色名已存在: {role_data.role_name}",
        )

    role = Role(
        role_name=role_data.role_name,
        table_permissions=role_data.table_permissions,
        field_permissions=role_data.field_permissions,
        row_conditions=role_data.row_conditions,
        doc_tags_allowed=role_data.doc_tags_allowed,
        can_export=role_data.can_export,
        max_query_rows=role_data.max_query_rows,
    )
    session.add(role)
    await session.commit()
    await session.refresh(role)

    logger.info(f"创建角色: {role.role_name} (ID: {role.id})")

    return RoleResponse(
        id=str(role.id),
        role_name=role.role_name,
        table_permissions=role.table_permissions or {},
        field_permissions=role.field_permissions or {},
        row_conditions=role.row_conditions or {},
        doc_tags_allowed=role.doc_tags_allowed or [],
        can_export=role.can_export or False,
        max_query_rows=role.max_query_rows or 1000,
        create_time=role.create_time,
        update_time=role.update_time,
    )


@router.get("/roles/{role_id}", response_model=RoleResponse, summary="获取角色详情")
async def get_role(
    role_id: str,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """获取指定角色详情"""
    result = await session.execute(
        select(Role).where(Role.id == role_id, Role.is_deleted == False)
    )
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在")

    return RoleResponse(
        id=str(role.id),
        role_name=role.role_name,
        table_permissions=role.table_permissions or {},
        field_permissions=role.field_permissions or {},
        row_conditions=role.row_conditions or {},
        doc_tags_allowed=role.doc_tags_allowed or [],
        can_export=role.can_export or False,
        max_query_rows=role.max_query_rows or 1000,
        create_time=role.create_time,
        update_time=role.update_time,
    )


@router.put("/roles/{role_id}", response_model=RoleResponse, summary="更新角色")
async def update_role(
    role_id: str,
    role_data: RoleUpdate,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """更新角色信息"""
    result = await session.execute(
        select(Role).where(Role.id == role_id, Role.is_deleted == False)
    )
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在")

    # 更新字段
    if role_data.role_name is not None:
        role.role_name = role_data.role_name
    if role_data.table_permissions is not None:
        role.table_permissions = role_data.table_permissions
    if role_data.field_permissions is not None:
        role.field_permissions = role_data.field_permissions
    if role_data.row_conditions is not None:
        role.row_conditions = role_data.row_conditions
    if role_data.doc_tags_allowed is not None:
        role.doc_tags_allowed = role_data.doc_tags_allowed
    if role_data.can_export is not None:
        role.can_export = role_data.can_export
    if role_data.max_query_rows is not None:
        role.max_query_rows = role_data.max_query_rows

    await session.commit()
    await session.refresh(role)

    return RoleResponse(
        id=str(role.id),
        role_name=role.role_name,
        table_permissions=role.table_permissions or {},
        field_permissions=role.field_permissions or {},
        row_conditions=role.row_conditions or {},
        doc_tags_allowed=role.doc_tags_allowed or [],
        can_export=role.can_export or False,
        max_query_rows=role.max_query_rows or 1000,
        create_time=role.create_time,
        update_time=role.update_time,
    )


@router.delete("/roles/{role_id}", summary="删除角色")
async def delete_role(
    role_id: str,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """软删除角色"""
    result = await session.execute(
        select(Role).where(Role.id == role_id, Role.is_deleted == False)
    )
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在")

    # 检查是否有用户关联此角色
    user_count_result = await session.execute(
        select(func.count()).select_from(User).where(
            User.role_id == role_id,
            User.is_deleted == False,
        )
    )
    user_count = user_count_result.scalar() or 0
    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该角色下还有 {user_count} 个用户，无法删除",
        )

    role.is_deleted = True
    await session.commit()

    logger.info(f"删除角色: {role.role_name} (ID: {role_id})")
    return {"status": "deleted", "role_id": role_id}


# ============================================================
# 审计日志（只读）
# ============================================================

@router.get("/audit-logs", response_model=AuditLogListResponse, summary="获取审计日志")
async def list_audit_logs(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(50, ge=1, le=200, description="每页数量"),
    user_id: str | None = Query(None, description="按用户ID筛选"),
    risk_level: str | None = Query(None, description="按风险等级筛选: low/medium/high"),
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """获取审计日志列表（只读）"""
    conditions = [AuditLog.is_deleted == False]

    if user_id:
        conditions.append(AuditLog.user_id == user_id)
    if risk_level:
        conditions.append(AuditLog.risk_level == risk_level)

    # 查询总数
    count_result = await session.execute(
        select(func.count()).select_from(AuditLog).where(*conditions)
    )
    total = count_result.scalar() or 0

    # 查询日志列表
    result = await session.execute(
        select(AuditLog)
        .where(*conditions)
        .order_by(AuditLog.create_time.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    logs = result.scalars().all()

    items = [
        AuditLogResponse(
            id=str(log.id),
            thread_id=log.thread_id,
            user_id=str(log.user_id),
            session_id=log.session_id,
            original_query=log.original_query,
            query_complexity=log.query_complexity,
            risk_level=log.risk_level,
            generated_sql=log.generated_sql,
            executed_sql=log.executed_sql,
            sql_safe=log.sql_safe,
            permission_pass=log.permission_pass,
            human_reviewed=log.human_reviewed or False,
            human_approved=log.human_approved,
            reviewer_id=str(log.reviewer_id) if log.reviewer_id else None,
            review_comment=log.review_comment,
            execution_success=log.execution_success,
            execution_time_ms=log.execution_time_ms,
            row_count=log.row_count,
            error_message=log.error_message,
            masked_fields=log.masked_fields,
            critic_score=log.critic_score,
            create_time=log.create_time,
        )
        for log in logs
    ]

    return AuditLogListResponse(total=total, items=items)


@router.get("/audit-logs/{log_id}", response_model=AuditLogResponse, summary="获取审计日志详情")
async def get_audit_log(
    log_id: str,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """获取单条审计日志详情"""
    result = await session.execute(
        select(AuditLog).where(
            AuditLog.id == log_id,
            AuditLog.is_deleted == False,
        )
    )
    log = result.scalar_one_or_none()
    if log is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="审计日志不存在")

    return AuditLogResponse(
        id=str(log.id),
        thread_id=log.thread_id,
        user_id=str(log.user_id),
        session_id=log.session_id,
        original_query=log.original_query,
        query_complexity=log.query_complexity,
        risk_level=log.risk_level,
        generated_sql=log.generated_sql,
        executed_sql=log.executed_sql,
        sql_safe=log.sql_safe,
        permission_pass=log.permission_pass,
        human_reviewed=log.human_reviewed or False,
        human_approved=log.human_approved,
        reviewer_id=str(log.reviewer_id) if log.reviewer_id else None,
        review_comment=log.review_comment,
        execution_success=log.execution_success,
        execution_time_ms=log.execution_time_ms,
        row_count=log.row_count,
        error_message=log.error_message,
        masked_fields=log.masked_fields,
        critic_score=log.critic_score,
        create_time=log.create_time,
    )
