"""
Pytest fixtures for enterprise-platform tests.

Provides shared fixtures: mock_llm, mock_db_session, test_settings,
sample_agent_state, mock_embedding_client, and mock_auth.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.orchestration.state import AgentState


# ============================================================
# Settings
# ============================================================

@pytest.fixture
def test_settings():
    """Provide test settings that work without a real .env file."""
    with patch("config.settings.settings") as mock_settings:
        mock_settings.deepseek_api_key = "sk-test-key"
        mock_settings.deepseek_base_url = "https://api.deepseek.com/v1"
        mock_settings.deepseek_model_name = "deepseek-chat"
        mock_settings.qwen_api_base = "http://localhost:8001/v1"
        mock_settings.qwen_model_name = "qwen2.5-7b-instruct"
        mock_settings.qwen_api_key = "not-needed"
        mock_settings.bge_api_base = "http://localhost:8002/v1"
        mock_settings.bge_model_name = "bge-m3"
        mock_settings.llm_temperature = 0.0
        mock_settings.llm_max_tokens = 4096
        mock_settings.sql_timeout_seconds = 30
        mock_settings.sql_max_retry = 2
        mock_settings.sql_max_rows = 1000
        mock_settings.sql_explain_cost_threshold = 50000
        mock_settings.sensitive_fields = ["phone", "id_card", "email", "bank_account", "password"]
        mock_settings.jwt_secret_key = "test-secret-key"
        mock_settings.jwt_algorithm = "HS256"
        mock_settings.jwt_expire_minutes = 480
        mock_settings.langfuse_public_key = ""
        mock_settings.langfuse_secret_key = ""
        mock_settings.langfuse_host = "https://cloud.langfuse.com"
        mock_settings.app_name = "企业智能数据分析平台"
        mock_settings.debug = False
        mock_settings.read_only_db_url = "postgresql+asyncpg://eda_admin:eda_admin_123@localhost:5432/eda_platform"
        mock_settings.admin_db_url = "postgresql+asyncpg://eda_admin:eda_admin_123@localhost:5432/eda_platform"
        mock_settings.redis_url = "redis://localhost:6379/0"
        mock_settings.minio_endpoint = "localhost:9000"
        mock_settings.minio_access_key = "minioadmin"
        mock_settings.minio_secret_key = "minioadmin"
        mock_settings.minio_bucket = "eda-documents"
        mock_settings.minio_secure = False
        yield mock_settings


# ============================================================
# Mock LLM
# ============================================================

@pytest.fixture
def mock_llm():
    """Mock ChatOpenAI that returns a configurable JSON response.

    Usage in tests:
        mock_llm.set_response({"intent": "complex_task", "complexity": "high"})
    """
    mock = MagicMock()

    def set_response(response_dict: dict, is_async: bool = True):
        """Configure the mock to return the given dict as JSON."""
        json_str = json.dumps(response_dict, ensure_ascii=False)
        message = AIMessage(content=json_str)

        if is_async:
            async def _ainvoke(*args, **kwargs):
                return message

            async def _astream(*args, **kwargs):
                yield message

            mock.ainvoke = _ainvoke
            mock.astream = _astream
        else:
            mock.invoke = MagicMock(return_value=message)

    mock.set_response = set_response

    # Default: return a simple response
    set_response({"intent": "simple_qa", "complexity": "low"})

    return mock


@pytest.fixture
def mock_async_llm():
    """Mock async ChatOpenAI that supports structured output patterns."""
    mock = AsyncMock()

    async def _ainvoke(*args, **kwargs):
        content = kwargs.get("content", "")
        return AIMessage(content=json.dumps({"answer": "mock response"}))

    mock.ainvoke = _ainvoke
    mock.astream = AsyncMock()
    mock.bind_tools = MagicMock(return_value=mock)
    mock.with_structured_output = MagicMock(return_value=mock)

    return mock


# ============================================================
# Mock DB Session
# ============================================================

class MockResult:
    """Simulates a SQLAlchemy result with fetchall/fetchmany/keys support."""

    def __init__(self, columns=None, data=None):
        self._columns = columns or []
        self._data = data or []
        self.closed = False

    def keys(self):
        return self._columns

    def fetchall(self):
        return self._data

    def fetchmany(self, size=None):
        if size is None:
            return self._data
        return self._data[:size]

    def fetchone(self):
        return self._data[0] if self._data else None

    def scalar(self):
        if self._data and self._data[0]:
            row = self._data[0]
            if isinstance(row, (tuple, list)):
                return row[0]
            return row
        return 0

    def close(self):
        self.closed = True


class MockSession:
    """Mock async SQLAlchemy session."""

    def __init__(self, results=None):
        self._results = results or {}
        self._executed_sql = []
        self._added = []
        self._committed = False
        self._rolled_back = False

    @property
    def executed_sql(self):
        return self._executed_sql

    async def execute(self, stmt, params=None):
        import sqlparse
        sql_str = str(stmt) if hasattr(stmt, "__str__") else stmt
        self._executed_sql.append(sql_str)
        parsed = sqlparse.parse(sql_str)[0]
        stmt_type = parsed.get_type()

        # Return result based on SQL type
        for key, result in self._results.items():
            if key.upper() in sql_str.upper():
                return result

        # Default empty result
        return MockResult()

    async def commit(self):
        self._committed = True

    async def rollback(self):
        self._rolled_back = True

    def add(self, obj):
        self._added.append(obj)

    async def close(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def mock_db_session():
    """Create a mock database session with configurable results.

    Usage:
        session = mock_db_session()
        session._results["SELECT"] = MockResult(columns=["id"], data=[(1,)])
    """
    return MockSession()


# ============================================================
# Sample AgentState
# ============================================================

@pytest.fixture
def sample_agent_state():
    """Provide a fully populated AgentState for scenario tests."""
    state: AgentState = {
        "user_id": "550e8400-e29b-41d4-a716-446655440001",
        "user_dept": "sales_dept",
        "user_role": "analyst",
        "query": "",
        "messages": [],
        "intent": "",
        "query_complexity": "",
        "task_plan": [],
        "relevant_schemas": "",
        "generated_sql": "",
        "sql_retry_count": 0,
        "execution_result": {},
        "execution_error": "",
        "sql_safe": True,
        "permission_pass": True,
        "requires_human_review": False,
        "human_approval": False,
        "critic_result": {},
        "replan_count": 0,
        "prev_score": 0.0,
        "retrieved_docs": [],
        "hallucination_check_pass": True,
        "final_answer": "",
        "masked_result": {},
        "audit_log": {},
    }
    return state


@pytest.fixture
def s1_agent_state(sample_agent_state):
    """AgentState pre-configured for S1 scenario: server failure analysis."""
    state = dict(sample_agent_state)
    state["query"] = "近三月服务器故障分析+优化方案+生成工单"
    state["messages"] = [HumanMessage(content=state["query"])]
    return state


@pytest.fixture
def s2_agent_state(sample_agent_state):
    """AgentState pre-configured for S2 scenario: sales comparison."""
    state = dict(sample_agent_state)
    state["query"] = "华南区Q2销售额与去年同期对比"
    state["messages"] = [HumanMessage(content=state["query"])]
    return state


# ============================================================
# Mock Embedding Client
# ============================================================

@pytest.fixture
def mock_embedding_client():
    """Mock httpx client for BGE-M3 embedding API."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()

        async def mock_post(*args, **kwargs):
            response = MagicMock()
            response.status_code = 200
            response.json = MagicMock(return_value={
                "data": [{"embedding": [0.1] * 1024, "index": 0}]
            })
            return response

        mock_client.post = mock_post
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        yield mock_client


