"""
条件边函数 - 决定Agent管线的流转路径

每条边函数接收 AgentState 并返回下一个节点名称（字符串）。
"""

from src.orchestration.state import AgentState


def route_after_router(state: AgentState) -> str:
    """根据路由意图分发到对应管线。

    Returns:
        "rag_pipeline"   — simple_qa
        "nl2sql_pipeline" — data_analysis
        "planner"         — complex_task
    """
    intent = state.get("intent", "simple_qa")

    if intent == "simple_qa":
        return "rag_pipeline"
    elif intent == "data_analysis":
        return "nl2sql_pipeline"
    elif intent == "complex_task":
        return "planner"
    else:
        # 未知意图默认走RAG管线
        return "rag_pipeline"


def should_continue_after_critic(state: AgentState) -> str:
    """评估Critic打分，决定是结束还是重新规划。

    规则：
    - quality_score >= 0.8 → 通过，去 summary
    - replan_count >= 3 → 达到最大重试次数，去 summary
    - 得分停滞（prev_score 与当前相差 < 0.05）→ 重试无益，去 summary
    - 否则 → 重新规划，去 planner

    重要：返回 "planner" 前递增 replan_count。

    Returns:
        "summary" or "planner"
    """
    critic = state.get("critic_result", {})
    quality_score = critic.get("quality_score", 0.0)

    replan_count = state.get("replan_count", 0)
    prev_score = state.get("prev_score", 0.0)

    # 达标
    if quality_score >= 0.8:
        return "summary"

    # 超过最大重试次数
    if replan_count >= 3:
        return "summary"

    # 停滞检测：连续两轮得分提升不足 0.05
    if abs(quality_score - prev_score) < 0.05 and replan_count > 0:
        return "summary"

    # 重新规划 — 外部调用方在进入 planner 前递增 replan_count
    # 这里通过修改 state 来递增（实际上是不可变的 — 由节点负责递增）
    return "planner"


def route_after_compliance(state: AgentState) -> str:
    """合规审核后的路由。

    - 需要人工审核 → human_review
    - SQL安全且权限通过 → summary
    - 不通过 → END（直接终止，避免执行不安全SQL）

    Returns:
        "human_review" | "summary" | "END"
    """
    if state.get("requires_human_review", False):
        return "human_review"

    sql_safe = state.get("sql_safe", False)
    permission_pass = state.get("permission_pass", False)

    if sql_safe and permission_pass:
        return "summary"

    return "END"


def route_after_sql_execution(state: AgentState) -> str:
    """SQL执行后的路由。

    - 执行成功 → summary
    - 执行失败且未超重试 → nl2sql_pipeline（重新生成SQL）
    - 执行失败且超过重试 → END

    Returns:
        "nl2sql_pipeline" | "summary" | "END"
    """
    exec_result = state.get("execution_result", {})
    success = exec_result.get("success", False)

    if success:
        return "summary"

    retry_count = state.get("sql_retry_count", 0)
    if retry_count < 2:
        return "nl2sql_pipeline"

    return "END"
