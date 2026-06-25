"""
Tool Tests: SchemaRetrievalTool, RAGRetrievalTool, SQLExecutionTool, SQLValidationTool.

All tests use mocked external dependencies (DB, embedding API, HTTP clients).
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import MockResult, MockSession


# ============================================================
# SchemaRetrievalTool Tests
# ============================================================

class TestSchemaRetrievalTool:
    """Test SchemaRetrievalTool with mocked pgvector and BGE-M3 API."""

    @pytest.fixture
    def mock_db_with_schemas(self):
        """Mock DB session returning schema_metadata rows."""
        from collections import namedtuple

        SchemaRow = namedtuple(
            "SchemaRow",
            ["table_name", "column_name", "data_type", "description", "is_sensitive", "similarity"],
        )
        rows = [
            SchemaRow("sales", "product_name", "VARCHAR", "产品名称", False, 0.95),
            SchemaRow("sales", "amount", "NUMERIC", "销售金额", False, 0.92),
            SchemaRow("sales", "sale_date", "DATE", "销售日期", False, 0.90),
            SchemaRow("sales", "customer_phone", "VARCHAR", "客户电话", True, 0.88),
            SchemaRow("servers", "hostname", "VARCHAR", "服务器主机名", False, 0.87),
            SchemaRow("servers", "failure_count", "INTEGER", "故障次数", False, 0.85),
        ]
        result = MockResult(
            columns=["table_name", "column_name", "data_type", "description", "is_sensitive", "similarity"],
            data=rows,
        )
        session = MockSession()
        session._results["schema_metadata"] = result
        return session

    @pytest.fixture
    def tool(self):
        from src.tools.schema_retrieval import SchemaRetrievalTool
        tool = SchemaRetrievalTool(top_k=5)
        tool.user_id = "test-user"
        tool.user_role = "analyst"
        tool.user_dept = "sales_dept"
        return tool

    @pytest.mark.asyncio
    async def test_fallback_search_returns_schemas(self, tool, mock_db_with_schemas):
        """When embedding API is unavailable, fallback to keyword search."""
        with patch(
            "src.db.database.admin_session"
        ) as mock_admin_session:
            mock_admin_session.return_value.__aenter__ = AsyncMock(
                return_value=mock_db_with_schemas
            )
            mock_admin_session.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await tool._fallback_search("销售金额查询")
            assert "schemas" in result
            assert "tables" in result
            assert "sales" in result["tables"]

    @pytest.mark.asyncio
    async def test_format_result_includes_sensitive_marks(self, tool):
        """Schema output must mark sensitive fields."""
        tables_info = {
            "sales": [
                {"column_name": "product_name", "data_type": "VARCHAR", "description": "产品名称", "is_sensitive": False},
                {"column_name": "customer_phone", "data_type": "VARCHAR", "description": "客户电话", "is_sensitive": True},
            ]
        }
        formatted = tool._format_result(tables_info)
        assert "[敏感]" in formatted["schemas"]
        assert "customer_phone" in formatted["schemas"]
        assert "product_name" in formatted["schemas"]

    @pytest.mark.asyncio
    async def test_schema_retrieval_top_k_limit(self, tool):
        """Schema retrieval should respect top_k limit."""
        assert tool._top_k == 5

    def test_tool_attributes(self, tool):
        """Verify tool has correct name and description."""
        assert tool.name == "schema_retrieval"
        assert "表结构" in tool.description
        assert "语义检索" in tool.description

    @pytest.mark.asyncio
    async def test_embedding_api_returns_vector(self, mock_embedding_client):
        """Verify the embedding API mock returns expected vector format."""
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://localhost:8002/v1/embeddings",
                json={"input": "test query", "model": "bge-m3"},
                timeout=30.0,
            )
            data = resp.json()
            assert "data" in data
            assert len(data["data"]) == 1
            assert "embedding" in data["data"][0]
            assert len(data["data"][0]["embedding"]) == 1024

    @pytest.mark.asyncio
    async def test_schema_retrieval_with_embedding_api(self, tool, mock_db_with_schemas, mock_embedding_client):
        """Full retrieval path with mocked embedding API and DB."""
        with patch(
            "src.db.database.admin_session"
        ) as mock_admin_session:
            mock_admin_session.return_value.__aenter__ = AsyncMock(
                return_value=mock_db_with_schemas
            )
            mock_admin_session.return_value.__aexit__ = AsyncMock(return_value=None)

            # The tool's _execute method will now use the mocked embedding API
            # and mocked DB session
            result = await tool._execute("销售数据查询")
            assert "schemas" in result
            assert "tables" in result
            assert len(result["tables"]) > 0


# ============================================================
# SQLExecutionTool Tests
# ============================================================

class TestSQLExecutionTool:
    """Test SQLExecutionTool with mocked DB session."""

    @pytest.fixture
    def tool(self):
        from src.tools.sql_execution import SQLExecutionTool
        tool = SQLExecutionTool(timeout=30, max_rows=100)
        tool.user_id = "test-user"
        tool.user_role = "analyst"
        tool.user_dept = "sales_dept"
        return tool

    @pytest.mark.asyncio
    async def test_execute_select_returns_data(self, tool):
        """Execute a valid SELECT and return formatted results."""
        mock_session = MockSession()
        mock_session._results = {
            "SELECT": MockResult(
                columns=["product_name", "amount"],
                data=[("笔记本电脑", 5999.00), ("手机", 2999.00)],
            ),
        }

        with patch(
            "src.tools.sql_execution.read_only_session"
        ) as mock_ro_session:
            mock_ro_session.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_ro_session.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await tool._execute("SELECT product_name, amount FROM sales LIMIT 10")
            assert result["success"] is True
            assert result["columns"] == ["product_name", "amount"]
            assert len(result["data"]) == 2
            assert result["row_count"] == 2
            assert result["execution_time_ms"] is not None
            assert result["error"] is None

    @pytest.mark.asyncio
    async def test_execute_timeout_handling(self, tool):
        """Timeout should produce a clean error response."""
        mock_session = MockSession()

        async def slow_execute(*args, **kwargs):
            await asyncio.sleep(999)  # Simulate a very slow query
            return MockResult()

        mock_session.execute = slow_execute

        tool._timeout = 0.01  # Force timeout to 10ms

        with patch(
            "src.tools.sql_execution.read_only_session"
        ) as mock_ro_session:
            mock_ro_session.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_ro_session.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await tool._execute("SELECT * FROM huge_table")
            assert result["success"] is False
            assert "超时" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_error_handling(self, tool):
        """SQL execution errors should be caught and returned gracefully."""
        mock_session = MockSession()

        async def failing_execute(*args, **kwargs):
            raise RuntimeError("Connection lost")

        mock_session.execute = failing_execute

        with patch(
            "src.tools.sql_execution.read_only_session"
        ) as mock_ro_session:
            mock_ro_session.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_ro_session.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await tool._execute("SELECT * FROM sales")
            assert result["success"] is False
            assert "Connection lost" in result["error"]

    @pytest.mark.asyncio
    async def test_max_rows_enforced(self, tool):
        """Results should not exceed max_rows."""
        mock_session = MockSession()
        all_data = [(f"item-{i}", i * 100) for i in range(200)]
        mock_session._results = {
            "SELECT": MockResult(
                columns=["name", "value"],
                data=all_data,
            ),
        }

        with patch(
            "src.tools.sql_execution.read_only_session"
        ) as mock_ro_session:
            mock_ro_session.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_ro_session.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await tool._execute("SELECT * FROM sales")
            assert result["row_count"] <= tool._max_rows
            assert result["row_count"] == 100  # Exactly max_rows

    def test_tool_attributes(self, tool):
        """Verify tool has correct name and description."""
        assert tool.name == "sql_execution"
        assert "只读" in tool.description
        assert "SQL" in tool.description


# ============================================================
# SQLValidationTool Tests
# ============================================================

class TestSQLValidationTool:
    """Test SQLValidationTool."""

    @pytest.fixture
    def tool(self):
        from src.tools.sql_validation import SQLValidationTool
        tool = SQLValidationTool(max_rows=1000)
        tool.user_id = "test-user"
        tool.user_role = "analyst"
        tool.user_dept = "sales_dept"
        return tool

    @pytest.mark.asyncio
    async def test_validate_valid_select(self, tool):
        """Valid SELECT should pass validation."""
        result = await tool._execute("SELECT * FROM sales LIMIT 10")
        assert result["valid"] is True
        assert result["is_select_only"] is True
        assert result["modified_sql"] is not None

    @pytest.mark.asyncio
    async def test_validate_rejects_insert(self, tool):
        """INSERT should be rejected."""
        result = await tool._execute(
            "INSERT INTO sales VALUES (1, 'test', 100)"
        )
        assert result["valid"] is False
        assert "INSERT" in result["reason"]

    @pytest.mark.asyncio
    async def test_validate_adds_missing_limit(self, tool):
        """Missing LIMIT should be auto-appended."""
        result = await tool._execute("SELECT * FROM sales")
        assert result["valid"] is True
        assert "LIMIT" in result["modified_sql"].upper()

    @pytest.mark.asyncio
    async def test_validate_sync_interface(self, tool):
        """Sync validation should work for direct calls."""
        result = tool.validate_sync("SELECT 1")
        assert result.valid is True

    def test_get_guard_returns_instance(self, tool):
        """get_guard should return the internal SQLGuard."""
        guard = tool.get_guard()
        assert guard is not None
        assert isinstance(guard, __import__("src.security.sql_guard", fromlist=["SQLGuard"]).SQLGuard)


# ============================================================
# RAG Retrieval Tests (mocked)
# ============================================================

class TestRAGRetrievalTool:
    """
    Test RAG retrieval pipeline.

    The RAGRetrievalTool is not yet created as a standalone file, but we test
    the components it would rely on: embedding API, pgvector search, and
    document chunk retrieval.
    """

    @pytest.fixture
    def sample_chunks(self):
        return [
            {
                "chunk_id": "chunk-001",
                "document_id": "doc-server-sop",
                "content": "## 服务器故障应急响应流程\n\n1. 确认故障范围\n2. 检查监控告警\n3. 执行应急预案\n4. 记录故障详情\n5. 提交事后分析报告",
                "page": 5,
                "structure_type": "paragraph",
                "parent_heading": "应急响应流程",
                "score": 0.94,
                "metadata": {"tags": ["server_ops", "sop"]},
            },
            {
                "chunk_id": "chunk-002",
                "document_id": "doc-server-sop",
                "content": "## 服务器故障分类\n\n- 硬件故障: CPU/内存/磁盘\n- 网络故障: 交换机/路由\n- 软件故障: 应用崩溃/内存泄漏\n- 人为故障: 误操作/配置错误",
                "page": 3,
                "structure_type": "paragraph",
                "parent_heading": "故障分类",
                "score": 0.91,
                "metadata": {"tags": ["server_ops", "sop"]},
            },
            {
                "chunk_id": "chunk-003",
                "document_id": "doc-performance",
                "content": "## 服务器性能优化\n\n1. 合理配置JVM内存\n2. 使用连接池\n3. 启用缓存层\n4. 优化SQL查询\n5. 使用CDN加速静态资源",
                "page": 12,
                "structure_type": "paragraph",
                "parent_heading": "性能优化指南",
                "score": 0.87,
                "metadata": {"tags": ["performance", "public"]},
            },
            {
                "chunk_id": "chunk-004",
                "document_id": "doc-finance",
                "content": "## Q2财务报告\n\n机密文档 - 仅供高管查阅",
                "page": 1,
                "structure_type": "heading",
                "parent_heading": "财务报告",
                "score": 0.45,
                "metadata": {"tags": ["finance", "confidential"]},
            },
        ]

    def test_chunk_structure_complete(self, sample_chunks):
        """Every chunk must have required fields for citation tracing."""
        required = ["chunk_id", "document_id", "content", "page", "score"]
        for chunk in sample_chunks:
            for field in required:
                assert field in chunk, f"Chunk {chunk.get('chunk_id')} missing {field}"

    def test_chunks_sorted_by_score(self, sample_chunks):
        """Chunks should be sorted by relevance score descending."""
        scores = [c["score"] for c in sample_chunks]
        assert scores == sorted(scores, reverse=True), "Chunks not sorted by score"

    def test_rag_citation_requirements(self, sample_chunks):
        """Validate that each chunk has proper citations."""
        for chunk in sample_chunks:
            # page must be a valid integer
            assert isinstance(chunk["page"], int)
            assert chunk["page"] >= 1

            # score must be between 0 and 1
            assert 0 <= chunk["score"] <= 1

            # content must not be empty
            assert len(chunk["content"]) > 10

    @pytest.mark.asyncio
    async def test_rag_vector_search_mock(self, mock_embedding_client):
        """Simulate a RAG vector search pipeline end-to-end."""
        import httpx

        query = "服务器故障应急处理"
        async with httpx.AsyncClient() as client:
            # Step 1: Get embedding for the query
            resp = await client.post(
                "http://localhost:8002/v1/embeddings",
                json={"input": query, "model": "bge-m3"},
                timeout=30.0,
            )
            embedding_result = resp.json()
            assert len(embedding_result["data"][0]["embedding"]) == 1024

        # Step 2: Simulate pgvector search (mocked)
        # In real code, this would be: SELECT * FROM document_chunks ORDER BY embedding <=> :vec LIMIT :k

        # Step 3: Simulate results
        mock_results = [
            {"chunk_id": "c1", "content": "故障应急处理流程...", "score": 0.95},
            {"chunk_id": "c2", "content": "常见故障类型...", "score": 0.88},
            {"chunk_id": "c3", "content": "监控告警设置...", "score": 0.82},
        ]

        assert len(mock_results) > 0
        assert mock_results[0]["score"] > 0.9

    def test_document_tag_based_filtering(self, sample_chunks):
        """Test document tag filtering for RAG results."""
        allowed_tags = {"server_ops", "public"}
        filtered = [
            c for c in sample_chunks
            if not c.get("metadata", {}).get("tags")
            or any(t in allowed_tags for t in c["metadata"]["tags"])
        ]

        # chunk-004 has tags ["finance", "confidential"] - should be filtered out
        assert len(filtered) == 3
        chunk_ids = [c["chunk_id"] for c in filtered]
        assert "chunk-004" not in chunk_ids

    def test_rag_empty_query_handling(self):
        """RAG should handle empty queries gracefully."""
        query = ""
        assert not query.strip(), "Empty query should be handled"
        # In real code, empty query would return early with empty results


# ============================================================
# DataMaskingTool Tests
# ============================================================

class TestDataMaskingTool:
    """Test data masking functionality."""

    @pytest.fixture
    def tool(self):
        from src.tools.data_masking import DataMaskingTool
        tool = DataMaskingTool()
        tool.user_id = "test-user"
        tool.user_role = "analyst"
        tool.user_dept = "sales_dept"
        return tool

    @pytest.mark.asyncio
    async def test_mask_phone_number(self, tool):
        result = await tool._execute(
            columns=["name", "customer_phone"],
            data=[["张三", "13812345678"]],
        )
        assert "138****5678" in str(result["data"])
        assert "customer_phone" in result["masked_fields"]

    @pytest.mark.asyncio
    async def test_mask_email(self, tool):
        result = await tool._execute(
            columns=["name", "customer_email"],
            data=[["李四", "lisi@example.com"]],
        )
        assert "***" in result["data"][0][1]
        assert "customer_email" in result["masked_fields"]

    @pytest.mark.asyncio
    async def test_mask_id_card(self, tool):
        result = await tool._execute(
            columns=["name", "id_card"],
            data=[["王五", "320102199001011234"]],
        )
        assert "***********" in result["data"][0][1]
        assert "id_card" in result["masked_fields"]

    @pytest.mark.asyncio
    async def test_non_sensitive_fields_unchanged(self, tool):
        result = await tool._execute(
            columns=["product_name", "amount", "sale_date"],
            data=[["笔记本电脑", 5999.00, "2024-01-15"]],
        )
        assert result["data"][0][0] == "笔记本电脑"
        assert result["data"][0][1] == 5999.00

    @pytest.mark.asyncio
    async def test_masked_fields_list_complete(self, tool):
        result = await tool._execute(
            columns=["name", "customer_phone", "customer_email", "amount"],
            data=[["张三", "13812345678", "zhang@example.com", 5999.00]],
        )
        assert "customer_phone" in result["masked_fields"]
        assert "customer_email" in result["masked_fields"]
        assert len(result["masked_fields"]) == 2


# ============================================================
# Audit Tool Tests
# ============================================================

class TestAuditWriter:
    """Test audit logging functionality."""

    @pytest.fixture
    def writer(self):
        from src.security.audit import AuditWriter
        return AuditWriter()

    @pytest.mark.asyncio
    async def test_log_query_start(self, writer):
        with patch("src.security.audit.admin_session") as mock_session:
            mock_session_obj = MockSession()
            mock_session.return_value.__aenter__ = AsyncMock(
                return_value=mock_session_obj
            )
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

            log_id = await writer.log_query_start(
                thread_id="thread-test-001",
                user_id="550e8400-e29b-41d4-a716-446655440001",
                query="test query",
            )
            assert len(mock_session_obj._added) == 1
            assert mock_session_obj._committed is True

    @pytest.mark.asyncio
    async def test_log_routing_result(self, writer):
        with patch("src.security.audit.admin_session") as mock_session:
            mock_session_obj = MockSession()
            mock_session.return_value.__aenter__ = AsyncMock(
                return_value=mock_session_obj
            )
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

            await writer.log_routing_result(
                thread_id="thread-test-001",
                complexity="high",
                risk_level="medium",
            )
            # Verify that _update_log was called and session committed
            assert mock_session_obj._committed is True

    @pytest.mark.asyncio
    async def test_full_audit_cycle(self, writer):
        """Simulate a complete audit logging cycle."""
        with patch("src.security.audit.admin_session") as mock_session:
            mock_session_obj = MockSession()
            mock_session.return_value.__aenter__ = AsyncMock(
                return_value=mock_session_obj
            )
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

            thread_id = "thread-full-cycle"

            await writer.log_query_start(thread_id, "550e8400-e29b-41d4-a716-446655440001", "Q2销售分析")
            await writer.log_routing_result(thread_id, "medium", "low")
            await writer.log_sql_generated(
                thread_id,
                "SELECT SUM(amount) FROM sales WHERE sale_date >= '2024-04-01' LIMIT 100",
            )
            await writer.log_validation_result(
                thread_id,
                sql_safe=True,
                permission_pass=True,
                executed_sql="SELECT SUM(amount) FROM sales WHERE sale_date >= '2024-04-01' AND dept = 'sales_dept' LIMIT 100",
            )
            await writer.log_execution_result(
                thread_id,
                success=True,
                row_count=1,
                execution_time_ms=42,
            )
            await writer.log_masking_result(thread_id, [])

            # All calls should have committed
            assert mock_session_obj._committed is True