# ============================================================
# Mock Auth / RBAC
# ============================================================

@pytest.fixture
def analyst_permissions():
    """Permissions fixture for an analyst role."""
    return {
        "user_id": "user-analyst-001",
        "dept": "sales_dept",
        "role_name": "analyst",
        "table_permissions": {
            "sales": ["read"],
            "orders": ["read"],
            "servers": ["read"],
            "tickets": ["read"],
        },
        "field_permissions": {
            "sales": ["product_name", "category", "amount", "quantity", "sale_date", "dept", "salesperson"],
            "servers": ["hostname", "status", "failure_count", "last_failure", "dept"],
            "tickets": ["ticket_id", "title", "status", "assignee", "created_at"],
        },
        "row_conditions": {
            "sales": "dept = '{{user_dept}}'",
        },
        "can_export": True,
        "max_query_rows": 1000,
        "doc_tags_allowed": ["server_ops", "sop", "public"],
    }


@pytest.fixture
def admin_permissions():
    """Permissions fixture for an admin role."""
    return {
        "user_id": "user-admin-001",
        "dept": "it_dept",
        "role_name": "admin",
        "table_permissions": {
            "sales": ["read", "write"],
            "orders": ["read", "write"],
            "servers": ["read", "write"],
            "tickets": ["read", "write", "delete"],
            "employees": ["read"],
        },
        "field_permissions": {
            "sales": ["*"],
            "servers": ["*"],
            "tickets": ["*"],
            "employees": ["*"],
        },
        "row_conditions": {},
        "can_export": True,
        "max_query_rows": 5000,
        "doc_tags_allowed": ["*"],
    }


@pytest.fixture
def viewer_permissions():
    """Permissions fixture for a viewer role (restricted)."""
    return {
        "user_id": "user-viewer-001",
        "dept": "sales_dept",
        "role_name": "viewer",
        "table_permissions": {
            "sales": ["read"],
        },
        "field_permissions": {
            "sales": ["product_name", "category", "amount"],
        },
        "row_conditions": {
            "sales": "dept = '{{user_dept}}'",
        },
        "can_export": False,
        "max_query_rows": 100,
        "doc_tags_allowed": ["public"],
    }


# ============================================================
# Event Loop for async tests
# ============================================================

@pytest.fixture(scope="session")
def event_loop():
    """Create a session-scoped event loop for async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
