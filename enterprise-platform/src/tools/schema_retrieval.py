"""Schema语义检索工具 - v3.0 pgvector版"""
from typing import Any
from src.tools.base import BaseSecureTool


class SchemaRetrievalTool(BaseSecureTool):
    name: str = "schema_retrieval"
    description: str = "根据用户自然语言查询，语义检索相关的数据库表结构。输入：查询文本。输出：相关表的DDL结构。"
    _top_k: int = 5

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._top_k = kwargs.get("top_k", 5)

    async def _execute(self, query: str, **kwargs) -> dict:
        """使用pgvector语义检索表结构"""
        self._log_access("retrieve", query=query[:100])
        from sqlalchemy import text
        from src.db.database import admin_session
        from src.llm.factory import get_heavy_llm
        # 1. 生成查询向量(使用BGE-M3 API)
        import httpx
        try:
            from config.settings import settings
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{settings.bge_api_base}/embeddings",
                    json={"input": query, "model": settings.bge_model_name},
                    timeout=30.0
                )
                query_embedding = resp.json()["data"][0]["embedding"]
        except Exception:
            return await self._fallback_search(query)
        # 2. pgvector检索
        async with admin_session() as session:
            result = await session.execute(
                text("""
                    SELECT table_name, column_name, data_type, description, is_sensitive,
                           1 - (embedding <=> :embedding) AS similarity
                    FROM schema_metadata
                    WHERE is_deleted = false
                    ORDER BY embedding <=> :embedding
                    LIMIT :limit
                """),
                {"embedding": query_embedding, "limit": self._top_k * 10}
            )
            rows = result.fetchall()
        tables_info = {}
        for row in rows:
            tn = row.table_name
            if tn not in tables_info:
                tables_info[tn] = []
            tables_info[tn].append({
                "column_name": row.column_name,
                "data_type": row.data_type,
                "description": row.description,
                "is_sensitive": row.is_sensitive,
            })
        return self._format_result(tables_info)

    async def _fallback_search(self, query: str) -> dict:
        """回退：ILIKE关键词搜索"""
        from sqlalchemy import text
        from src.db.database import admin_session
        async with admin_session() as session:
            keywords = query.lower().split()[:3]
            conditions = " OR ".join([
                f"(description ILIKE '%{kw}%' OR table_name ILIKE '%{kw}%' OR column_name ILIKE '%{kw}%')"
                for kw in keywords
            ]) or "1=1"
            result = await session.execute(
                text(f"SELECT * FROM schema_metadata WHERE {conditions} AND is_deleted = false LIMIT {self._top_k * 10}")
            )
            rows = result.fetchall()
        tables_info = {}
        for row in rows:
            tn = row.table_name
            if tn not in tables_info:
                tables_info[tn] = []
            tables_info[tn].append({"column_name": row.column_name, "data_type": row.data_type,
                                     "description": row.description, "is_sensitive": row.is_sensitive})
        return self._format_result(tables_info)

    def _format_result(self, tables_info: dict) -> dict:
        schema_parts, table_names = [], []
        for table_name, columns in tables_info.items():
            table_names.append(table_name)
            lines = [f"表名: {table_name}", "字段:"]
            for col in columns:
                sensitive_mark = " [敏感]" if col.get("is_sensitive") else ""
                lines.append(f"  - {col['column_name']} ({col.get('data_type','VARCHAR')}): {col.get('description','')}{sensitive_mark}")
            schema_parts.append("\n".join(lines))
        return {"schemas": "\n\n".join(schema_parts), "tables": table_names}
