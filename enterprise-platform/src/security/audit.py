"""
审计日志写入器

负责将Agent执行过程中的关键节点信息写入审计日志表，
支持异步写入，不阻塞主流程。

使用 admin_session 上下文管理器保证连接安全释放。
"""

import uuid
from typing import Any

from sqlalchemy import update

from src.db.database import admin_session
from src.db.models.audit_log import AuditLog


class AuditWriter:
    """审计日志写入器"""

    async def log_query_start(
        self,
        thread_id: str,
        user_id: str,
        query: str,
        session_id: str | None = None,
    ) -> str:
        """记录查询开始"""
        async with admin_session() as session:
            log = AuditLog(
                thread_id=thread_id,
                user_id=uuid.UUID(user_id) if isinstance(user_id, str) else user_id,
                session_id=session_id,
                original_query=query,
            )
            session.add(log)
            await session.commit()
            return str(log.id)

    async def log_routing_result(
        self,
        thread_id: str,
        complexity: str,
        risk_level: str,
    ):
        """记录路由分析结果"""
        await self._update_log(thread_id, {
            "query_complexity": complexity,
            "risk_level": risk_level,
        })

    async def log_sql_generated(self, thread_id: str, sql: str):
        """记录LLM生成的SQL"""
        await self._update_log(thread_id, {"generated_sql": sql})

    async def log_validation_result(
        self,
        thread_id: str,
        sql_safe: bool,
        permission_pass: bool,
        executed_sql: str | None = None,
        reason: str | None = None,
    ):
        """记录安全校验和权限校验结果"""
        await self._update_log(thread_id, {
            "sql_safe": sql_safe,
            "permission_pass": permission_pass,
            "executed_sql": executed_sql,
            "error_message": reason if not sql_safe else None,
        })

    async def log_execution_result(
        self,
        thread_id: str,
        success: bool,
        row_count: int = 0,
        execution_time_ms: int = 0,
        error: str | None = None,
    ):
        """记录SQL执行结果"""
        await self._update_log(thread_id, {
            "execution_success": success,
            "row_count": row_count,
            "execution_time_ms": execution_time_ms,
            "error_message": error,
        })

    async def log_human_review(
        self,
        thread_id: str,
        approved: bool,
        reviewer_id: str | None = None,
        comment: str | None = None,
    ):
        """记录人工审核结果"""
        await self._update_log(thread_id, {
            "human_reviewed": True,
            "human_approved": approved,
            "reviewer_id": uuid.UUID(reviewer_id) if reviewer_id else None,
            "review_comment": comment,
        })

    async def log_masking_result(
        self,
        thread_id: str,
        masked_fields: list[str],
    ):
        """记录脱敏结果"""
        await self._update_log(thread_id, {"masked_fields": masked_fields})

    async def _update_log(self, thread_id: str, updates: dict[str, Any]):
        """更新审计日志"""
        async with admin_session() as session:
            stmt = (
                update(AuditLog)
                .where(AuditLog.thread_id == thread_id)
                .values(**updates)
            )
            await session.execute(stmt)
            await session.commit()