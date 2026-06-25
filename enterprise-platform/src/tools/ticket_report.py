"""
工单/报告生成工具

功能：
1. 生成结构化JSON工单或报告
2. 存储到MinIO对象存储
3. MinIO不可用时回退到本地JSON文件
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.tools.base import BaseSecureTool

logger = logging.getLogger(__name__)


class TicketReportTool(BaseSecureTool):
    """工单/报告生成工具"""

    name: str = "ticket_report"
    description: str = (
        "生成结构化的工单或分析报告，存储到MinIO并返回访问URL。"
        "输入：报告类型和内容数据。输出：存储URL和报告摘要。"
    )

    _fallback_dir: str = "data/reports"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._fallback_dir = kwargs.get("fallback_dir", "data/reports")

    def _check_permission(self, resource: str = "") -> bool:
        return True

    async def _execute(
        self,
        report_type: str,
        title: str,
        data: dict,
        tags: list[str] | None = None,
        **kwargs,
    ) -> dict:
        """
        生成工单/报告

        Args:
            report_type: 类型 (ticket / analysis_report / summary)
            title: 报告标题
            data: 报告内容数据
            tags: 标签列表

        Returns:
            {"url": "访问URL", "report_id": "xxx", "title": "...", "storage": "minio|local"}
        """
        self._log_access("generate", report_type=report_type, title=title[:100])

        report_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        # 构建报告结构
        report = {
            "report_id": report_id,
            "type": report_type,
            "title": title,
            "created_at": timestamp,
            "created_by": self.user_id or "system",
            "tags": tags or [],
            "data": data,
        }

        # 1. 尝试MinIO存储
        try:
            url = await self._store_minio(report_id, report)
            self._log_access("stored_minio", report_id=report_id)
            return {
                "url": url,
                "report_id": report_id,
                "title": title,
                "type": report_type,
                "storage": "minio",
                "created_at": timestamp,
            }
        except Exception as e:
            logger.warning(f"MinIO存储失败，回退到本地: {e}")
            self._log_access("minio_fallback", error=str(e))

        # 2. 回退到本地文件
        try:
            url = await self._store_local(report_id, report)
            self._log_access("stored_local", report_id=report_id)
            return {
                "url": url,
                "report_id": report_id,
                "title": title,
                "type": report_type,
                "storage": "local",
                "created_at": timestamp,
            }
        except Exception as e:
            logger.error(f"本地存储也失败: {e}")
            return {
                "error": f"报告存储失败: {e}",
                "report_id": report_id,
                "storage": "none",
            }

    async def _store_minio(self, report_id: str, report: dict) -> str:
        """存储报告到MinIO"""
        from config.settings import settings

        try:
            from minio import Minio
        except ImportError:
            raise ImportError("minio-py 未安装，请执行: pip install minio")

        client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

        bucket = settings.minio_bucket

        # 确保bucket存在
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            logger.info(f"创建MinIO bucket: {bucket}")

        object_name = f"reports/{report_id}.json"
        data_bytes = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")

        client.put_object(
            bucket_name=bucket,
            object_name=object_name,
            data=data_bytes,
            length=len(data_bytes),
            content_type="application/json",
        )

        # 生成URL
        protocol = "https" if settings.minio_secure else "http"
        return f"{protocol}://{settings.minio_endpoint}/{bucket}/{object_name}"

    async def _store_local(self, report_id: str, report: dict) -> str:
        """存储报告到本地文件"""
        output_dir = Path(self._fallback_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        file_path = output_dir / f"{report_id}.json"
        file_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return f"file://{file_path.as_posix()}"

    async def retrieve_report(self, report_id: str) -> dict | None:
        """按ID检索报告（先查MinIO，再查本地）"""
        # 尝试MinIO
        try:
            from config.settings import settings
            from minio import Minio

            client = Minio(
                endpoint=settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )

            object_name = f"reports/{report_id}.json"
            response = client.get_object(settings.minio_bucket, object_name)
            data = json.loads(response.read().decode("utf-8"))
            response.close()
            response.release_conn()
            return data
        except Exception:
            pass

        # 回退本地
        file_path = Path(self._fallback_dir) / f"{report_id}.json"
        if file_path.exists():
            return json.loads(file_path.read_text(encoding="utf-8"))

        return None
