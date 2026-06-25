"""
RAG文档检索工具 - pgvector混合搜索版

功能：
1. 向量语义检索 (pgvector cosine距离)
2. BM25关键词检索 (PostgreSQL ts_rank全文搜索)
3. RRF倒数排名融合 (Reciprocal Rank Fusion)
4. 权限过滤：根据用户doc_tags过滤结果
5. Critic溯源字段：page_number / structure_type / parent_heading
"""

import logging
from typing import Any

from sqlalchemy import text

from src.db.database import admin_session
from src.tools.base import BaseSecureTool

logger = logging.getLogger(__name__)


class RAGRetrievalTool(BaseSecureTool):
    """RAG文档检索工具 - pgvector混合搜索"""

    name: str = "rag_retrieval"
    description: str = (
        "在文档知识库中执行混合检索（向量语义 + BM25关键词），返回相关文档切片。"
        "输入：查询文本。输出：排序后的文档切片列表，含溯源信息。"
    )

    _top_k: int = 10
    _vector_weight: float = 0.5
    _bm25_weight: float = 0.5

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._top_k = kwargs.get("top_k", 10)
        self._vector_weight = kwargs.get("vector_weight", 0.5)
        self._bm25_weight = kwargs.get("bm25_weight", 0.5)

    def _check_permission(self, resource: str = "") -> bool:
        return True

    async def _execute(self, query: str, **kwargs) -> dict:
        """执行混合检索"""
        self._log_access("retrieve", query=query[:200], top_k=self._top_k)

        top_k = kwargs.get("top_k", self._top_k)

        try:
            # 1. 生成查询向量
            query_embedding = await self._get_query_embedding(query)
        except Exception as e:
            logger.warning(f"向量生成失败，回退到纯BM25检索: {e}")
            return await self._bm25_only_search(query, top_k)

        try:
            # 2. 混合检索 + RRF融合
            chunks = await self._hybrid_search(query, query_embedding, top_k)
        except Exception as e:
            logger.warning(f"混合检索失败，回退到纯向量检索: {e}")
            chunks = await self._vector_only_search(query_embedding, top_k)

        # 3. 权限过滤
        if self.user_id:
            try:
                from src.security.rbac import RBACEngine
                rbac = RBACEngine()
                chunks = await rbac.filter_allowed_documents(self.user_id, chunks)
            except Exception as e:
                logger.warning(f"权限过滤跳过: {e}")

        # 4. 格式化结果
        return self._format_chunks(chunks)

    async def _get_query_embedding(self, query: str) -> list[float]:
        """调用BGE-M3 API生成查询向量"""
        import httpx
        from config.settings import settings

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.bge_api_base}/embeddings",
                json={"input": query, "model": settings.bge_model_name},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]

    async def _hybrid_search(
        self, query: str, query_embedding: list[float], top_k: int
    ) -> list[dict]:
        """
        混合检索：向量 + BM25，使用RRF融合

        策略：
        - 向量检索：取 top_k * 3 个候选
        - BM25检索：取 top_k * 3 个候选
        - RRF融合：score = sum(1 / (k + rank_i))  for each retriever
        - 最终取 top_k 个结果
        """
        # 向量检索
        vector_chunks = await self._vector_only_search(query_embedding, top_k * 3)

        # BM25关键词检索
        bm25_chunks = await self._bm25_only_search(query, top_k * 3)

        # RRF融合
        k_rrf = 60  # RRF平滑常数

        chunk_scores: dict[str, float] = {}
        chunk_data: dict[str, dict] = {}

        # 向量排名
        for rank, chunk in enumerate(vector_chunks):
            cid = str(chunk.get("chunk_id", chunk.get("id", id(chunk))))
            chunk_scores[cid] = chunk_scores.get(cid, 0) + (1.0 / (k_rrf + rank + 1))
            chunk_data[cid] = chunk

        # BM25排名
        for rank, chunk in enumerate(bm25_chunks):
            cid = str(chunk.get("chunk_id", chunk.get("id", id(chunk))))
            chunk_scores[cid] = chunk_scores.get(cid, 0) + (1.0 / (k_rrf + rank + 1))
            if cid not in chunk_data:
                chunk_data[cid] = chunk

        # 按融合分数排序
        sorted_ids = sorted(chunk_scores.keys(), key=lambda cid: chunk_scores[cid], reverse=True)

        fused_chunks = []
        for cid in sorted_ids[:top_k]:
            chunk = chunk_data[cid]
            chunk["_rrf_score"] = round(chunk_scores[cid], 6)
            fused_chunks.append(chunk)

        return fused_chunks

    async def _vector_only_search(
        self, query_embedding: list[float], top_k: int
    ) -> list[dict]:
        """纯向量语义检索"""
        async with admin_session() as session:
            result = await session.execute(
                text("""
                    SELECT
                        id, document_id, chunk_index, content,
                        page_number, structure_type, parent_heading,
                        metadata,
                        1 - (embedding <=> :embedding) AS similarity
                    FROM document_chunks
                    WHERE is_deleted = false AND embedding IS NOT NULL
                    ORDER BY embedding <=> :embedding
                    LIMIT :limit
                """),
                {"embedding": query_embedding, "limit": top_k},
            )
            rows = result.fetchall()

        return [self._row_to_chunk(row) for row in rows]

    async def _bm25_only_search(self, query: str, top_k: int) -> list[dict]:
        """
        BM25关键词检索（PostgreSQL内置全文搜索 ts_rank）

        使用 plainto_tsquery 将查询文本转为tsquery，
        用 ts_rank 计算BM25相关度排名。
        """
        async with admin_session() as session:
            try:
                result = await session.execute(
                    text("""
                        SELECT
                            id, document_id, chunk_index, content,
                            page_number, structure_type, parent_heading,
                            metadata,
                            ts_rank(
                                to_tsvector('simple', content),
                                plainto_tsquery('simple', :query)
                            ) AS similarity
                        FROM document_chunks
                        WHERE is_deleted = false
                          AND to_tsvector('simple', content) @@ plainto_tsquery('simple', :query)
                        ORDER BY similarity DESC
                        LIMIT :limit
                    """),
                    {"query": query, "limit": top_k},
                )
                rows = result.fetchall()
            except Exception:
                # 全文搜索失败时退回到ILIKE
                result = await session.execute(
                    text("""
                        SELECT
                            id, document_id, chunk_index, content,
                            page_number, structure_type, parent_heading,
                            metadata,
                            0.1 AS similarity
                        FROM document_chunks
                        WHERE is_deleted = false
                          AND content ILIKE '%' || :query || '%'
                        LIMIT :limit
                    """),
                    {"query": query, "limit": top_k},
                )
                rows = result.fetchall()

        return [self._row_to_chunk(row) for row in rows]

    def _row_to_chunk(self, row) -> dict:
        """将数据库行转为chunk字典"""
        metadata = {}
        if hasattr(row, 'metadata') and row.metadata is not None:
            metadata = row.metadata if isinstance(row.metadata, dict) else {}
        elif hasattr(row, 'metadata_') and row.metadata_ is not None:
            metadata = row.metadata_ if isinstance(row.metadata_, dict) else {}

        return {
            "chunk_id": str(row.id),
            "document_id": str(row.document_id) if row.document_id else None,
            "chunk_index": row.chunk_index,
            "content": row.content,
            "page_number": getattr(row, 'page_number', None),
            "structure_type": getattr(row, 'structure_type', None),
            "parent_heading": getattr(row, 'parent_heading', None),
            "metadata": metadata,
            "similarity": float(getattr(row, 'similarity', 0)),
        }

    def _format_chunks(self, chunks: list[dict]) -> dict:
        """格式化检索结果为LLM友好格式"""
        formatted = []
        for i, chunk in enumerate(chunks):
            source_info = []
            if chunk.get("page_number"):
                source_info.append(f"页码:{chunk['page_number']}")
            if chunk.get("structure_type"):
                source_info.append(f"类型:{chunk['structure_type']}")
            if chunk.get("parent_heading"):
                source_info.append(f"章节:{chunk['parent_heading']}")

            formatted.append({
                "index": i,
                "chunk_id": chunk.get("chunk_id"),
                "document_id": chunk.get("document_id"),
                "content": chunk.get("content", ""),
                "source": " | ".join(source_info) if source_info else "未知来源",
                "source_fields": {
                    "page_number": chunk.get("page_number"),
                    "structure_type": chunk.get("structure_type"),
                    "parent_heading": chunk.get("parent_heading"),
                },
                "similarity": chunk.get("similarity", 0),
                "_rrf_score": chunk.get("_rrf_score"),
            })

        return {
            "chunks": formatted,
            "total": len(formatted),
            "query_type": "hybrid_rrf",
        }
