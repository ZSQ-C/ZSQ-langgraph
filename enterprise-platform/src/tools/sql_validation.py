"""
SQL安全校验工具

功能：
1. 基于sqlparse解析SQL语法树
2. 白名单校验：只允许SELECT/WITH
3. 禁止关键词检测
4. 系统表保护
5. 强制LIMIT
6. 注入行级权限条件
"""

from typing import Any

from src.security.sql_guard import SQLGuard, SQLValidationResult
from src.tools.base import BaseSecureTool


class SQLValidationTool(BaseSecureTool):
    """SQL安全校验工具"""

    name: str = "sql_validation"
    description: str = (
        "校验SQL语句的安全性，包括语句类型检查、禁止关键词检测、"
        "系统表保护、强制LIMIT等。输入：SQL语句字符串。"
        "输出：校验结果，包含是否通过、原因、修正后的SQL。"
    )

    _sql_guard: SQLGuard
    _max_rows: int = 1000

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._max_rows = kwargs.get("max_rows", 1000)
        self._sql_guard = SQLGuard(max_rows=self._max_rows)

    def _check_permission(self, resource: str = "") -> bool:
        """SQL校验工具本身不需要权限校验"""
        return True

    async def _execute(self, sql: str, **kwargs) -> dict[str, Any]:
        """
        校验SQL安全性

        Args:
            sql: 待校验的SQL语句

        Returns:
            {
                "valid": true/false,
                "reason": "校验原因",
                "modified_sql": "修正后的SQL（如有LIMIT追加）",
                "is_select_only": true/false
            }
        """
        self._log_access("validate", sql=sql[:200])

        # 快速检查
        is_select = SQLGuard.is_select_only(sql)

        # 完整校验
        result: SQLValidationResult = self._sql_guard.validate(sql)

        return {
            "valid": result.valid,
            "reason": result.reason,
            "modified_sql": result.modified_sql,
            "is_select_only": is_select,
        }

    def validate_sync(self, sql: str) -> SQLValidationResult:
        """同步校验接口（供编排层直接调用）"""
        return self._sql_guard.validate(sql)

    def get_guard(self) -> SQLGuard:
        """获取SQLGuard实例"""
        return self._sql_guard