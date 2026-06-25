"""
Router Node - 意图分类与复杂度评估

使用轻量模型 (Qwen2.5-7B) 对用户查询进行快速三分类：
- simple_qa: 简单知识问答 → RAG管线
- data_analysis: 数据查询分析 → NL2SQL管线
- complex_task: 复杂多步骤任务 → Agent管线
"""

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from src.llm.factory import get_light_llm
from src.llm.prompts.router import ROUTER_SYSTEM_PROMPT, ROUTER_USER_TEMPLATE
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
        # 尝试修复常见问题：尾部逗号、单引号等
        cleaned = re.sub(r",\s*}", "}", json_str)
        cleaned = re.sub(r",\s*]", "]", cleaned)
        cleaned = cleaned.replace("'", '"')
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return {}


async def router_node(state: AgentState) -> dict:
    """路由节点：分析用户查询，判断意图类型和复杂度。

    从状态中读取：
    - user_id: 用户标识
    - user_dept: 用户部门
    - user_role: 用户角色
    - query: 用户查询文本

    返回：
    - intent: 意图分类 (simple_qa / data_analysis / complex_task)
    - query_complexity: 复杂度分级 (low / medium / high)
    - query: 透传原始查询
    - audit_log: 节点审计信息
    """
    user_id = state.get("user_id", "anonymous")
    user_dept = state.get("user_dept", "unknown")
    user_role = state.get("user_role", "user")
    query = state.get("query", "")

    logger.info(f"[Router] 开始路由分析 user={user_id} query_len={len(query)}")

    # 构建系统提示词，格式化用户上下文
    system_prompt = ROUTER_SYSTEM_PROMPT.format(
        user_id=user_id,
        user_dept=user_dept,
        user_role=user_role,
    )

    # 构建用户消息
    user_message = ROUTER_USER_TEMPLATE.format(query=query)

    # 调用轻量LLM
    llm = get_light_llm(max_tokens=1024)

    try:
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ])
        response_text = response.content if hasattr(response, "content") else str(response)

        logger.info(f"[Router] LLM响应: {response_text[:300]}")

        # 解析JSON
        parsed = _extract_json_from_response(response_text)

        intent = parsed.get("intent", "simple_qa")
        query_complexity = parsed.get("complexity", "low")

        # 校验意图值
        valid_intents = {"simple_qa", "data_analysis", "complex_task"}
        if intent not in valid_intents:
            logger.warning(f"[Router] 未知意图 '{intent}'，回退为 simple_qa")
            intent = "simple_qa"

        # 校验复杂度值
        valid_complexities = {"low", "medium", "high"}
        if query_complexity not in valid_complexities:
            logger.warning(f"[Router] 未知复杂度 '{query_complexity}'，回退为 low")
            query_complexity = "low"

    except Exception as e:
        logger.exception(f"[Router] LLM调用或解析失败: {e}")
        intent = "simple_qa"
        query_complexity = "low"

    audit_entry = {
        "nodes_visited": ["router"],
        "intent": intent,
        "complexity": query_complexity,
    }

    # 合并已有审计日志
    existing_audit = state.get("audit_log", {})
    if isinstance(existing_audit, dict):
        existing_nodes = existing_audit.get("nodes_visited", [])
        if isinstance(existing_nodes, list):
            audit_entry["nodes_visited"] = existing_nodes + ["router"]
        else:
            audit_entry["nodes_visited"] = ["router"]
        audit_entry.update({k: v for k, v in existing_audit.items() if k != "nodes_visited"})

    logger.info(f"[Router] 路由结果 intent={intent} complexity={query_complexity}")

    return {
        "intent": intent,
        "query_complexity": query_complexity,
        "query": query,
        "audit_log": audit_entry,
    }
