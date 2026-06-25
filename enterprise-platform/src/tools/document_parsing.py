"""
文档解析工具

功能：
1. 根据文件类型路由到不同解析器（PyMuPDF / PaddleOCR / Unstructured）
2. LayoutParser 布局分析
3. 文本分块 + BGE-M3 向量化

当前实现：可直接处理 text/markdown 文件的完整流水线。
PDF/图片等二进制格式的OCR处理通过Docker在Phase 5集成。
"""

import logging
import re
from pathlib import Path
from typing import Any

from src.tools.base import BaseSecureTool

logger = logging.getLogger(__name__)

# 支持的文件类型及对应解析策略
SUPPORTED_EXTENSIONS = {
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".pdf": "pdf",          # Phase 5: Docker PyMuPDF
    ".png": "image",        # Phase 5: Docker PaddleOCR
    ".jpg": "image",        # Phase 5: Docker PaddleOCR
    ".jpeg": "image",       # Phase 5: Docker PaddleOCR
    ".docx": "office",      # Phase 5: Docker Unstructured
    ".xlsx": "office",      # Phase 5: Docker Unstructured
}


class DocumentParsingTool(BaseSecureTool):
    """文档解析工具"""

    name: str = "document_parsing"
    description: str = (
        "解析上传的文档文件，提取文本内容并进行智能分块和向量化。"
        "输入：文件路径或文件内容。输出：分块结果及向量化状态。"
    )

    _chunk_size: int = 500
    _chunk_overlap: int = 50
    _vectorize: bool = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._chunk_size = kwargs.get("chunk_size", 500)
        self._chunk_overlap = kwargs.get("chunk_overlap", 50)
        self._vectorize = kwargs.get("vectorize", True)

    def _check_permission(self, resource: str = "") -> bool:
        return True

    async def _execute(
        self,
        file_path: str = "",
        content: str = "",
        file_type: str = "",
        metadata: dict | None = None,
        **kwargs,
    ) -> dict:
        """
        解析文档

        Args:
            file_path: 文件路径（与content二选一）
            content: 直接传入的文本内容（与file_path二选一）
            file_type: 文件类型提示（如 "text", "markdown", "pdf"）
            metadata: 附加元数据

        Returns:
            {"chunks": [...], "total_chunks": N, "vectorized": bool, "file_type": str}
        """
        self._log_access("parse", file_path=file_path[:100], file_type=file_type)

        # 1. 确定文件类型和获取文本内容
        if content:
            parsed_type, text = await self._parse_content(content, file_type)
        elif file_path:
            parsed_type, text = await self._parse_file(file_path, file_type)
        else:
            return {"error": "必须提供 file_path 或 content", "chunks": [], "total_chunks": 0}

        if not text or not text.strip():
            return {"error": "文档内容为空", "chunks": [], "total_chunks": 0, "file_type": parsed_type}

        # 2. 布局分析（markdown按标题分段）
        layout_blocks = self._analyze_layout(text, parsed_type)

        # 3. 文本分块
        chunks = self._chunk_text(text, parsed_type, layout_blocks)

        # 4. 向量化（调用BGE-M3 API）
        vectorized = False
        if self._vectorize:
            try:
                embeddings = await self._vectorize_chunks([c["text"] for c in chunks])
                for i, emb in enumerate(embeddings):
                    chunks[i]["embedding"] = emb
                vectorized = True
            except Exception as e:
                logger.warning(f"向量化失败: {e}")
                self._log_access("vectorize_failed", error=str(e))

        # 5. 附加元数据
        if metadata:
            for chunk in chunks:
                chunk.setdefault("metadata", {}).update(metadata)

        return {
            "chunks": chunks,
            "total_chunks": len(chunks),
            "vectorized": vectorized,
            "file_type": parsed_type,
        }

    async def _parse_file(self, file_path: str, file_type_hint: str) -> tuple[str, str]:
        """从文件路径解析文档"""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        ext = path.suffix.lower()
        parsed_type = file_type_hint or SUPPORTED_EXTENSIONS.get(ext, "unknown")

        if parsed_type in ("pdf", "image", "office"):
            return parsed_type, self._stub_binary_parse(file_path, parsed_type)

        # 文本类文件：直接读取
        try:
            text = path.read_text(encoding="utf-8")
            return parsed_type, text
        except UnicodeDecodeError:
            try:
                text = path.read_text(encoding="gbk")
                return parsed_type, text
            except Exception as e:
                raise ValueError(f"无法解码文件: {file_path}, 错误: {e}")

    async def _parse_content(self, content: str, file_type_hint: str) -> tuple[str, str]:
        """从内容字符串解析"""
        parsed_type = file_type_hint or "text"
        return parsed_type, content

    def _stub_binary_parse(self, file_path: str, file_type: str) -> str:
        """
        二进制文件解析占位（Phase 5 Docker集成）

        目前返回占位提示，实际OCR/解析将通过Docker容器调用：
        - PDF: PyMuPDF (fitz) 提取文本
        - 图片: PaddleOCR 识别
        - Office: Unstructured 库解析
        """
        return f"[Phase 5 待集成] {file_type.upper()} 文档: {Path(file_path).name}\n完整OCR解析将在Docker环境部署后启用。"

    def _analyze_layout(self, text: str, file_type: str) -> list[dict]:
        """
        布局分析

        对markdown文件按标题层次分段，对纯文本按段落分块。
        """
        blocks = []

        if file_type == "markdown":
            # 按markdown标题分割
            heading_pattern = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
            positions = [m for m in heading_pattern.finditer(text)]

            for i, match in enumerate(positions):
                level = len(match.group(1))
                heading = match.group(2).strip()
                start = match.end()
                end = positions[i + 1].start() if i + 1 < len(positions) else len(text)
                block_text = text[start:end].strip()
                if block_text:
                    blocks.append({
                        "type": "section",
                        "heading": heading,
                        "level": level,
                        "text": block_text,
                        "start_char": start,
                        "end_char": end,
                    })
        else:
            # 纯文本按双换行分段
            paragraphs = text.split("\n\n")
            pos = 0
            for para in paragraphs:
                para = para.strip()
                if para:
                    blocks.append({
                        "type": "paragraph",
                        "heading": "",
                        "level": 0,
                        "text": para,
                        "start_char": pos,
                        "end_char": pos + len(para),
                    })
                pos += len(para) + 2

        return blocks

    def _chunk_text(self, text: str, file_type: str, layout_blocks: list[dict]) -> list[dict]:
        """
        文本分块

        策略：
        - Markdown: 优先按章节分块，超长章节进一步切分
        - 纯文本: 固定大小滑动窗口
        """
        chunks = []

        if layout_blocks and file_type == "markdown":
            for block in layout_blocks:
                block_text = block["text"]
                if len(block_text) <= self._chunk_size:
                    chunks.append({
                        "text": block_text,
                        "structure_type": block["type"],
                        "parent_heading": block.get("heading", ""),
                        "metadata": {"heading_level": block.get("level", 0)},
                    })
                else:
                    # 大块进一步切分
                    sub_chunks = self._sliding_window_chunk(block_text)
                    for sc in sub_chunks:
                        chunks.append({
                            "text": sc,
                            "structure_type": block["type"],
                            "parent_heading": block.get("heading", ""),
                            "metadata": {"heading_level": block.get("level", 0)},
                        })
        else:
            # 纯文本滑动窗口分块
            text_chunks = self._sliding_window_chunk(text)
            for tc in text_chunks:
                chunks.append({
                    "text": tc,
                    "structure_type": "paragraph",
                    "parent_heading": "",
                    "metadata": {},
                })

        return chunks

    def _sliding_window_chunk(self, text: str) -> list[str]:
        """滑动窗口分块"""
        chunks = []
        chunk_size = self._chunk_size
        overlap = self._chunk_overlap

        if len(text) <= chunk_size:
            return [text]

        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunks.append(text[start:end])
            start += chunk_size - overlap

        return chunks

    async def _vectorize_chunks(self, texts: list[str]) -> list[list[float]]:
        """使用BGE-M3 API向量化文本块"""
        import httpx
        from config.settings import settings

        embeddings = []
        batch_size = 10

        async with httpx.AsyncClient() as client:
            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                resp = await client.post(
                    f"{settings.bge_api_base}/embeddings",
                    json={
                        "input": batch,
                        "model": settings.bge_model_name,
                    },
                    timeout=60.0,
                )
                resp.raise_for_status()
                data = resp.json()
                batch_embeddings = [item["embedding"] for item in data["data"]]
                embeddings.extend(batch_embeddings)

        return embeddings
