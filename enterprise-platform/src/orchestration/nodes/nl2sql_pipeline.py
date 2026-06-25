"""
NL2SQL Pipeline Node - 自然语言转 SQL 完整管线

专用于 data_analysis 场景，将用户自然语言问题转化为安全的 SQL 查询并执行。
包含 7 个步骤：Schema 向量检索 → SQL 生成 → EXPLAIN 预检
→ 权限检查 → SQL 执行 → 数据脱敏 → 重试路由。

每一步都有独立的 try/except 保护。
"""

import json
import logging
import re
import uuid

from sqlalchemy import text

from config.settings import settings
from src.db.database import read_only_session
from src.llm.factory import get_sql_llm
from src.llm.prompts.sql_generation import (
    SQL_GENERATION_SYSTEM_PROMPT,
    SQL_GENERATION_USER_TEMPLATE,
)
from src.orchestration.state import AgentState
from src.security.rbac import RBACEngine
from src.security.sql_guard import SQLGuard
from src.tools.data_masking import DataMaskingTool
from src.tools.schema_retrieval import SchemaRetrievalTool
from src.tools.sql_execution import SQLExecutionTool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL 提取正则
# ---------------------------------------------------------------------------

SQL_BLOCK_RE = re.compile(
    r"```sql\s*\n?(.*?)```",
    re.DOTALL | re.IGNORECASE,
)

SQL_GENERIC_BLOCK_RE = re.compile(
    r"```\s*\n?(.*?)```",
    re.DOTALL,
)


def _extract_sql_from_response(response_text: str) -> str | None:
    """从 LLM 响应中提取 SQL 语句。

    优先匹配 ```sql ... ``` 代码块，次选纯 ``` ... ``` 代码块，
    最后回退到直接文本解析。
    """
    if not response_text:
        return None

    # 优先匹配 sql 代码块
    match = SQL_BLOCK_RE.search(response_text)
    if match:
        sql = match.group(1).strip()
        if sql:
            return sql

    # 回退到通用代码块
    match = SQL_GENERIC_BLOCK_RE.search(response_text)
    if match:
        sql = match.group(1).strip()
        if sql and _looks_like_sql(sql):
            return sql

    # 最后尝试直接解析整个响应
    text = response_text.strip()
    if _looks_like_sql(text):
        return text

    return None


def _looks_like_sql(text: str) -> bool:
    """快速判断文本是否像 SQL 语句。"""
    upper = text.upper().strip()
    return upper.startswith("SELECT") or upper.startswith("WITH")


# ---------------------------------------------------------------------------
# Step 1: Schema 向量检索
# ---------------------------------------------------------------------------

async def _retrieve_schemas(query: str) -> dict:
    """使用 SchemaRetrievalTool 进行语义检索获取相关表结构。

    Returns:
        {"schemas": "DDL text", "tables": ["t1", "t2", ...]}
    """
    try:
        tool = SchemaRetrievalTool(top_k=5)
        result = await tool._arun(query)
        if isinstance(result, dict) and "error" not in result:
            schemas = result.get("schemas", "")
            tables = result.get("tables", [])
            logger.info("[NL2SQL:Schema] 检索到 %d 张相关表: %s", len(tables), tables)
            return {"schemas": str(schemas), "tables": list(tables) if tables else []}
        else:
            logger.warning("[NL2SQL:Schema] Schema 检索返回异常: %s", result)
            return {"schemas": "", "tables": []}
    except Exception as e:
        logger.warning("[NL2SQL:Schema] Schema 检索失败: %s", e)
        return {"schemas": "", "tables": []}


# ---------------------------------------------------------------------------
# Step 2: SQL 生成
# ---------------------------------------------------------------------------

