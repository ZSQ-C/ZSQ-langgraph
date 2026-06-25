"""
Tool Agent Node - 多工具调度与执行

按拓扑顺序执行 task_plan 中的每个步骤，调用对应工具，
并压缩工具输出为摘要。支持5种工具类型：
- rag_retrieval: 知识库文档检索
- sql_query: NL2SQL + 数据库查询
- document_parsing: 文档解析 (stub)
- ticket_report: 工单报表生成 (stub)
- http_api: 外部API调用 (stub)
"""

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.llm.factory import get_heavy_llm, get_sql_llm
from src.orchestration.state import AgentState
from src.tools.schema_retrieval import SchemaRetrievalTool
from src.tools.sql_execution import SQLExecutionTool

logger = logging.getLogger(__name__)

# 有效的工具名称集合
_VALID_TOOLS = {"rag_retrieval", "sql_query", "document_parsing", "ticket_report", "http_api"}


async def _compress_output(tool_name: str, tool_output: dict, max_chars: int = 300) -> str:
    """使用主力LLM将工具输出压缩为简洁摘要。

    Args:
        tool_name: 工具名称
        tool_output: 工具原始输出字典
        max_chars: 摘要最大字符数

    Returns:
        压缩后的摘要字符串
    """
    llm = get_heavy_llm(max_tokens=512)

    output_str = json.dumps(tool_output, ensure_ascii=False, default=str)
    # 截断过长输出，避免超出LLM上下文
    if len(output_str) > 4000:
        output_str = output_str[:4000] + "...[truncated]"

    prompt = (
        f"请将以下 [{tool_name}] 工具的输出结果压缩为不超过{max_chars}个字符的简洁摘要。"
        f"保留关键数字、表名、字段名和核心结论。\n\n"
        f"工具输出:\n{output_str}\n\n"
        f"摘要 (不超过{max_chars}字):"
    )

    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        summary = response.content if hasattr(response, "content") else str(response)
        summary = summary.strip()
        if len(summary) > max_chars:
            summary = summary[:max_chars]
        return summary
    except Exception as e:
        logger.warning(f"[ToolAgent] 压缩失败: {e}，使用简单截断")
        truncated = json.dumps(tool_output, ensure_ascii=False, default=str)
        if len(truncated) > max_chars:
            truncated = truncated[:max_chars - 3] + "..."
        return truncated


async def _execute_rag_retrieval(description: str) -> dict:
    """执行RAG文档检索。

    Args:
        description: 检索查询文本

    Returns:
        检索结果字典，包含 chunks 和 total
    """
    from src.tools.rag_retrieval import RAGRetrievalTool

    try:
        tool = RAGRetrievalTool()
        result = await tool._arun(query=description)
        return result if isinstance(result, dict) else {"chunks": [], "total": 0}
    except Exception as e:
        logger.warning(f"[ToolAgent] RAG检索失败: {e}")
        # 回退：使用LLM模拟知识检索
        llm = get_heavy_llm(max_tokens=1024)
        fallback_prompt = f"基于以下问题检索相关知识，返回JSON格式 {{\"chunks\": [{{\"content\": \"...\"}}], \"total\": 1}}:\n\n{description}"
        try:
            response = await llm.ainvoke([HumanMessage(content=fallback_prompt)])
            text = response.content if hasattr(response, "content") else str(response)
            # 尝试解析JSON
            import re
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
        except Exception:
            pass
        return {"chunks": [{"content": f"未检索到关于 '{description}' 的相关文档"}], "total": 0, "source": "fallback_llm"}


