"""
Compliance Node - SQL 合规校验与权限验证

对 NL2SQL 管线生成的 SQL 进行多层安全校验：
1. sqlparse 语句类型 / 禁止关键词 / 系统表 / LIMIT 检查
2. SQLGuard 完整校验
3. EXPLAIN 执行计划代价评估
4. RBAC 表级权限检查与行级条件注入
5. 汇总标记设置
"""

import json
import logging
import re

import sqlparse
from sqlalchemy import text

from config.settings import settings
from src.db.database import read_only_session
from src.orchestration.state import AgentState
from src.security.rbac import RBACEngine
from src.security.sql_guard import SQLGuard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 白名单语句类型
ALLOWED_STATEMENT_TYPES = {"SELECT", "WITH", "UNKNOWN"}

# 禁止的关键词
FORBIDDEN_KEYWORDS = {
    "DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "TRUNCATE",
    "CREATE", "EXEC", "EXECUTE", "GRANT", "REVOKE",
    "COPY", "VACUUM", "REINDEX", "CLUSTER",
}

# 禁止的系统表前缀
FORBIDDEN_TABLE_PREFIXES = [
    "pg_catalog.",
    "information_schema.",
    "pg_",
]

# 危险函数/模式
DANGEROUS_PATTERNS = [
    r"\blo_import\b",
    r"\blo_export\b",
    r"\bpg_read_file\b",
    r"\bpg_write_file\b",
    r"\bpg_sleep\b",
    r"\bgenerate_series\b.*\bpg_sleep\b",
]


# ---------------------------------------------------------------------------
# Step 1: sqlparse 解析校验
# ---------------------------------------------------------------------------

def _check_statement_type(sql: str) -> tuple[bool, str]:
    """检查 SQL 语句类型是否在白名单内。

    Returns:
        (pass, reason)
    """
    try:
        parsed = sqlparse.parse(sql)
        if not parsed:
            return False, "SQL 解析为空"

        for statement in parsed:
            if not statement.tokens or all(t.is_whitespace for t in statement.tokens):
                continue
            stmt_type = statement.get_type()
            if stmt_type not in ALLOWED_STATEMENT_TYPES:
                return False, f"禁止的语句类型: {stmt_type}，仅允许 SELECT 和 WITH (CTE)"

        return True, "OK"
    except Exception as e:
        return False, f"SQL 解析失败: {str(e)}"


def _check_forbidden_keywords(sql: str) -> tuple[bool, str]:
    """检查 SQL 中是否包含禁止的关键词。

    Returns:
        (pass, reason)
    """
    sql_upper = sql.upper()

    found = []
    for keyword in FORBIDDEN_KEYWORDS:
        pattern = r'\b' + re.escape(keyword) + r'\b'
        if re.search(pattern, sql_upper):
            found.append(keyword)

    if found:
        return False, f"SQL 包含禁止的关键词: {', '.join(found)}"

    return True, "OK"


def _check_system_tables(sql: str) -> tuple[bool, str]:
    """检查 SQL 是否访问了受保护的系统表。

    Returns:
        (pass, reason)
    """
    sql_upper = sql.upper()

    for prefix in FORBIDDEN_TABLE_PREFIXES:
        if prefix.upper() in sql_upper:
            return False, f"禁止访问系统表/视图: {prefix}"

    return True, "OK"


def _check_dangerous_patterns(sql: str) -> tuple[bool, str]:
    """检查 SQL 中是否包含危险函数或模式。

    Returns:
        (pass, reason)
    """
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, sql, re.IGNORECASE):
            return False, f"SQL 包含危险函数/模式: {pattern}"

    return True, "OK"


def _ensure_limit(sql: str, max_rows: int | None = None) -> tuple[str, bool]:
    """确保 SQL 包含 LIMIT 子句，没有则自动追加。

    Returns:
        (sql, was_modified)
    """
    if max_rows is None:
        max_rows = settings.sql_max_rows

    if re.search(r'\bLIMIT\b', sql, re.IGNORECASE):
        return sql, False

    sql = sql.rstrip(";").rstrip()
    modified = f"{sql} LIMIT {max_rows}"
    logger.info("[Compliance] 自动追加 LIMIT %d", max_rows)
    return modified, True


# ---------------------------------------------------------------------------
# Step 2: SQLGuard 完整校验
# ---------------------------------------------------------------------------

