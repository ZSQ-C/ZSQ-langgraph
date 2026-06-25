"""
HTTP API 调用工具

功能：
1. 封装httpx异步HTTP客户端
2. URL白名单校验
3. 请求体大小限制（防止滥用）
4. 超时控制
5. 支持GET/POST/PUT/DELETE
"""

import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from src.tools.base import BaseSecureTool

logger = logging.getLogger(__name__)

# 默认允许的API域名白名单
DEFAULT_ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    # 企业内部系统
    "erp.internal.example.com",
    "crm.internal.example.com",
    "oa.internal.example.com",
    # BGE嵌入服务
    "localhost:8002",
    "127.0.0.1:8002",
]

DEFAULT_MAX_REQUEST_SIZE = 1 * 1024 * 1024  # 1MB


class HTTPAPITool(BaseSecureTool):
    """HTTP API调用工具"""

    name: str = "http_api"
    description: str = (
        "安全调用外部HTTP API。支持GET/POST/PUT/DELETE方法。"
        "输入：URL、方法、请求头、请求体。输出：响应状态码和内容。"
    )

    _allowed_hosts: list[str]
    _max_request_size: int
    _timeout: float

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._allowed_hosts = kwargs.get("allowed_hosts", DEFAULT_ALLOWED_HOSTS)
        self._max_request_size = kwargs.get("max_request_size", DEFAULT_MAX_REQUEST_SIZE)
        self._timeout = kwargs.get("timeout", 30.0)

    def _check_permission(self, resource: str = "") -> bool:
        """HTTP API调用需要权限"""
        if not self.user_id:
            return False
        return True

    async def _execute(
        self,
        url: str,
        method: str = "GET",
        headers: dict | None = None,
        body: dict | str | None = None,
        params: dict | None = None,
        **kwargs,
    ) -> dict:
        """
        调用外部HTTP API

        Args:
            url: 目标URL
            method: HTTP方法 (GET/POST/PUT/DELETE)
            headers: 请求头
            body: 请求体
            params: URL查询参数

        Returns:
            {
                "status_code": 200,
                "headers": {...},
                "body": ...,
                "elapsed_ms": 123,
                "error": null
            }
        """
        method = method.upper()
        self._log_access("call", url=url[:200], method=method)

        # 1. URL白名单校验
        valid, reason = self._validate_url(url)
        if not valid:
            self._log_access("blocked", reason=reason)
            return {
                "status_code": 403,
                "headers": {},
                "body": None,
                "elapsed_ms": 0,
                "error": f"URL被封禁: {reason}",
            }

        # 2. 请求体大小校验
        body_bytes = None
        if body is not None:
            if isinstance(body, dict):
                import json
                body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
            elif isinstance(body, str):
                body_bytes = body.encode("utf-8")
            elif isinstance(body, bytes):
                body_bytes = body
            else:
                body_bytes = str(body).encode("utf-8")

            if len(body_bytes) > self._max_request_size:
                return {
                    "status_code": 413,
                    "headers": {},
                    "body": None,
                    "elapsed_ms": 0,
                    "error": f"请求体过大: {len(body_bytes)} bytes (限制: {self._max_request_size})",
                }

        # 3. 发起请求
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                req_kwargs = {"headers": headers, "params": params}
                if body_bytes:
                    if isinstance(body, dict):
                        # 如果是字典，优先用json参数
                        req_kwargs["json"] = body
                    else:
                        req_kwargs["content"] = body_bytes

                import time
                start = time.time()

                resp = await client.request(method, url, **req_kwargs)

                elapsed_ms = int((time.time() - start) * 1000)

                # 解析响应体
                resp_body = None
                try:
                    resp_body = resp.json()
                except Exception:
                    resp_body = resp.text[:10000]  # 限制返回文本大小

                self._log_access(
                    "success",
                    status_code=resp.status_code,
                    elapsed_ms=elapsed_ms,
                )

                return {
                    "status_code": resp.status_code,
                    "headers": dict(resp.headers),
                    "body": resp_body,
                    "elapsed_ms": elapsed_ms,
                    "error": None,
                }

        except httpx.TimeoutException:
            self._log_access("timeout", url=url[:200])
            return {
                "status_code": 504,
                "headers": {},
                "body": None,
                "elapsed_ms": int(self._timeout * 1000),
                "error": f"请求超时 ({self._timeout}s)",
            }
        except httpx.ConnectError as e:
            self._log_access("connect_error", error=str(e)[:200])
            return {
                "status_code": 502,
                "headers": {},
                "body": None,
                "elapsed_ms": 0,
                "error": f"连接失败: {str(e)[:500]}",
            }
        except Exception as e:
            self._log_access("error", error=str(e)[:200])
            return {
                "status_code": 500,
                "headers": {},
                "body": None,
                "elapsed_ms": 0,
                "error": f"请求异常: {str(e)[:500]}",
            }

    def _validate_url(self, url: str) -> tuple[bool, str]:
        """
        URL白名单校验

        Returns:
            (是否通过, 原因)
        """
        try:
            parsed = urlparse(url)
        except Exception as e:
            return False, f"URL解析失败: {e}"

        # 只允许http/https
        if parsed.scheme not in ("http", "https"):
            return False, f"不支持的协议: {parsed.scheme}"

        # 必须有hostname
        if not parsed.hostname:
            return False, "URL缺少主机名"

        # 白名单校验
        hostname = parsed.hostname
        port = parsed.port
        host_with_port = f"{hostname}:{port}" if port else hostname

        # 检查hostname或hostname:port是否在白名单
        for allowed in self._allowed_hosts:
            if hostname == allowed or host_with_port == allowed:
                return True, "OK"
            # 支持通配符 *.example.com
            if allowed.startswith("*.") and hostname.endswith(allowed[1:]):
                return True, "OK"
            # 如果allowed不含端口，仅匹配hostname
            if ":" not in allowed and hostname == allowed:
                return True, "OK"
            # 如果allowed含端口，精确匹配 hostname:port
            if ":" in allowed and host_with_port == allowed:
                return True, "OK"

        return False, f"主机不在白名单: {hostname}"

    def add_allowed_host(self, host: str):
        """动态添加白名单主机"""
        if host not in self._allowed_hosts:
            self._allowed_hosts.append(host)
            logger.info(f"添加HTTP API白名单: {host}")

    def remove_allowed_host(self, host: str):
        """移除白名单主机"""
        if host in self._allowed_hosts:
            self._allowed_hosts.remove(host)
            logger.info(f"移除HTTP API白名单: {host}")

    def get_allowed_hosts(self) -> list[str]:
        """获取当前白名单"""
        return list(self._allowed_hosts)
