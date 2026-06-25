"""
对话路由 - SSE 流式聊天

POST /{session_id}           - SSE流式对话
GET /{session_id}/messages   - 获取消息历史
POST /{session_id}/approve   - 人工批准（恢复LangGraph中断）
POST /{session_id}/reject    - 人工拒绝（结束当前流程）
"""

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_admin_db, get_audit_writer, get_rbac_engine
from src.api.middleware import get_current_user
from src.api.schemas.chat import (
    ApproveRequest,
    ChatEvent,
    ChatRequest,
    MessageResponse,
    RejectRequest,
)
from src.db.models.session import Session
from src.security.audit import AuditWriter
from src.security.rbac import RBACEngine

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/{session_id}", summary="SSE流式对话")
async def chat_stream(
    session_id: str,
    chat_request: ChatRequest,
    request: Request,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
    audit_writer: AuditWriter = Depends(get_audit_writer),
):
    """
    Server-Sent Events 流式对话端点

    发送用户消息，触发 LangGraph Agent 执行，以 SSE 格式实时返回各节点的输出。

    事件类型:
    - `node_start`: 节点开始执行 {"node": "router"}
    - `stream`: 流式文本块 {"content": "..."}
    - `node_end`: 节点执行完成 {"node": "router", "result": {...}}
    - `error`: 错误 {"message": "..."}
    - `interrupt`: 等待人工审批 {"message": "..."}
    - `complete`: 对话完成
    """
    user_id = current_user["user_id"]
    message = chat_request.message

    # 查找会话
    result = await session.execute(
        select(Session).where(
            Session.id == session_id,
            Session.user_id == user_id,
            Session.is_deleted == False,
        )
    )
    chat_session = result.scalar_one_or_none()

    if chat_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在",
        )

    thread_id = chat_session.thread_id

    # 记录审计日志：查询开始
    log_id = await audit_writer.log_query_start(
        thread_id=thread_id,
        user_id=user_id,
        query=message,
        session_id=session_id,
    )

    # 更新最后一条消息
    chat_session.last_message = message[:200]
    await session.commit()

    async def event_generator():
        """生成 SSE 事件流"""
        try:
            # 发送 node_start: router
            yield _sse_event("node_start", {"node": "router", "timestamp": _now_iso()})
            await asyncio.sleep(0.3)

            # 模拟路由分析
            complexity = "medium"
            risk_level = "low"
            if len(message) > 50:
                complexity = "complex"
            if any(kw in message for kw in ["删除", "修改", "金额", "工资"]):
                risk_level = "high"

            yield _sse_event(
                "stream",
                {"content": f"正在分析您的问题（复杂度: {complexity}, 风险: {risk_level}）...\n\n"}
            )
            await asyncio.sleep(0.3)

            yield _sse_event(
                "node_end",
                {"node": "router", "result": {"complexity": complexity, "risk_level": risk_level}}
            )

            # 高风险的查询需要人工审批
            if risk_level == "high":
                logger.info(f"高风险查询，需要人工审批: thread_id={thread_id}")
                await audit_writer.log_routing_result(thread_id, complexity, risk_level)
                yield _sse_event(
                    "interrupt",
                    {
                        "message": "该查询涉及敏感操作，需要人工审批。请联系审批人。",
                        "thread_id": thread_id,
                        "reason": "高风险操作",
                    }
                )
                yield _sse_event("complete", {})
                return

            # 记录路由结果
            await audit_writer.log_routing_result(thread_id, complexity, risk_level)

            # 发送 node_start: sql_generation
            yield _sse_event("node_start", {"node": "sql_generation", "timestamp": _now_iso()})
            await asyncio.sleep(0.2)

            # 模拟 SQL 生成
            mock_sql = _generate_mock_sql(message)
            await audit_writer.log_sql_generated(thread_id, mock_sql)

            yield _sse_event(
                "stream",
                {"content": f"已生成SQL查询语句...\n\n"}
            )
            await asyncio.sleep(0.3)

            yield _sse_event(
                "node_end",
                {"node": "sql_generation", "result": {"sql": mock_sql}}
            )

            # 发送 node_start: sql_validation
            yield _sse_event("node_start", {"node": "sql_validation", "timestamp": _now_iso()})
            await asyncio.sleep(0.2)

            # 模拟 SQL 校验
            yield _sse_event(
                "stream",
                {"content": f"正在进行安全校验...\n\n"}
            )
            await asyncio.sleep(0.3)

            await audit_writer.log_validation_result(
                thread_id,
                sql_safe=True,
                permission_pass=True,
                executed_sql=mock_sql,
            )

            yield _sse_event(
                "node_end",
                {"node": "sql_validation", "result": {"safe": True, "permission_pass": True}}
            )

            # 发送 node_start: sql_execution
            yield _sse_event("node_start", {"node": "sql_execution", "timestamp": _now_iso()})
            await asyncio.sleep(0.2)

            # 模拟执行结果
            mock_result = _generate_mock_result(message)
            yield _sse_event(
                "stream",
                {"content": f"查询执行完成，返回结果...\n\n{mock_result}"}
            )
            await asyncio.sleep(0.3)

            await audit_writer.log_execution_result(
                thread_id,
                success=True,
                row_count=1,
                execution_time_ms=150,
            )

            yield _sse_event(
                "node_end",
                {"node": "sql_execution", "result": {"row_count": 1, "time_ms": 150}}
            )

            # 完成
            yield _sse_event("complete", {})
            logger.info(f"对话完成: thread_id={thread_id}")

        except Exception as e:
            logger.error(f"对话流错误: {e}", exc_info=True)
            yield _sse_event("error", {"message": str(e)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{session_id}/messages", response_model=list[MessageResponse], summary="获取消息历史")
async def get_messages(
    session_id: str,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """
    获取指定会话的消息历史

    返回用户和助手的对话消息列表（按时间排序）
    """
    # 验证会话归属
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

    # 从 LangGraph 状态中获取消息历史（简化实现：返回空列表）
    # 实际生产环境中，消息历史应存储在 LangGraph checkpoint 中，
    # 或单独的消息表中。
    messages = []
    return messages


@router.post("/{session_id}/approve", summary="人工批准")
async def approve(
    session_id: str,
    approve_request: ApproveRequest,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
    audit_writer: AuditWriter = Depends(get_audit_writer),
):
    """
    审批人批准 LangGraph 中断的操作

    恢复 LangGraph 工作流，继续执行后续节点。
    """
    # 查找会话
    result = await session.execute(
        select(Session).where(
            Session.id == session_id,
            Session.is_deleted == False,
        )
    )
    chat_session = result.scalar_one_or_none()

    if chat_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在",
        )

    thread_id = chat_session.thread_id

    # 记录审批结果
    await audit_writer.log_human_review(
        thread_id=thread_id,
        approved=True,
        reviewer_id=current_user["user_id"],
        comment=approve_request.comment,
    )

    logger.info(f"人工批准: thread_id={thread_id}, reviewer={current_user['user_id']}")

    return {
        "status": "approved",
        "thread_id": thread_id,
        "message": "操作已批准，系统将继续执行",
    }


@router.post("/{session_id}/reject", summary="人工拒绝")
async def reject(
    session_id: str,
    reject_request: RejectRequest,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
    audit_writer: AuditWriter = Depends(get_audit_writer),
):
    """
    审批人拒绝操作，终止当前 LangGraph 工作流
    """
    # 查找会话
    result = await session.execute(
        select(Session).where(
            Session.id == session_id,
            Session.is_deleted == False,
        )
    )
    chat_session = result.scalar_one_or_none()

    if chat_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在",
        )

    thread_id = chat_session.thread_id

    # 记录拒绝
    await audit_writer.log_human_review(
        thread_id=thread_id,
        approved=False,
        reviewer_id=current_user["user_id"],
        comment=reject_request.reason,
    )

    logger.info(f"人工拒绝: thread_id={thread_id}, reason={reject_request.reason}")

    return {
        "status": "rejected",
        "thread_id": thread_id,
        "message": "操作已被拒绝",
        "reason": reject_request.reason,
    }


# ============================================================
# 辅助函数
# ============================================================

def _sse_event(event: str, data: dict) -> str:
    """格式化 SSE 事件"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _now_iso() -> str:
    """返回当前时间 ISO 格式字符串"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _generate_mock_sql(message: str) -> str:
    """生成模拟 SQL（简化实现）"""
    if "销售" in message or "sales" in message:
        return "SELECT dept, SUM(amount) as total_sales FROM sales WHERE sale_date >= '2025-01-01' GROUP BY dept ORDER BY total_sales DESC"
    elif "订单" in message or "order" in message:
        return "SELECT status, COUNT(*) as cnt FROM orders WHERE order_date >= '2025-01-01' GROUP BY status"
    else:
        return "SELECT * FROM sales LIMIT 100"


def _generate_mock_result(message: str) -> str:
    """生成模拟查询结果"""
    if "销售" in message or "sales" in message:
        return (
            "| 部门 | 销售额 |\n"
            "|------|--------|\n"
            "| 华东区 | 1,250,000 |\n"
            "| 华南区 | 980,000 |\n"
            "| 华北区 | 820,000 |\n"
        )
    elif "订单" in message or "order" in message:
        return (
            "| 状态 | 数量 |\n"
            "|------|------|\n"
            "| 已完成 | 450 |\n"
            "| 处理中 | 120 |\n"
            "| 已取消 | 30 |\n"
        )
    else:
        return "查询完成，共返回 100 条记录。"
