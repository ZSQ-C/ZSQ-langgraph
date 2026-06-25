"""
SQL执行工具

功能：
1. 仅在只读从库执行SQL（使用 read_only_session 上下文管理器）
2. 超时熔断保护（连接层面 statement_timeout + 应用层 asyncio.wait_for）
3. 返回行数限制
4. 异常安全连接释放
"""

import asyncio
import time
from typing import Any

from sqlalchemy import text

from config.settings import settings
from src.db.database import read_only_session
from src.tools.base import BaseSecureTool


class SQLExecutionTool(BaseSecureTool):
    """SQL执行工具（只读从库）"""

    name: str = "sql_execution"
    description: str = (
        "在只读数据库中安全执行SQL查询。输入：已验证的SQL语句。"
        "输出：查询结果数据和元信息。"
    )

    _timeout: int = 30
    _max_rows: int = 1000

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._timeout = kwargs.get("timeout", settings.sql_timeout_seconds)
        self._max_rows = kwargs.get("max_rows", 1000)

    def _check_permission(self, resource: str = "") -> bool:
        """SQL执行需要用户有查询权限（编排层合规审核节点提前完成）"""
        return True

    async def _execute(self, sql: str, **kwargs) -> dict[str, Any]:
        """
        执行SQL查询

        Args:
            sql: 已验证的SQL语句

        Returns:
            {
                "success": true/false,
                "columns": ["列名列表"],
                "data": [[行数据], ...],
                "row_count": 返回行数,
                "execution_time_ms": 执行耗时(毫秒),
                "error": "错误信息"
            }
        """
        self._log_access("execute", sql=sql[:200])

        start_time = time.time()

        try:
            # 使用上下文管理器，异常安全，自动释放连接
            async with read_only_session() as session:
                # 应用层超时控制（兜底，连接层已设置 statement_timeout）
                result = await asyncio.wait_for(
                    self._do_execute(session, sql),
                    timeout=self._timeout,
                )

                execution_time = int((time.time() - start_time) * 1000)
                result["execution_time_ms"] = execution_time

                self._log_access(
                    "execute_success",
                    row_count=result.get("row_count", 0),
                    time_ms=execution_time,
                )
                return result

        except asyncio.TimeoutError:
            execution_time = int((time.time() - start_time) * 1000)
            self._log_access("execute_timeout", time_ms=execution_time)
            return {
                "success": False,
                "columns": [],
                "data": [],
                "row_count": 0,
                "execution_time_ms": execution_time,
                "error": f"SQL执行超时（{self._timeout}秒），请优化查询条件",
            }

        except Exception as e:
            execution_time = int((time.time() - start_time) * 1000)
            self._log_access("execute_error", error=str(e))
            return {
                "success": False,
                "columns": [],
                "data": [],
                "row_count": 0,
                "execution_time_ms": execution_time,
                "error": f"SQL执行失败: {str(e)}",
            }

    async def _do_execute(self, session, sql: str) -> dict[str, Any]:
        """执行SQL并格式化结果"""
        result = await session.execute(text(sql))
        columns = list(result.keys()) if result.keys() else []
        rows = result.fetchmany(self._max_rows)
        data = [[str(v) if v is not None else None for v in row] for row in rows]

        return {
            "success": True,
            "columns": columns,
            "data": data,
            "row_count": len(data),
            "error": None,
        }