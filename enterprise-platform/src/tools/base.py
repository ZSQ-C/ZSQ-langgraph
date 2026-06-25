"""
安全工具基类

所有工具继承此基类，内置：
- 权限校验：每个工具调用前自动校验用户权限
- 日志埋点：记录工具调用时间、参数、结果
- 异步执行：统一使用 _arun 异步接口
"""

import time
import logging
from typing import Any

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


class BaseSecureTool(BaseTool):
    """
    安全工具基类

    子类需要实现：
    - name: 工具名称
    - description: 工具描述
    - _execute(): 实际执行逻辑
    """

    # 用户信息（运行时注入）
    user_id: str = ""
    user_role: str = ""
    user_dept: str = ""

    def _check_permission(self, resource: str = "") -> bool:
        """
        权限校验钩子，子类可重写

        Args:
            resource: 需要校验的资源标识

        Returns:
            是否有权限
        """
        # 基类默认通过，子类根据实际场景重写
        return True

    def _log_access(self, action: str, detail: str = "", **kwargs):
        """记录工具调用日志"""
        logger.info(
            f"[Tool:{self.name}] "
            f"user={self.user_id} "
            f"role={self.user_role} "
            f"action={action} "
            f"detail={detail} "
            f"{' '.join(f'{k}={v}' for k, v in kwargs.items())}"
        )

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        """异步执行入口，包含权限校验和日志"""
        start_time = time.time()

        self._log_access("start", args=str(args)[:200], **kwargs)

        try:
            # 权限校验
            if not self._check_permission():
                self._log_access("denied", "权限不足")
                return {"error": "权限不足", "detail": "您没有执行此操作的权限"}

            # 执行实际逻辑
            result = await self._execute(*args, **kwargs)

            elapsed = time.time() - start_time
            self._log_access("success", f"耗时{elapsed:.2f}s")

            return result

        except Exception as e:
            elapsed = time.time() - start_time
            self._log_access("error", str(e), elapsed=f"{elapsed:.2f}s")
            logger.exception(f"[Tool:{self.name}] 执行异常")
            return {"error": str(e), "detail": f"工具执行异常: {type(e).__name__}"}

    async def _execute(self, *args: Any, **kwargs: Any) -> Any:
        """
        实际执行逻辑，子类必须重写

        Raises:
            NotImplementedError: 子类未实现
        """
        raise NotImplementedError("子类必须实现 _execute 方法")

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """同步入口（不推荐使用，保留兼容）"""
        raise NotImplementedError("请使用异步接口 _arun")