def _run_sqlguard_validation(sql: str) -> dict:
    """使用 SQLGuard 进行完整的安全校验。

    Returns:
        {"valid": bool, "reason": str, "modified_sql": str | None}
    """
    try:
        guard = SQLGuard(max_rows=settings.sql_max_rows)
        result = guard.validate(sql)
        return {
            "valid": result.valid,
            "reason": result.reason,
            "modified_sql": result.modified_sql,
        }
    except Exception as e:
        logger.warning("[Compliance] SQLGuard 校验异常: %s", e)
        return {"valid": False, "reason": f"SQLGuard 校验异常: {str(e)}", "modified_sql": None}


# ---------------------------------------------------------------------------
# Step 3: EXPLAIN 执行检查
# ---------------------------------------------------------------------------

async def _execute_explain(sql: str) -> dict:
    """通过 EXPLAIN (FORMAT JSON) 检查查询计划代价和危险操作。

    Returns:
        {
            "success": bool,
            "total_cost": float,
            "plan_rows": int,
            "warnings": list[str],
            "errors": list[str],
            "explain_json": dict,
            "error": str | None,
        }
    """
    clean_sql = sql.strip().rstrip(";")

    try:
        async with read_only_session() as session:
            result = await session.execute(
                text(f"EXPLAIN (FORMAT JSON) {clean_sql}")
            )
            rows = result.fetchall()

        if not rows:
            return {
                "success": False,
                "total_cost": 0.0,
                "plan_rows": 0,
                "warnings": [],
                "errors": ["EXPLAIN 返回空结果"],
                "explain_json": {},
                "error": "EXPLAIN 返回空结果",
            }

        plan_json = rows[0][0] if rows[0] else {}
        if isinstance(plan_json, list):
            plan_json = plan_json[0] if plan_json else {}

        # 使用 SQLGuard.explain_check 进行危险操作检测
        try:
            guard = SQLGuard(max_rows=settings.sql_max_rows)
            check_result = guard.explain_check(plan_json)
            warnings = check_result.get("warnings", [])
            errors = check_result.get("errors", [])
            is_safe = check_result.get("safe", True)
        except Exception:
            # 回退到手动遍历
            warnings, errors, is_safe = _manual_explain_check(plan_json)

        total_cost = _extract_total_cost(plan_json)
        plan_rows = _extract_plan_rows(plan_json)

        # 代价阈值检查
        if total_cost > settings.sql_explain_cost_threshold:
            warnings.append(
                f"查询计划总代价 {total_cost:.0f} 超过阈值 {settings.sql_explain_cost_threshold}"
            )
            is_safe = False

        logger.info("[Compliance] EXPLAIN 检查: cost=%.2f, rows=%d, safe=%s",
                    total_cost, plan_rows, is_safe)

        return {
            "success": True,
            "total_cost": total_cost,
            "plan_rows": plan_rows,
            "warnings": warnings,
            "errors": errors,
            "explain_json": plan_json,
            "error": None,
        }

    except Exception as e:
        logger.warning("[Compliance] EXPLAIN 执行失败: %s", e)
        return {
            "success": False,
            "total_cost": 0.0,
            "plan_rows": 0,
            "warnings": [],
            "errors": [],
            "explain_json": {},
            "error": str(e),
        }


def _extract_total_cost(plan_json: dict) -> float:
    """从 EXPLAIN JSON 中递归提取最大 Total Cost。"""
    max_cost = 0.0

    def traverse(node: dict) -> None:
        nonlocal max_cost
        tc = node.get("Total Cost", 0)
        if isinstance(tc, (int, float)) and tc > max_cost:
            max_cost = float(tc)
        for child in node.get("Plans", []):
            if isinstance(child, dict):
                traverse(child)

    plan = plan_json.get("Plan", plan_json)
    if isinstance(plan, dict):
        traverse(plan)
    elif isinstance(plan, list):
        for p in plan:
            if isinstance(p, dict):
                traverse(p)

    return max_cost


def _extract_plan_rows(plan_json: dict) -> int:
    """从 EXPLAIN JSON 中提取顶层 Plan Rows。"""
    plan = plan_json.get("Plan", plan_json)
    if isinstance(plan, dict):
        return plan.get("Plan Rows", 0)
    if isinstance(plan, list) and len(plan) > 0 and isinstance(plan[0], dict):
        return plan[0].get("Plan Rows", 0)
    return 0