async def _generate_sql(query: str, schemas_text: str) -> str | None:
    """使用 get_sql_llm() 生成 SQL。

    Args:
        query: 用户自然语言问题。
        schemas_text: Schema 文本描述。

    Returns:
        生成的 SQL 字符串，失败返回 None。
    """
    try:
        llm = get_sql_llm()
        system_prompt = SQL_GENERATION_SYSTEM_PROMPT.format(
            max_rows=settings.sql_max_rows,
            schemas=schemas_text if schemas_text else "（未检索到相关表结构，请根据常识推断）",
        )
        user_prompt = SQL_GENERATION_USER_TEMPLATE.format(query=query)

        response = await llm.ainvoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        response_text = response.content if hasattr(response, "content") else str(response)

        sql = _extract_sql_from_response(response_text)
        if sql:
            logger.info("[NL2SQL:Gen] SQL 生成成功: %s", sql[:200])
            return sql

        logger.warning("[NL2SQL:Gen] 无法从 LLM 响应中提取 SQL: %s", response_text[:300])
        return None
    except Exception as e:
        logger.warning("[NL2SQL:Gen] SQL 生成失败: %s", e)
        return None


# ---------------------------------------------------------------------------
# Step 3: EXPLAIN 预检
# ---------------------------------------------------------------------------

async def _explain_precheck(sql: str) -> dict:
    """执行 EXPLAIN (FORMAT JSON) 预检，评估查询代价。

    Returns:
        {
            "success": bool,
            "total_cost": float,
            "plan_json": dict,
            "error": str | None,
        }
    """
    try:
        # 清理 SQL 尾部，确保是单个语句
        clean_sql = sql.strip().rstrip(";")

        async with read_only_session() as session:
            result = await session.execute(
                text(f"EXPLAIN (FORMAT JSON) {clean_sql}")
            )
            rows = result.fetchall()

        if not rows:
            return {"success": False, "total_cost": 0.0, "plan_json": {}, "error": "EXPLAIN 返回空结果"}

        plan_json = rows[0][0] if rows[0] else {}
        if isinstance(plan_json, list):
            plan_json = plan_json[0] if plan_json else {}

        # 提取总代价
        total_cost = _extract_total_cost(plan_json)

        logger.info("[NL2SQL:Explain] 查询计划总代价: %.2f", total_cost)

        return {
            "success": True,
            "total_cost": total_cost,
            "plan_json": plan_json,
            "error": None,
        }
    except Exception as e:
        logger.warning("[NL2SQL:Explain] EXPLAIN 预检失败: %s", e)
        return {"success": False, "total_cost": 0.0, "plan_json": {}, "error": str(e)}


def _extract_total_cost(plan_json: dict) -> float:
    """从 EXPLAIN JSON 中递归提取 Total Cost 的最大值。"""
    max_cost = 0.0

    def traverse(node):
        nonlocal max_cost
        tc = node.get("Total Cost", 0)
        if tc > max_cost:
            max_cost = float(tc)
        for child in node.get("Plans", []):
            traverse(child)

    plan = plan_json.get("Plan", plan_json)
    traverse(plan)
    return max_cost


# ---------------------------------------------------------------------------
# Step 4: 权限检查（行级安全注入）
# ---------------------------------------------------------------------------

async def _check_and_inject_permissions(
    user_id: str,
    sql: str,
) -> tuple[str, bool, list[str]]:
    """使用 RBACEngine 检查表级权限并注入行级过滤条件。

    Args:
        user_id: 用户 ID。
        sql: 原始 SQL。

    Returns:
        (modified_sql, all_tables_allowed, denied_tables)
    """
    denied_tables: list[str] = []

    try:
        rbac = RBACEngine()

        # 提取 SQL 中引用的表名
        tables = SQLGuard.extract_tables(sql)
        if not tables:
            logger.info("[NL2SQL:RBAC] 未从 SQL 中提取到表名，跳过权限检查")
            return sql, True, []

        # 检查每个表的权限
        for table in tables:
            has_access = await rbac.check_table_access(user_id, table)
            if not has_access:
                denied_tables.append(table)
                logger.warning("[NL2SQL:RBAC] 用户 %s 无权访问表 %s", user_id, table)

        # 注入行级过滤条件
        try:
            modified_sql = await rbac.inject_row_conditions(user_id, sql)
        except Exception as e:
            logger.warning("[NL2SQL:RBAC] 行级条件注入失败，使用原始 SQL: %s", e)
            modified_sql = sql

        all_allowed = len(denied_tables) == 0

        logger.info("[NL2SQL:RBAC] 权限检查完成: allowed=%s, denied=%s",
                    all_allowed, denied_tables)
        return modified_sql, all_allowed, denied_tables

    except Exception as e:
        logger.warning("[NL2SQL:RBAC] 权限检查失败: %s", e)
        return sql, True, []


