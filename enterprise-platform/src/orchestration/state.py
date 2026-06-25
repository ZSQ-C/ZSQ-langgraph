"""
AgentState - 企业RAG+Agent平台的完整状态定义

所有可观测字段集中在此TypedDict中，由LangGraph管理读写。
"""
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """跨节点的完整状态容器"""

    # ---- 用户上下文 ----
    user_id: str
    user_dept: str
    user_role: str

    # ---- 查询与对话 ----
    query: str
    messages: Annotated[list[BaseMessage], add_messages]

    # ---- 路由结果 ----
    intent: str                      # simple_qa | data_analysis | complex_task
    query_complexity: str            # low | medium | high

    # ---- 任务规划 ----
    task_plan: list[dict]            # [{"step_id":1, "description":"...", "tool":"...", "depends_on":[]}]
    relevant_schemas: str            # DDL格式的表结构描述文本

    # ---- SQL生成 ----
    generated_sql: str
    sql_retry_count: int

    # ---- 执行结果 ----
    execution_result: dict           # {"success": bool, "columns": [...], "data": [...], ...}
    execution_error: str

    # ---- 合规 ----
    sql_safe: bool
    permission_pass: bool

    # ---- 人工审核 ----
    requires_human_review: bool
    human_approval: bool

    # ---- Critic 溯源 ----
    critic_result: dict              # {"quality_score": float, "passed": [...], "failed_checks": [...]}
    replan_count: int
    prev_score: float

    # ---- RAG ----
    retrieved_docs: list[dict]       # [{"chunk_id":..., "content":..., "page":..., "score":...}]
    hallucination_check_pass: bool

    # ---- 最终输出 ----
    final_answer: str
    masked_result: dict              # 脱敏后的结果
    audit_log: dict                  # {"thread_id": str, "nodes_visited": [...], ...}