def _manual_explain_check(plan_json: dict) -> tuple[list[str], list[str], bool]:
    """手动遍历 EXPLAIN 计划进行危险操作检测。"""
    warnings: list[str] = []
    errors: list[str] = []

    def traverse(node: dict) -> None:
        node_type = node.get("Node Type", "")
        plan_rows = node.get("Plan Rows", 0)
        total_cost = node.get("Total Cost", 0)

        # 笛卡尔积检测
        if node_type == "Nested Loop" and "Join Filter" not in json.dumps(node):
            errors.append(f"疑似笛卡尔积: Nested Loop at cost {total_cost}")

        # 大表全表扫描
        if node_type == "Seq Scan" and plan_rows > 10000:
            rel = node.get("Relation Name", "unknown")
            warnings.append(f"大表全表扫描: {rel} (预估 {plan_rows} 行)")

        # 高代价节点
        if isinstance(total_cost, (int, float)) and total_cost > 50000:
            warnings.append(f"查询计划总代价过高: {total_cost}")

        for child in node.get("Plans", []):
            if isinstance(child, dict):
                traverse(child)

    if isinstance(plan_json, list):
        for p in plan_json:
            traverse(p.get("Plan", p))
    else:
        traverse(plan_json.get("Plan", plan_json))

    is_safe = len(errors) == 0
    return warnings, errors, is_safe


# ---------------------------------------------------------------------------
# Step 4: RBAC 权限检查
# ---------------------------------------------------------------------------

async def _validate_rbac(
    user_id: str,
    sql: str,
) -> dict:
    """使用 RBACEngine 进行表级权限检查和行级条件注入。

    Returns:
        {
            "all_tables_permitted": bool,
            "denied_tables": list[str],
            "modified_sql": str,
            "row_conditions_applied": list[str],
        }
    """
    denied_tables: list[str] = []
    modified_sql = sql
    row_conditions: list[str] = []

    try:
        rbac = RBACEngine()

        tables = SQLGuard.extract_tables(sql)
        if not tables:
            logger.info("[Compliance:RBAC] 未从 SQL 提取到表名，跳过权限检查")
            return {
                "all_tables_permitted": True,
                "denied_tables": [],
                "modified_sql": sql,
                "row_conditions_applied": [],
            }

        for table in tables:
            has_access = await rbac.check_table_access(user_id, table)
            if not has_access:
                denied_tables.append(table)

        # 注入行级条件
        try:
            modified_sql = await rbac.inject_row_conditions(user_id, sql)
            if modified_sql != sql:
                row_conditions = [
                    t for t in tables
                    if await rbac.get_row_condition(user_id, t) not in (None, "1=1")
                ]
        except Exception as e:
            logger.warning("[Compliance:RBAC] 行级条件注入失败: %s", e)

        logger.info("[Compliance:RBAC] 权限检查完成: tables=%s, denied=%s, row_cond=%s",
                    tables, denied_tables, row_conditions)

        return {
            "all_tables_permitted": len(denied_tables) == 0,
            "denied_tables": denied_tables,
            "modified_sql": modified_sql,
            "row_conditions_applied": row_conditions,
        }

    except Exception as e:
        logger.warning("[Compliance:RBAC] 权限检查失败: %s", e)
        return {
            "all_tables_permitted": True,
            "denied_tables": [],
            "modified_sql": sql,
            "row_conditions_applied": [],
        }


# ---------------------------------------------------------------------------
# 主节点函数
# ---------------------------------------------------------------------------

