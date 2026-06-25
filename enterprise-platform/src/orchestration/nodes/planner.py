"""
Planner Node - 任务分解与步骤规划

使用主力模型 (DeepSeek-V3) 将复杂用户查询拆解为有序执行步骤。
支持 Critic 反馈驱动的重规划 (replan)。
"""

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from src.llm.factory import get_heavy_llm
from src.llm.prompts.planner import PLANNER_SYSTEM_PROMPT, PLANNER_USER_TEMPLATE
from src.orchestration.state import AgentState

logger = logging.getLogger(__name__)


def _extract_json_from_response(response: str) -> dict:
    """从LLM响应中提取JSON，处理markdown代码块包裹的情况。

    Args:
        response: LLM原始文本响应

    Returns:
        解析后的JSON字典。解析失败返回空字典。
    """
    if not response:
        return {}

    text = response.strip()

    # 尝试匹配 ```json ... ``` 或 ``` ... ``` 代码块
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL | re.IGNORECASE)
    if match:
        json_str = match.group(1).strip()
    else:
        # 无代码块时尝试匹配第一个 { ... } 对象
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            json_str = match.group(0).strip()
        else:
            json_str = text

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*}", "}", json_str)
        cleaned = re.sub(r",\s*]", "]", cleaned)
        cleaned = cleaned.replace("'", '"')
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return {}


def _build_fallback_plan(query: str) -> list[dict]:
    """当LLM规划失败时，构建单步回退计划。

    Args:
        query: 用户原始查询

    Returns:
        包含单个 rag_retrieval 步骤的列表
    """
    return [
        {
            "step_id": 1,
            "description": query,
            "tool": "rag_retrieval",
            "depends_on": [],
        }
    ]


def _validate_steps(steps: list) -> list[dict]:
    """校验并规范化步骤列表。

    确保每个步骤都有 step_id、description、tool、depends_on 字段。
    过滤掉无效步骤。

    Args:
        steps: LLM输出的原始步骤列表

    Returns:
        规范化后的步骤列表
    """
    if not isinstance(steps, list):
        return []

    valid_steps = []
    used_ids = set()

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue

        step_id = step.get("step_id", i + 1)
        if step_id in used_ids:
            continue
        used_ids.add(step_id)

        description = step.get("description", "")
        tool = step.get("tool", "rag_retrieval")
        depends_on = step.get("depends_on", [])

        if not isinstance(depends_on, list):
            depends_on = [depends_on] if depends_on else []

        # 校验工具名称
        valid_tools = {"rag_retrieval", "sql_query", "document_parsing", "ticket_report", "http_api"}
        if tool not in valid_tools:
            logger.warning(f"[Planner] 未知工具 '{tool}'，回退为 rag_retrieval")
            tool = "rag_retrieval"

        valid_steps.append({
            "step_id": int(step_id),
            "description": str(description),
            "tool": tool,
            "depends_on": [int(d) for d in depends_on],
        })

    return valid_steps


async def planner_node(state: AgentState) -> dict:
    """规划节点：将复杂用户查询拆解为有序执行步骤。

    从状态中读取：
    - query: 用户查询文本
    - critic_result: (可选) Critic 反馈，用于重规划

    返回：
    - task_plan: 步骤列表
    - replan_count: 递增后的重规划次数
    - prev_score: 上一轮 Critic 质量评分
    """
    query = state.get("query", "")

    # 提取 Critic 反馈
    critic_result = state.get("critic_result", {})

    if isinstance(critic_result, dict):
        feedback = critic_result.get("recommendation", "")
        if not feedback and critic_result:
            # 无 recommendation 但有其他字段，序列化整个结果作为反馈
            failed = critic_result.get("failed_checks", [])
            missing = critic_result.get("missing", [])
            superfluous = critic_result.get("superfluous", [])
            if failed or missing or superfluous:
                feedback = json.dumps({
                    "failed_checks": failed,
                    "missing": missing,
                    "superfluous": superfluous,
                }, ensure_ascii=False)
            elif critic_result:
                feedback = json.dumps(critic_result, ensure_ascii=False)
    elif isinstance(critic_result, str) and critic_result.strip():
        feedback = critic_result
    else:
        feedback = ""

    current_replan_count = state.get("replan_count", 0)
    if feedback:
        logger.info(f"[Planner] 重规划模式 feedback_len={len(feedback)} replan_count={current_replan_count}")
    else:
        logger.info(f"[Planner] 首次规划 query_len={len(query)}")

    # 构建系统提示词，格式化反馈
    system_prompt = PLANNER_SYSTEM_PROMPT.format(
        feedback=feedback if feedback else "无（首次规划，无需修正）",
    )

    # 构建用户消息
    user_message = PLANNER_USER_TEMPLATE.format(query=query)

    # 调用主力LLM
    llm = get_heavy_llm()

    task_plan = []
    try:
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ])
        response_text = response.content if hasattr(response, "content") else str(response)

        logger.info(f"[Planner] LLM响应: {response_text[:300]}")

        # 解析JSON
        parsed = _extract_json_from_response(response_text)
        raw_steps = parsed.get("steps", [])

        # 校验步骤
        task_plan = _validate_steps(raw_steps)

        if not task_plan:
            logger.warning("[Planner] LLM返回空步骤，使用回退计划")
            task_plan = _build_fallback_plan(query)

    except Exception as e:
        logger.exception(f"[Planner] LLM调用或解析失败: {e}")
        task_plan = _build_fallback_plan(query)

    # 递增重规划计数
    replan_count = current_replan_count + 1
    prev_score = critic_result.get("quality_score", 0.0) if isinstance(critic_result, dict) else 0.0

    logger.info(f"[Planner] 规划完成 steps={len(task_plan)} replan_count={replan_count}")
    for step in task_plan:
        logger.debug(f"  Step {step['step_id']}: [{step['tool']}] {step['description'][:80]} deps={step['depends_on']}")

    return {
        "task_plan": task_plan,
        "replan_count": replan_count,
        "prev_score": float(prev_score),
    }