async def _execute_sql_query(description: str, user_id: str = "", user_role: str = "", user_dept: str = "") -> dict:
    """执行NL2SQL查询：检索表结构 → 生成SQL → 执行SQL。

    Args:
        description: 自然语言查询描述
        user_id: 用户ID
        user_role: 用户角色
        user_dept: 用户部门

    Returns:
        {
            "generated_sql": str,
            "execution_result": dict,
            "schemas": dict,
            "table_name": str | None,
            "row_count": int,
        }
    """
    # 步骤1: 检索相关表结构
    schema_tool = SchemaRetrievalTool()
    schema_tool.user_id = user_id
    schema_tool.user_role = user_role
    schema_tool.user_dept = user_dept
    schema_result = await schema_tool._arun(query=description)

    schemas_text = ""
    table_name = None
    if isinstance(schema_result, dict):
        schemas_text = schema_result.get("schemas", "")
        tables = schema_result.get("tables", [])
        table_name = tables[0] if tables else None

    if not schemas_text:
        logger.warning("[ToolAgent] 未检索到相关表结构")
        return {
            "generated_sql": "",
            "execution_result": {"success": False, "error": "未找到相关数据表"},
            "schemas": schema_result,
            "table_name": None,
            "row_count": 0,
        }

    # 步骤2: 生成SQL
    sql_llm = get_sql_llm()
    sql_system = (
        "你是一个SQL生成专家。根据提供的表结构和用户问题，生成只读SELECT查询。\n"
        "要求：\n"
        "1. 只生成SELECT语句，严禁INSERT/UPDATE/DELETE/DROP\n"
        "2. 使用标准SQL语法\n"
        "3. 添加适当的WHERE条件和LIMIT\n"
        "4. 直接输出SQL语句，不要包含任何解释或代码块标记"
    )
    sql_user = f"表结构:\n{schemas_text}\n\n用户问题:\n{description}\n\n请生成SQL查询:"

    sql_response = await sql_llm.ainvoke([
        SystemMessage(content=sql_system),
        HumanMessage(content=sql_user),
    ])
    generated_sql = sql_response.content if hasattr(sql_response, "content") else str(sql_response)
    generated_sql = generated_sql.strip()

    # 清理SQL代码块标记
    import re
    sql_match = re.search(r"```(?:sql)?\s*\n?(.*?)\n?```", generated_sql, re.DOTALL | re.IGNORECASE)
    if sql_match:
        generated_sql = sql_match.group(1).strip()

    logger.info(f"[ToolAgent] 生成SQL: {generated_sql[:200]}")

    # 步骤3: 执行SQL
    exec_tool = SQLExecutionTool()
    exec_tool.user_id = user_id
    exec_tool.user_role = user_role
    exec_tool.user_dept = user_dept
    exec_result = await exec_tool._arun(sql=generated_sql)

    row_count = 0
    if isinstance(exec_result, dict) and exec_result.get("success"):
        row_count = exec_result.get("row_count", 0)

    return {
        "generated_sql": generated_sql,
        "execution_result": exec_result if isinstance(exec_result, dict) else {"result": str(exec_result)},
        "schemas": schema_result if isinstance(schema_result, dict) else {"raw": str(schema_result)},
        "table_name": table_name,
        "row_count": row_count,
    }


async def _execute_document_parsing(description: str) -> dict:
    """文档解析工具 (stub)。

    Args:
        description: 解析描述

    Returns:
        stub 占位结果
    """
    logger.info(f"[ToolAgent] document_parsing stub: {description[:100]}")
    return {"info": "document_parsing stub", "message": "文档解析功能待实现", "description": description}


async def _execute_ticket_report(description: str) -> dict:
    """工单报表工具 (stub)。

    Args:
        description: 报表描述

    Returns:
        stub 占位结果
    """
    logger.info(f"[ToolAgent] ticket_report stub: {description[:100]}")
    return {"info": "ticket_report stub", "message": "工单报表功能待实现", "description": description}


async def _execute_http_api(description: str) -> dict:
    """外部HTTP API调用工具 (stub)。

    Args:
        description: API调用描述

    Returns:
        stub 占位结果
    """
    logger.info(f"[ToolAgent] http_api stub: {description[:100]}")
    return {"info": "http_api stub", "message": "HTTP API调用功能待实现", "description": description}


async def _execute_step(
    step: dict,
    state: AgentState,
) -> dict:
    """执行单个步骤，分发到对应工具。

    Args:
        step: 步骤定义 {"step_id": int, "description": str, "tool": str, "depends_on": [int]}
        state: Agent状态

    Returns:
        {"step_id": int, "tool_name": str, "tool_input": str, "tool_output": dict, "summary": str}
    """
    step_id = step["step_id"]
    tool_name = step["tool"]
    description = step.get("description", "")

    user_id = state.get("user_id", "")
    user_role = state.get("user_role", "")
    user_dept = state.get("user_dept", "")

    logger.info(f"[ToolAgent] 执行 Step{step_id} tool={tool_name} desc={description[:80]}")

    tool_output: dict = {}

    if tool_name == "rag_retrieval":
        tool_output = await _execute_rag_retrieval(description)

    elif tool_name == "sql_query":
        tool_output = await _execute_sql_query(
            description=description,
            user_id=user_id,
            user_role=user_role,
            user_dept=user_dept,
        )

    elif tool_name == "document_parsing":
        tool_output = await _execute_document_parsing(description)

    elif tool_name == "ticket_report":
        tool_output = await _execute_ticket_report(description)

    elif tool_name == "http_api":
        tool_output = await _execute_http_api(description)

    else:
        logger.warning(f"[ToolAgent] 未知工具 '{tool_name}'，回退为 rag_retrieval")
        tool_output = await _execute_rag_retrieval(description)

    # 压缩输出为摘要
    summary = await _compress_output(tool_name, tool_output)

    return {
        "step_id": step_id,
        "tool_name": tool_name,
        "tool_input": description,
        "tool_output": tool_output,
        "summary": summary,
    }


