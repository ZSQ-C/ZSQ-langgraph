"""
SQL安全白名单校验器

基于sqlparse进行SQL语法解析，实现三层安全校验：
1. 语句类型：只允许SELECT/WITH
2. 禁止关键词：禁止DROP/DELETE/INSERT/UPDATE等写操作
3. 系统表保护：禁止访问pg_catalog、information_schema等
4. 强制LIMIT：确保查询不会返回过多数据
"""

import re
from dataclasses import dataclass
from typing import Optional

import sqlparse
from sqlparse.tokens import Keyword, DML, Name


@dataclass
class SQLValidationResult:
    """SQL校验结果"""
    valid: bool
    reason: str
    modified_sql: str | None = None


class SQLGuard:
    """SQL安全白名单校验器"""

    # 允许的语句类型
    ALLOWED_STATEMENTS = {"SELECT", "WITH"}

    # 禁止的关键词（写操作、管理操作）
    FORBIDDEN_KEYWORDS = {
        "DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "TRUNCATE",
        "CREATE", "EXEC", "EXECUTE", "GRANT", "REVOKE",
        "COPY", "VACUUM", "REINDEX", "CLUSTER",
    }

    # 禁止访问的系统表前缀
    FORBIDDEN_TABLE_PREFIXES = {
        "pg_catalog.",
        "information_schema.",
        "pg_",
        "sql_",
    }

    def __init__(self, max_rows: int = 1000):
        self.max_rows = max_rows

    def validate(self, sql: str) -> SQLValidationResult:
        """完整校验SQL安全性"""
        sql = sql.strip()

        if not sql:
            return SQLValidationResult(False, "SQL为空")

        # 1. 解析SQL
        try:
            parsed = sqlparse.parse(sql)
        except Exception as e:
            return SQLValidationResult(False, f"SQL解析失败: {str(e)}")

        if not parsed:
            return SQLValidationResult(False, "SQL解析为空")

        statement = parsed[0]

        # 2. 检查语句类型
        stmt_type = statement.get_type()
        if stmt_type not in self.ALLOWED_STATEMENTS:
            return SQLValidationResult(
                False,
                f"禁止的语句类型: {stmt_type}，仅允许 SELECT 和 WITH"
            )

        # 3. 检查禁止关键词
        sql_upper = sql.upper()
        for keyword in self.FORBIDDEN_KEYWORDS:
            # 使用正则匹配完整单词，避免误判（如"INSERT"不会匹配"INSERTED"）
            pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(pattern, sql_upper):
                return SQLValidationResult(
                    False,
                    f"SQL包含禁止的关键词: {keyword}"
                )

        # 4. 检查系统表访问
        for prefix in self.FORBIDDEN_TABLE_PREFIXES:
            if prefix.upper() in sql_upper:
                return SQLValidationResult(
                    False,
                    f"禁止访问系统表: {prefix}"
                )

        # 5. 检查LIMIT（强制要求）
        limit_check = self._ensure_limit(sql)
        if limit_check is None:
            return SQLValidationResult(
                False,
                "SQL缺少LIMIT子句，必须限制返回行数"
            )

        return SQLValidationResult(True, "OK", limit_check)

    def _ensure_limit(self, sql: str) -> str | None:
        """确保SQL包含LIMIT子句，没有则追加"""
        sql_upper = sql.upper()

        # 检查是否已有LIMIT
        if re.search(r'\bLIMIT\b', sql_upper):
            return sql

        # 没有LIMIT，在末尾追加
        sql = sql.rstrip(";").rstrip()
        return f"{sql} LIMIT {self.max_rows}"

    @staticmethod
    def is_select_only(sql: str) -> bool:
        """快速检查是否为SELECT语句"""
        sql_stripped = sql.strip().upper()
        return sql_stripped.startswith("SELECT") or sql_stripped.startswith("WITH")

    @staticmethod
    def extract_tables(sql: str) -> list[str]:
        """提取SQL中引用的表名"""
        try:
            parsed = sqlparse.parse(sql)[0]
            tables = []
            from_seen = False

            for token in parsed.flatten():
                if token.ttype is Keyword and token.value.upper() == "FROM":
                    from_seen = True
                    continue
                if from_seen and token.ttype is Name:
                    tables.append(token.value.strip('"'))
                    from_seen = False

            return list(set(tables))
        except Exception:
            return []

    @staticmethod
    def explain_check(explain_json: dict) -> dict:
        """EXPLAIN预检：解析查询计划JSON，检测危险操作"""
        errors, warnings = [], []

        def traverse(plan_node):
            node_type = plan_node.get("Node Type", "")
            plan_rows = plan_node.get("Plan Rows", 0)
            total_cost = plan_node.get("Total Cost", 0)
            if node_type == "Nested Loop" and "Join Filter" not in str(plan_node):
                errors.append(f"疑似笛卡尔积: Nested Loop at cost {total_cost}")
            if node_type == "Seq Scan" and plan_rows > 10000:
                rel = plan_node.get("Relation Name", "unknown")
                warnings.append(f"大表全表扫描: {rel} (预估{plan_rows}行)")
            if total_cost > 50000:
                warnings.append(f"查询计划总代价过高: {total_cost}")
            for child in plan_node.get("Plans", []):
                traverse(child)

        plan_list = explain_json if isinstance(explain_json, list) else [explain_json]
        for plan in plan_list:
            traverse(plan.get("Plan", plan))
        return {"safe": len(errors) == 0, "warnings": warnings, "errors": errors}