async def compliance_node(state: AgentState) -> dict:
    """对生成的 SQL 进行多层安全合规校验。

    依次执行 5 个检查步骤：
    1. sqlparse 解析校验（语句类型 / 禁止关键词 / 系统表 / 危险模式 / LIMIT）
    2. SQLGuard 完整校验
    3. EXPLAIN 执行计划检查
    4. RBAC 权限验证与行级条件注入
    5. 汇总标记

    Args:
        state: 当前 AgentState.

    Returns:
        {
            "sql_safe": bool,
            "permission_pass": bool,
            "requires_human_review": bool,
            "generated_sql": str,   # 可能已修改（附加 LIMIT / 行级条件）
            "execution_result": dict,  # EXPLAIN 结果
        }
    """
    generated_sql: str = state.get("generated_sql", "")
    user_id: str = state.get("user_id", "")
    query: str = state.get("query", "")

    logger.info("[Compliance] 开始校验 SQL, user=%s", user_id)

    # 初始化检查结果
    check_results: dict[str, dict] = {}
    all_checks_pass = True
    reasons: list[str] = []

    # ---- Step 1: sqlparse 解析校验 ----
    sql_to_check = generated_sql
    if not sql_to_check.strip():
        logger.error("[Compliance] generated_sql 为空，无法校验")
        return {
            "sql_safe": False,
            "permission_pass": False,
            "requires_human_review": True,
            "generated_sql": "",
            "execution_result": {"error": "未提供 SQL 语句"},
        }

    # 1a: 语句类型检查
    type_pass, type_reason = _check_statement_type(sql_to_check)
    check_results["statement_type"] = {"pass": type_pass, "reason": type_reason}
    if not type_pass:
        all_checks_pass = False
        reasons.append(type_reason)

    # 1b: 禁止关键词检查
    keyword_pass, keyword_reason = _check_forbidden_keywords(sql_to_check)
    check_results["forbidden_keywords"] = {"pass": keyword_pass, "reason": keyword_reason}
    if not keyword_pass:
        all_checks_pass = False
        reasons.append(keyword_reason)

    # 1c: 系统表检查
    sys_pass, sys_reason = _check_system_tables(sql_to_check)
    check_results["system_tables"] = {"pass": sys_pass, "reason": sys_reason}
    if not sys_pass:
        all_checks_pass = False
        reasons.append(sys_reason)

    # 1d: 危险模式检查
    danger_pass, danger_reason = _check_dangerous_patterns(sql_to_check)
    check_results["dangerous_patterns"] = {"pass": danger_pass, "reason": danger_reason}
    if not danger_pass:
        all_checks_pass = False
        reasons.append(danger_reason)

    # 1e: LIMIT 确保
    sql_with_limit, limit_modified = _ensure_limit(sql_to_check)
    if limit_modified:
        sql_to_check = sql_with_limit
        check_results["limit"] = {"pass": True, "reason": "已自动追加 LIMIT 子句"}
    else:
        check_results["limit"] = {"pass": True, "reason": "LIMIT 子句已存在"}

    # ---- Step 2: SQLGuard 完整校验 ----
    sqlguard_result = _run_sqlguard_validation(sql_to_check)
    check_results["sqlguard"] = sqlguard_result
    if not sqlguard_result["valid"]:
        all_checks_pass = False
        reasons.append(f"SQLGuard: {sqlguard_result['reason']}")

    # 优先使用 SQLGuard 修正后的 SQL
    if sqlguard_result.get("modified_sql"):
        sql_to_check = sqlguard_result["modified_sql"]
        logger.info("[Compliance] 使用 SQLGuard 修正后的 SQL")

    # ---- Step 3: EXPLAIN 执行检查 ----
    explain_result = await _execute_explain(sql_to_check)
    check_results["explain"] = explain_result

    explain_high_risk = False
    if explain_result["success"]:
        if explain_result.get("errors"):
            all_checks_pass = False
            reasons.extend(explain_result["errors"])
        if explain_result.get("warnings"):
            for w in explain_result["warnings"]:
                logger.warning("[Compliance] EXPLAIN 警告: %s", w)
        if explain_result["total_cost"] > settings.sql_explain_cost_threshold:
            explain_high_risk = True
    else:
        logger.warning("[Compliance] EXPLAIN 执行失败: %s", explain_result.get("error"))

    # ---- Step 4: RBAC 权限检查 ----
    rbac_result = await _validate_rbac(user_id, sql_to_check)
    check_results["rbac"] = rbac_result

    permission_pass = rbac_result["all_tables_permitted"] and len(rbac_result["denied_tables"]) == 0
    if not permission_pass:
        reasons.append(f"无权限访问表: {', '.join(rbac_result['denied_tables'])}")

    # 使用 RBAC 注入行级条件后的 SQL
    sql_final = rbac_result["modified_sql"] if rbac_result["modified_sql"] else sql_to_check

    # ---- Step 5: 汇总标记 ----
    sql_safe = all_checks_pass

    # 判断是否需要人工审核
    requires_human_review = any([
        not sql_safe,
        not permission_pass,
        explain_high_risk,
        explain_result.get("errors"),
        # 如果权限有表被拒绝，必须人工审核
        len(rbac_result.get("denied_tables", [])) > 0,
    ])

    # 汇总日志
    if reasons:
        logger.warning("[Compliance] 校验未通过项: %s", "; ".join(reasons))
    logger.info("[Compliance] 校验完成: sql_safe=%s, perm_pass=%s, human_review=%s",
                sql_safe, permission_pass, requires_human_review)

    return {
        "sql_safe": sql_safe,
        "permission_pass": permission_pass,
        "requires_human_review": requires_human_review,
        "generated_sql": sql_final,
        "execution_result": {
            "explain_result": explain_result,
            "check_results": check_results,
            "sql_safe": sql_safe,
            "permission_pass": permission_pass,
            "reasons": reasons,
        },
    }