def _topological_order(steps: list[dict]) -> list[list[dict]]:
    """将步骤按拓扑层级分组，同一层级的步骤可以按序执行。

    每一步用 depends_on 字段声明其前置依赖。无依赖的步骤在第一层，
    后续层级包含那些依赖已满足的步骤。

    Args:
        steps: 步骤列表

    Returns:
        按层级分组的步骤列表，如 [[step1, step2], [step3], [step4]]
    """
    if not steps:
        return []

    step_map: dict[int, dict] = {s["step_id"]: s for s in steps}
    completed_ids: set[int] = set()
    layers: list[list[dict]] = []

    remaining = set(step_map.keys())

    while remaining:
        ready: list[dict] = []
        for sid in sorted(remaining):
            step = step_map[sid]
            deps = step.get("depends_on", [])
            if all(d in completed_ids for d in deps):
                ready.append(step)

        if not ready:
            # 存在循环依赖或孤立依赖，将剩余步骤全部加入当前层
            logger.warning(f"[ToolAgent] 检测到不可满足的依赖，剩余步骤加入当前层: {remaining}")
            for sid in sorted(remaining):
                ready.append(step_map[sid])

        for step in ready:
            completed_ids.add(step["step_id"])
            remaining.discard(step["step_id"])

        layers.append(ready)

    return layers


async def tool_node(state: AgentState) -> dict:
    """工具代理节点：按拓扑顺序执行多工具调用管线。

    从状态中读取：
    - task_plan: 步骤规划列表
    - user_id / user_role / user_dept: 用户上下文

    返回：
    - execution_result: 包含 steps、raw_outputs、summary
    - generated_sql: 如果有SQL查询步骤，存储生成的SQL
    - messages: 包含执行摘要的AIMessage
    """
    task_plan: list[dict] = state.get("task_plan", [])
    user_id = state.get("user_id", "")

    if not task_plan:
        logger.warning("[ToolAgent] 无任务计划，跳过执行")
        return {
            "execution_result": {"steps": [], "raw_outputs": {}, "summary": "没有可执行的任务步骤。"},
            "messages": [AIMessage(content="没有可执行的任务步骤。")],
        }

    logger.info(f"[ToolAgent] 开始执行任务 user={user_id} total_steps={len(task_plan)}")

    # 拓扑排序分组
    layers = _topological_order(task_plan)

    all_tool_records: list[dict] = []
    raw_outputs: dict[str, dict] = {}
    summaries: list[str] = []
    generated_sql = ""

    # 逐层执行
    for layer_idx, layer in enumerate(layers):
        logger.info(f"[ToolAgent] 执行第{layer_idx + 1}层 ({len(layer)}个步骤)")

        for step in layer:
            record = await _execute_step(step, state)
            all_tool_records.append(record)
            raw_outputs[str(step["step_id"])] = record["tool_output"]
            summaries.append(f"[{step['tool']}] {record['summary']}")

            # 如果是SQL查询步骤，收集生成的SQL
            if step["tool"] == "sql_query" and "generated_sql" in record["tool_output"]:
                sql = record["tool_output"]["generated_sql"]
                if sql:
                    generated_sql = sql

    # 合并摘要
    combined_summary = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(summaries))

    execution_result = {
        "steps": all_tool_records,
        "raw_outputs": raw_outputs,
        "summary": combined_summary,
    }

    logger.info(f"[ToolAgent] 执行完成 steps={len(all_tool_records)} summary_len={len(combined_summary)}")

    return_dict: dict[str, Any] = {
        "execution_result": execution_result,
        "messages": [AIMessage(content=combined_summary)],
    }

    # 如果有SQL生成，存入状态
    if generated_sql:
        return_dict["generated_sql"] = generated_sql

    return return_dict
