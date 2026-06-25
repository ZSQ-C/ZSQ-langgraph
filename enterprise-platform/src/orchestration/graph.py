"""
主编排图 — 构建完整的 LangGraph StateGraph

8个核心节点 + 条件边，编译为可执行应用。
"""
import logging
from typing import Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph

from src.orchestration.edges import (
    route_after_compliance,
    route_after_router,
    route_after_sql_execution,
    should_continue_after_critic,
)
from src.orchestration.nodes.compliance import compliance_node
from src.orchestration.nodes.critic import critic_node
from src.orchestration.nodes.nl2sql_pipeline import nl2sql_pipeline_node
from src.orchestration.nodes.planner import planner_node
from src.orchestration.nodes.rag_pipeline import rag_pipeline_node
from src.orchestration.nodes.router import router_node
from src.orchestration.nodes.summary import summary_node
from src.orchestration.nodes.tool_agent import tool_node
from src.orchestration.state import AgentState

logger = logging.getLogger(__name__)


def build_graph(
    checkpointer: Optional[BaseCheckpointSaver] = None,
) -> StateGraph:
    """构建完整的编排图。

    Args:
        checkpointer: 可选的外部检查点保存器（如 RedisSaver）。
                      如果为 None，使用内存检查点。

    Returns:
        编译后的 StateGraph（尚未 compile，调用方自行 .compile()）。
    """
    builder = StateGraph(AgentState)

    # ===================== 添加所有节点 =====================
    builder.add_node("router", router_node)
    builder.add_node("planner", planner_node)
    builder.add_node("tool_agent", tool_node)
    builder.add_node("critic", critic_node)
    builder.add_node("summary", summary_node)
    builder.add_node("rag_pipeline", rag_pipeline_node)
    builder.add_node("nl2sql_pipeline", nl2sql_pipeline_node)
    builder.add_node("compliance", compliance_node)

    # ===================== 入口 =====================
    builder.set_entry_point("router")

    # ===================== 条件边 =====================
    # router → rag_pipeline | nl2sql_pipeline | planner
    builder.add_conditional_edges(
        "router",
        route_after_router,
        {
            "rag_pipeline": "rag_pipeline",
            "nl2sql_pipeline": "nl2sql_pipeline",
            "planner": "planner",
        },
    )

    # rag_pipeline → END（独立管线，不进入Agent主循环）
    builder.add_edge("rag_pipeline", END)

    # nl2sql_pipeline → compliance
    builder.add_edge("nl2sql_pipeline", "compliance")

    # compliance → human_review | summary | END
    builder.add_conditional_edges(
        "compliance",
        route_after_compliance,
        {
            "human_review": "human_review" if _has_human_review_node() else "summary",
            "summary": "summary",
            "END": END,
        },
    )

    # planner → tool_agent
    builder.add_edge("planner", "tool_agent")

    # tool_agent → critic
    builder.add_edge("tool_agent", "critic")

    # critic → summary | planner（重规划循环）
    builder.add_conditional_edges(
        "critic",
        should_continue_after_critic,
        {
            "summary": "summary",
            "planner": "planner",
        },
    )

    # summary → END
    builder.add_edge("summary", END)

    # ===================== 编译 =====================
    compile_kwargs = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer
        logger.info("使用外部检查点保存器编译图")

    compiled = builder.compile(**compile_kwargs)
    logger.info("编排图编译完成（8节点 + 条件边）")
    return compiled


def _has_human_review_node() -> bool:
    """检查是否注入了 human_review 节点（默认构建不包含）。"""
    return False