# ---------------------------------------------------------------------------
# Step 5: SQL 执行
# ---------------------------------------------------------------------------

async def _execute_sql(sql: str) -> dict:
    """使用 SQLExecutionTool 执行 SQL。

    Returns:
        {"success": bool, "columns": [...], "data": [[...]], "row_count": int, "execution_time_ms": int, "error": str}
    """
    try:
        tool = SQLExecutionTool(
            timeout=settings.sql_timeout_seconds,
            max_rows=settings.sql_max_rows,
        )
        result = await tool._arun(sql)
        if isinstance(result, dict):
            logger.info("[NL2SQL:Exec] SQL 执行完成: success=%s, rows=%d",
                        result.get("success"), result.get("row_count", 0))
            return result
        else:
            logger.warning("[NL2SQL:Exec] SQL 执行返回非字典结果: %s", type(result))
            return {
                "success": False,
                "columns": [],
                "data": [],
                "row_count": 0,
                "execution_time_ms": 0,
                "error": f"SQL 执行工具返回异常类型: {type(result).__name__}",
            }
    except Exception as e:
        logger.warning("[NL2SQL:Exec] SQL 执行异常: %s", e)
        return {
            "success": False,
            "columns": [],
            "data": [],
            "row_count": 0,
            "execution_time_ms": 0,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Step 6: 数据脱敏
# ---------------------------------------------------------------------------

async def _mask_data(columns: list[str], data: list[list]) -> dict:
    """使用 DataMaskingTool 对敏感字段进行脱敏。

    Returns:
        {"columns": [...], "data": [[...]], "masked_fields": [...], "row_count": int}
    """
    if not columns or not data:
        logger.info("[NL2SQL:Mask] 无数据需要脱敏")
        return {"columns": columns or [], "data": data or [], "masked_fields": [], "row_count": 0}

    try:
        tool = DataMaskingTool(sensitive_fields=settings.sensitive_fields)
        result = await tool._arun(
            columns=columns,
            data=data,
            sensitive_fields=settings.sensitive_fields,
        )
        if isinstance(result, dict):
            logger.info("[NL2SQL:Mask] 脱敏完成: masked_fields=%s",
                        result.get("masked_fields", []))
            return result
        else:
            logger.warning("[NL2SQL:Mask] 脱敏返回非字典结果")
            return {"columns": columns, "data": data, "masked_fields": [], "row_count": len(data)}
    except Exception as e:
        logger.warning("[NL2SQL:Mask] 脱敏失败: %s", e)
        return {"columns": columns, "data": data, "masked_fields": [], "row_count": len(data)}


# ---------------------------------------------------------------------------
# 主节点函数
# ---------------------------------------------------------------------------

async def nl2sql_pipeline_node(state: AgentState) -> dict:
    """完整的 NL2SQL 管线 - data_analysis 专用。

    依次执行 7 个步骤，将自然语言问题转化为 SQL、执行并脱敏。
    支持失败重试（通过 sql_retry_count 控制）。

    Args:
        state: 当前 AgentState.

    Returns:
        {
            "relevant_schemas": str,
            "generated_sql": str,
            "execution_result": dict,
            "masked_result": dict,
            "execution_error": str,
            "sql_retry_count": int,
            "requires_human_review": bool,
        }
    """
    query: str = state.get("query", "")
    user_id: str = state.get("user_id", "")
    existing_sql: str = state.get("generated_sql", "")
    existing_retry: int = state.get("sql_retry_count", 0)
    existing_schemas: str = state.get("relevant_schemas", "")

    logger.info("[NL2SQL] 启动管线, query=%s, user=%s, retry=%d",
                query[:80], user_id, existing_retry)

    # ---- Step 1: Schema 向量检索 ----
    # 如果是重试，复用已有的 schemas
    if existing_schemas and existing_retry > 0:
        schema_result = {"schemas": existing_schemas, "tables": []}
        logger.info("[NL2SQL] 重试模式，复用已有 schemas")
    else:
        schema_result = await _retrieve_schemas(query)
    relevant_schemas = schema_result["schemas"]

    # ---- Step 2: SQL 生成 ----
    # 如果是重试且已有 SQL，尝试修正而非全新生成
    if existing_sql and existing_retry > 0:
        # 重试时基于原 SQL 进行修正提示
        retry_query = (
            f"原始查询需求: {query}\n\n"
            f"上一次生成的 SQL 执行失败，请修正：\n```sql\n{existing_sql}\n```\n"
            f"请重新生成正确的 SQL 查询。"
        )
        generated_sql = await _generate_sql(retry_query, relevant_schemas)
    else:
        generated_sql = await _generate_sql(query, relevant_schemas)

    if not generated_sql:
        logger.error("[NL2SQL] SQL 生成失败，无可用 SQL")
        return {
            "relevant_schemas": relevant_schemas,
            "generated_sql": existing_sql or "",
            "execution_result": {
                "success": False,
                "columns": [],
                "data": [],
                "row_count": 0,
                "execution_time_ms": 0,
                "error": "SQL 生成失败：无法从 LLM 响应中提取有效 SQL",
            },
            "masked_result": {},
            "execution_error": "SQL 生成失败",
            "sql_retry_count": existing_retry + 1,
            "requires_human_review": True,
        }

    # ---- Step 3: EXPLAIN 预检 ----
    explain_result = await _explain_precheck(generated_sql)
    requires_human_review = False

    if explain_result["success"]:
        total_cost = explain_result["total_cost"]
        if total_cost > settings.sql_explain_cost_threshold:
            logger.warning(
                "[NL2SQL] EXPLAIN 总代价 %.2f > 阈值 %d，标记为需人工审核",
                total_cost, settings.sql_explain_cost_threshold,
            )
            requires_human_review = True
    else:
        logger.warning("[NL2SQL] EXPLAIN 预检失败: %s", explain_result.get("error"))
        # EXPLAIN 失败不阻断流程，但标记需要审核
        requires_human_review = True

    # ---- Step 4: 权限检查 ----
    modified_sql, all_tables_allowed, denied_tables = await _check_and_inject_permissions(
        user_id, generated_sql,
    )

    if not all_tables_allowed:
        requires_human_review = True

    sql_to_execute = modified_sql if modified_sql else generated_sql

    # ---- Step 5: SQL 执行 ----
    execution_result = await _execute_sql(sql_to_execute)

    # ---- Step 6: 数据脱敏 ----
    if execution_result.get("success") and execution_result.get("data"):
        masked_result = await _mask_data(
            execution_result.get("columns", []),
            execution_result.get("data", []),
        )
    else:
        masked_result = {}

    # ---- 处理执行状态和重试 ----
    execution_error = ""
    new_retry_count = existing_retry

    if not execution_result.get("success"):
        error_msg = execution_result.get("error", "未知执行错误")
        execution_error = error_msg
        new_retry_count = existing_retry + 1
        logger.warning("[NL2SQL] SQL 执行失败 (重试 %d): %s", new_retry_count, error_msg)

    logger.info("[NL2SQL] 管线完成: success=%s, retry=%d, human_review=%s",
                execution_result.get("success"), new_retry_count, requires_human_review)

    return {
        "relevant_schemas": relevant_schemas,
        "generated_sql": generated_sql,
        "execution_result": execution_result,
        "masked_result": masked_result,
        "execution_error": execution_error,
        "sql_retry_count": new_retry_count,
        "requires_human_review": requires_human_review,
    }
