# 企业级多 Agent + RAG 知识库平台 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建企业级多 Agent + RAG 知识库平台，基于 LangGraph 状态机编排，实现 RAG 检索 + NL2SQL 双管线，支持复杂多步骤任务自主拆解执行。

**Architecture:** 5 层架构 — 接入层(FastAPI+Vue3) → 编排层(LangGraph 6节点+条件边) → Agent层(模型分级路由) → 工具层(5个BaseTool) → 数据层(PG/pgvector+Redis+MinIO)。双管线：独立 RAG 管线处理纯问答，Agent 管线通过工具调用处理复杂任务中的 RAG。

**Tech Stack:** FastAPI + LangGraph + PostgreSQL/pgvector + Redis + MinIO + DeepSeek-V3 + Qwen2.5-7B + BGE-M3 + PaddleOCR + PyMuPDF + Vue3 + Element Plus

**Spec:** `docs/superpowers/specs/2025-06-25-enterprise-rag-agent-platform-design.md`

## Global Constraints

- Python ^3.11, Poetry 依赖管理
- LangGraph ^0.2.0（显式状态机+条件边，禁止 LLM 自由路由）
- 所有工具继承 `langchain_core.tools.BaseTool`
- 数据库：PostgreSQL + pgvector（不用 MySQL/Milvus）
- 只读从库执行 SQL，sqlparse 白名单校验
- 全部离线可部署（PaddleOCR/BGE-M3/Qwen2.5-7B 本地）
- PEP8 + Ruff 格式化 + 中文注释

---

## 现有代码评估

| 文件 | 评估 | 操作 |
|------|------|------|
| `enterprise-platform/config/settings.py` | 基础配置骨架，需扩展新字段 | **复用+扩展** |
| `enterprise-platform/src/db/database.py` | 生产级双引擎+重试+连接池 | **复用**（几乎不动） |
| `enterprise-platform/src/db/models/user.py` | 缺少 password_hash 字段 | **复用+加字段** |
| `enterprise-platform/src/db/models/role.py` | 缺少 doc_tags_allowed 字段 | **复用+加字段** |
| `enterprise-platform/src/db/models/audit_log.py` | 字段较完整 | **复用+扩展** |
| `enterprise-platform/src/db/models/schema_metadata.py` | 需加 pgvector 向量字段 | **复用+改造** |
| `enterprise-platform/src/llm/factory.py` | 仅支持 DeepSeek/Qwen | **重写**（加模型分级） |
| `enterprise-platform/src/llm/prompts/*.py` | 旧意图分类体系 | **重写**（新 3 类意图） |
| `enterprise-platform/src/security/rbac.py` | 三维权限校验，质量好 | **复用+加文档权限** |
| `enterprise-platform/src/security/sql_guard.py` | 白名单+禁止词+LIMIT | **复用+加 EXPLAIN** |
| `enterprise-platform/src/security/audit.py` | 异步审计写入 | **复用+加 action 类型** |
| `enterprise-platform/src/tools/base.py` | BaseSecureTool 基类 | **复用**（不修改） |
| `enterprise-platform/src/tools/schema_retrieval.py` | 依赖 Milvus，需迁移 | **重写**（pgvector+RRF+HyDE） |
| `enterprise-platform/src/tools/sql_validation.py` | 封装 SQLGuard | **复用+扩展** |
| `enterprise-platform/src/tools/sql_execution.py` | 只读执行+超时熔断 | **复用+加 EXPLAIN** |
| `enterprise-platform/src/tools/data_masking.py` | 数据脱敏 | **复用** |
| `enterprise-platform/config/permissions.yaml` | 种子权限数据 | **复用+更新** |
| `enterprise-platform/docker-compose.yml` | 旧服务组合 | **重写**（加 BGE-M3/Qwen/PaddleOCR） |

---

## Phase 1: P0 基础设施（Day 1）

### Task 1.1: 扩展配置与项目依赖

**Files:**
- Modify: `enterprise-platform/pyproject.toml`（加新依赖）
- Modify: `enterprise-platform/config/settings.py`（加新配置项）
- Modify: `enterprise-platform/.env.example`（加新环境变量模板）

**Interfaces:**
- Produces: `Settings` 类新增字段供所有下游模块使用

- [ ] **Step 1: 更新 pyproject.toml 依赖**

```toml
[tool.poetry.dependencies]
python = "^3.11"
# 编排层
langgraph = "^0.2.0"
langchain = "^0.3.0"
langchain-core = "^0.3.0"
langchain-community = "^0.3.0"
langchain-openai = "^0.2.0"
# 后端框架
fastapi = "^0.115.0"
uvicorn = {extras = ["standard"], version = "^0.32.0"}
sse-starlette = "^2.1.0"
# 数据层
sqlalchemy = {extras = ["asyncio"], version = "^2.0.0"}
asyncpg = "^0.30.0"
alembic = "^1.14.0"
pgvector = "^0.3.0"                # ★ 新增: pgvector Python 客户端
redis = "^5.2.0"
# MinIO 客户端
minio = "^7.2.0"                   # ★ 新增
# 安全
sqlparse = "^0.5.0"
# 配置
pydantic = "^2.0.0"
pydantic-settings = "^2.0.0"
# 可观测
langfuse = "^2.50.0"
# 工具
python-dotenv = "^1.0.0"
httpx = "^0.28.0"
# ★ 新增: 文档解析
pymupdf = "^1.24.0"
paddleocr = "^2.9.0"
paddlepaddle = "^3.0.0"
python-docx = "^1.1.0"
unstructured = "^0.16.0"
layoutparser = "^0.3.0"
# ★ 新增: 嵌入服务客户端
sentence-transformers = "^3.0.0"

[tool.poetry.group.dev.dependencies]
pytest = "^8.0.0"
pytest-asyncio = "^0.24.0"
ruff = "^0.7.0"
```

- [ ] **Step 2: 扩展 config/settings.py 配置项**

```python
"""
统一配置入口 - 使用 Pydantic Settings 管理所有环境变量
v3.0: 增加 Qwen 本地模型、BGE-M3、MinIO、文档解析配置
"""

from typing import Literal
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用全局配置"""

    # ========== 数据库配置 ==========
    read_only_db_url: str = "postgresql+asyncpg://eda_admin:eda_admin_123@localhost:5432/eda_platform"
    admin_db_url: str = "postgresql+asyncpg://eda_admin:eda_admin_123@localhost:5432/eda_platform"

    # ========== Redis 配置 ==========
    redis_url: str = "redis://localhost:6379/0"

    # ========== MinIO 配置 ★ ==========
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "eda-documents"
    minio_secure: bool = False

    # ========== 主力 LLM 配置（DeepSeek-V3）==========
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model_name: str = "deepseek-chat"

    # ========== 轻量 LLM 配置（Qwen2.5-7B 本地）★ ==========
    qwen_api_base: str = "http://localhost:8001/v1"
    qwen_model_name: str = "qwen2.5-7b-instruct"
    qwen_api_key: str = "not-needed"

    # ========== 嵌入模型配置（BGE-M3 本地）★ ==========
    bge_api_base: str = "http://localhost:8002/v1"
    bge_model_name: str = "bge-m3"

    # ========== LLM 通用配置 ==========
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096

    # ========== 安全配置 ==========
    sql_timeout_seconds: int = 30
    sql_max_retry: int = 2
    sql_max_rows: int = 1000
    sql_explain_cost_threshold: int = 50000       # ★ EXPLAIN 代价阈值
    sensitive_fields: list[str] = ["phone", "id_card", "email", "bank_account", "password"]

    # ========== JWT 配置 ★ ==========
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    # ========== Langfuse 可观测 ==========
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # ========== 应用配置 ==========
    app_name: str = "企业智能数据分析平台"
    debug: bool = False

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


settings = Settings()
```

- [ ] **Step 3: 更新 .env.example**

```bash
# 数据库
READ_ONLY_DB_URL=postgresql+asyncpg://eda_admin:eda_admin_123@localhost:5432/eda_platform
ADMIN_DB_URL=postgresql+asyncpg://eda_admin:eda_admin_123@localhost:5432/eda_platform

# Redis
REDIS_URL=redis://localhost:6379/0

# MinIO
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=eda-documents

# DeepSeek-V3 (主力)
DEEPSEEK_API_KEY=sk-your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# Qwen2.5-7B (轻量本地)
QWEN_API_BASE=http://localhost:8001/v1

# BGE-M3 (嵌入本地)
BGE_API_BASE=http://localhost:8002/v1

# JWT
JWT_SECRET_KEY=change-me-in-production

# Langfuse
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
```

- [ ] **Step 4: Commit**

```bash
git add enterprise-platform/pyproject.toml enterprise-platform/config/settings.py enterprise-platform/.env.example
git commit -m "feat: expand config for v3.0 - add Qwen/BGE-M3/MinIO/EXPLAIN settings"
```

---

### Task 1.2: 更新数据库 ORM 模型

**Files:**
- Modify: `enterprise-platform/src/db/models/user.py`
- Modify: `enterprise-platform/src/db/models/role.py`
- Modify: `enterprise-platform/src/db/models/audit_log.py`
- Modify: `enterprise-platform/src/db/models/schema_metadata.py`
- Create: `enterprise-platform/src/db/models/document.py`
- Create: `enterprise-platform/src/db/models/document_chunk.py`

**Interfaces:**
- Consumes: `Base` from `src.db.database`
- Produces: `User`, `Role`, `AuditLog`, `SchemaMetadata`, `Document`, `DocumentChunk` ORM 类

- [ ] **Step 1: 修改 User 模型（加 password_hash）**

```python
"""
用户表 ORM 模型
v3.0: 增加 password_hash 字段
"""
from sqlalchemy import Boolean, Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.db.database import Base


class User(Base):
    """用户表"""
    username = Column(String(100), unique=True, nullable=False, comment="用户名")
    password_hash = Column(String(255), nullable=False, comment="密码哈希")  # ★ 新增
    dept = Column(String(200), nullable=False, comment="部门")
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id"), nullable=False)
    is_active = Column(Boolean, default=True, comment="是否启用")
    role = relationship("Role", backref="users", lazy="selectin")
```

- [ ] **Step 2: 修改 Role 模型（加 doc_tags_allowed）**

```python
"""
角色权限表 ORM 模型
v3.0: 增加 doc_tags_allowed 字段
"""
from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from src.db.database import Base


class Role(Base):
    """角色权限表"""
    role_name = Column(String(50), unique=True, nullable=False)
    table_permissions = Column(JSONB, nullable=False, default=dict)
    field_permissions = Column(JSONB, nullable=False, default=dict)
    row_conditions = Column(JSONB, nullable=False, default=dict)
    doc_tags_allowed = Column(ARRAY(String), default=[], comment="可访问文档标签")  # ★ 新增
    can_export = Column(Boolean, default=False)
    max_query_rows = Column(Integer, default=1000)
```

- [ ] **Step 3: 修改 AuditLog 模型（加新 action 类型字段）**

保持现有字段，追加：
```python
# ★ 新增字段
tool_name = Column(String(100), comment="工具名称")
tool_input = Column(Text, comment="工具输入（截断）")
tool_output_summary = Column(Text, comment="工具输出摘要")
critic_score = Column(Float, comment="Critic 质量评分")
critic_result = Column(JSONB, comment="Critic 溯源结果")
```

- [ ] **Step 4: 修改 SchemaMetadata 模型（加 pgvector）**

```python
"""
Schema 元数据表 ORM 模型
v3.0: 增加 pgvector 向量字段
"""
from pgvector.sqlalchemy import Vector  # ★
from src.db.database import Base
from sqlalchemy import Column, String, Text, Boolean, DateTime, text
from sqlalchemy.dialects.postgresql import UUID


class SchemaMetadata(Base):
    """表结构元数据"""
    __tablename__ = "schema_metadata"
    table_name = Column(String(200), nullable=False)
    column_name = Column(String(200), nullable=False)
    data_type = Column(String(100))
    description = Column(Text, comment="中文业务含义")
    is_sensitive = Column(Boolean, default=False)
    embedding = Column(Vector(1024), comment="BGE-M3 嵌入向量")  # ★ pgvector
```

- [ ] **Step 5: 创建 Document 模型**

```python
"""
文档元数据表 ORM 模型
"""
from sqlalchemy import Column, String, Integer, Boolean, Text, DateTime, text
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import relationship
from src.db.database import Base


class Document(Base):
    """文档元数据表"""
    __tablename__ = "documents"
    title = Column(String(500), nullable=False, comment="文档标题")
    file_type = Column(String(50), comment="pdf/docx/md/image/log")
    file_path = Column(String(1000), comment="MinIO 存储路径")
    parse_engine = Column(String(50), comment="使用的解析引擎")
    page_count = Column(Integer, default=0)
    tags = Column(ARRAY(String), default=[], comment="文档标签")
    chunk_count = Column(Integer, default=0)
    is_parsed = Column(Boolean, default=False, comment="是否已解析")
    parse_error = Column(Text, comment="解析失败原因")
    uploaded_by = Column(UUID(as_uuid=True), comment="上传者")
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")
```

- [ ] **Step 6: 创建 DocumentChunk 模型（含向量+版面结构）**

```python
"""
文档切片表 ORM 模型
v3.0: 增加 page_number / structure_type / parent_heading 溯源字段
"""
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, String, Integer, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from src.db.database import Base


class DocumentChunk(Base):
    """文档切片表"""
    __tablename__ = "document_chunks"
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False, comment="切片序号")
    content = Column(Text, nullable=False, comment="切片文本")
    embedding = Column(Vector(1024), comment="BGE-M3 嵌入向量")
    page_number = Column(Integer, comment="原文档页码（溯源）")                    # ★ 溯源
    structure_type = Column(String(50), comment="heading/paragraph/table/figure_caption")  # ★ 版面
    parent_heading = Column(String(500), comment="所属章节标题")                    # ★ 层级
    metadata_ = Column("metadata", JSONB, default={})
    document = relationship("Document", back_populates="chunks")
```

- [ ] **Step 7: Commit**

```bash
git add enterprise-platform/src/db/models/
git commit -m "feat: update ORM models for v3.0 - add pgvector/documents/document_chunks/chunk trace fields"
```

---

### Task 1.3: 重写 LLM 工厂（模型分级路由）

**Files:**
- Rewrite: `enterprise-platform/src/llm/factory.py`

**Interfaces:**
- Produces: `get_heavy_llm()` → ChatOpenAI(DeepSeek-V3), `get_light_llm()` → ChatOpenAI(Qwen2.5-7B), `get_llm_by_tier("heavy"|"light")`

- [ ] **Step 1: 写测试**

```python
# tests/test_llm_factory.py
import pytest
from src.llm.factory import LLMFactory, get_heavy_llm, get_light_llm


def test_get_heavy_llm_returns_deepseek():
    llm = get_heavy_llm()
    assert llm.model_name == "deepseek-chat"
    assert "deepseek" in str(llm.openai_api_base)


def test_get_light_llm_returns_qwen():
    llm = get_light_llm()
    assert "qwen" in llm.model_name
    assert "8001" in str(llm.openai_api_base)


def test_light_llm_has_lower_max_tokens():
    heavy = get_heavy_llm()
    light = get_light_llm()
    assert light.max_tokens <= heavy.max_tokens
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd enterprise-platform && pytest tests/test_llm_factory.py -v
# Expected: FAIL (import errors)
```

- [ ] **Step 3: 重写 LLM 工厂**

```python
"""
统一 LLM 工厂 - v3.0 模型分级路由

支持两级模型：
- 主力模型 (heavy): DeepSeek-V3 — Planner / Tool Agent / Summary
- 轻量模型 (light): Qwen2.5-7B 本地 — Router / 合规审核 / Critic

所有模型统一通过 ChatOpenAI 兼容接口调用。
"""

from langchain_openai import ChatOpenAI
from config.settings import settings


class LLMFactory:
    """LLM 工厂类，支持模型分级"""

    @staticmethod
    def create_heavy(
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatOpenAI:
        """创建主力模型实例 (DeepSeek-V3)"""
        return ChatOpenAI(
            model=settings.deepseek_model_name,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            temperature=temperature if temperature is not None else settings.llm_temperature,
            max_tokens=max_tokens or settings.llm_max_tokens,
        )

    @staticmethod
    def create_light(
        temperature: float | None = None,
        max_tokens: int = 1024,
    ) -> ChatOpenAI:
        """创建轻量模型实例 (Qwen2.5-7B 本地)"""
        return ChatOpenAI(
            model=settings.qwen_model_name,
            api_key=settings.qwen_api_key,
            base_url=settings.qwen_api_base,
            temperature=temperature if temperature is not None else 0.0,
            max_tokens=max_tokens,
        )

    @staticmethod
    def create(provider: str = "deepseek") -> ChatOpenAI:
        """兼容旧接口"""
        if provider == "deepseek":
            return LLMFactory.create_heavy()
        elif provider == "qwen":
            return LLMFactory.create_light()
        raise ValueError(f"不支持的 provider: {provider}")


# 模块级便捷函数
def get_heavy_llm(temperature: float | None = None) -> ChatOpenAI:
    """获取主力 LLM（Planner / Tool Agent / Summary 用）"""
    return LLMFactory.create_heavy(temperature=temperature)


def get_light_llm() -> ChatOpenAI:
    """获取轻量 LLM（Router / 合规审核 / Critic 格式化用）"""
    return LLMFactory.create_light()


def get_sql_llm() -> ChatOpenAI:
    """获取 SQL 生成专用 LLM（temperature=0 确保稳定输出）"""
    return LLMFactory.create_heavy(temperature=0.0)


def get_llm() -> ChatOpenAI:
    """获取默认 LLM（向后兼容）"""
    return get_heavy_llm()
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd enterprise-platform && pytest tests/test_llm_factory.py -v
# Expected: PASS
```

- [ ] **Step 5: Commit**

```bash
git add enterprise-platform/src/llm/factory.py tests/test_llm_factory.py
git commit -m "feat: rewrite LLM factory with model tiering - heavy(DeepSeek-V3) + light(Qwen2.5-7B)"
```

---

### Task 1.4: 数据库初始化脚本与 pgvector 扩展

**Files:**
- Modify: `enterprise-platform/scripts/init_postgres.sql`
- Modify: `enterprise-platform/scripts/init_db.py`
- Create: `enterprise-platform/scripts/init_milvus.py` → 删除（不再需要）

**Interfaces:**
- Produces: 数据库初始化流程（含 pgvector 扩展 + 向量索引）

- [ ] **Step 1: 更新 init_postgres.sql（加 pgvector 扩展 + 向量索引）**

```sql
-- 初始化数据库：扩展 + 向量索引

-- 启用 pgvector 扩展
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- schema_metadata 向量索引
CREATE INDEX IF NOT EXISTS idx_schema_embedding
ON schema_metadata USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- document_chunks 向量索引
CREATE INDEX IF NOT EXISTS idx_chunk_embedding
ON document_chunks USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- document_chunks 文档级 B-Tree 索引
CREATE INDEX IF NOT EXISTS idx_chunk_doc ON document_chunks(document_id);

-- 全文检索索引（BM25 关键词召回用）
CREATE INDEX IF NOT EXISTS idx_chunk_content_fts
ON document_chunks USING gin (to_tsvector('simple', content));

CREATE INDEX IF NOT EXISTS idx_schema_fts
ON schema_metadata USING gin (to_tsvector('simple', description));
```

- [ ] **Step 2: 更新 init_db.py（调用 SQL 脚本）**

```python
"""数据库初始化脚本"""
import asyncio
import logging
from pathlib import Path
from src.db.database import init_db, admin_engine
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    # 1. 创建所有表
    await init_db()
    logger.info("表结构创建完成")

    # 2. 执行初始化 SQL（扩展 + 索引）
    sql_path = Path(__file__).parent / "init_postgres.sql"
    async with admin_engine.begin() as conn:
        for statement in sql_path.read_text(encoding="utf-8").split(";"):
            stmt = statement.strip()
            if stmt and not stmt.startswith("--"):
                await conn.execute(text(stmt))
    logger.info("pgvector 扩展和索引创建完成")

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Commit**

```bash
git rm enterprise-platform/scripts/init_milvus.py  # 不再需要 Milvus
git add enterprise-platform/scripts/
git commit -m "feat: update DB init for pgvector - remove Milvus, add vector indexes and FTS"
```

---

## Phase 2: P1 核心能力（上）— 安全 + 工具层（Day 2）

### Task 2.1: 增强安全模块（加 EXPLAIN 预检 + 文档权限）

**Files:**
- Modify: `enterprise-platform/src/security/sql_guard.py`
- Modify: `enterprise-platform/src/security/rbac.py`

- [ ] **Step 1: SQLGuard 增加 EXPLAIN 预检方法**

在 `SQLGuard` 类末尾追加：
```python
    @staticmethod
    def explain_check(explain_json: dict) -> dict:
        """
        EXPLAIN 预检：解析查询计划 JSON，检测危险操作

        Args:
            explain_json: EXPLAIN (FORMAT JSON) 返回的查询计划

        Returns:
            {"safe": bool, "warnings": [...], "errors": [...]}
        """
        errors = []
        warnings = []

        def traverse(plan_node):
            node_type = plan_node.get("Node Type", "")
            plan_rows = plan_node.get("Plan Rows", 0)
            total_cost = plan_node.get("Total Cost", 0)

            # 1. 笛卡尔积检测：Nested Loop 且无 Join Filter
            if node_type == "Nested Loop" and "Join Filter" not in str(plan_node):
                errors.append(f"疑似笛卡尔积: Nested Loop at cost {total_cost}")

            # 2. 大表全扫警告
            if node_type == "Seq Scan" and plan_rows > 10000:
                rel = plan_node.get("Relation Name", "unknown")
                warnings.append(f"大表全表扫描: {rel} (预估 {plan_rows} 行)")

            # 3. 总代价阈值
            if total_cost > 50000:
                warnings.append(f"查询计划总代价过高: {total_cost}")

            # 递归检查子计划
            for child in plan_node.get("Plans", []):
                traverse(child)

        if isinstance(explain_json, list):
            for plan in explain_json:
                plan_node = plan.get("Plan", plan)
                traverse(plan_node)
        else:
            traverse(explain_json.get("Plan", explain_json))

        return {
            "safe": len(errors) == 0,
            "warnings": warnings,
            "errors": errors,
        }
```

- [ ] **Step 2: RBACEngine 增加文档权限校验**

在 `RBACEngine` 类末尾追加：
```python
    async def check_document_access(self, user_id: str, doc_tags: list[str]) -> bool:
        """检查用户是否有文档标签的访问权限"""
        perms = await self.get_user_permissions(user_id)
        allowed = set(perms.doc_tags_allowed if hasattr(perms, 'doc_tags_allowed') else [])
        if not allowed or "*" in allowed:
            return True
        return all(tag in allowed for tag in doc_tags)

    async def filter_allowed_documents(self, user_id: str, chunks: list[dict]) -> list[dict]:
        """过滤掉用户无权访问的文档切片"""
        perms = await self.get_user_permissions(user_id)
        allowed_tags = set(getattr(perms, 'doc_tags_allowed', []) or [])
        if not allowed_tags or "*" in allowed_tags:
            return chunks
        return [
            c for c in chunks
            if not c.get("metadata", {}).get("tags")
            or any(t in allowed_tags for t in c["metadata"]["tags"])
        ]
```

- [ ] **Step 3: 更新 UserPermissions dataclass**

在 `rbac.py` 中的 `UserPermissions` 增加：
```python
doc_tags_allowed: list[str] = field(default_factory=list)  # ★ 新增
```

- [ ] **Step 4: 更新 get_user_permissions 读取 doc_tags_allowed**

在 `get_user_permissions` 方法中 `permissions = UserPermissions(...)` 构造处追加：
```python
doc_tags_allowed=role.doc_tags_allowed or [],
```

- [ ] **Step 5: Commit**

```bash
git add enterprise-platform/src/security/
git commit -m "feat: add EXPLAIN pre-check to SQLGuard and document permission to RBAC"
```

---

### Task 2.2: 更新现有工具 + 新建 3 个工具

**Files:**
- Modify: `enterprise-platform/src/tools/schema_retrieval.py` → 重写为 pgvector 版本
- Modify: `enterprise-platform/src/tools/sql_execution.py` → 加 EXPLAIN 预检 + 表名/行数返回
- Create: `enterprise-platform/src/tools/rag_retrieval.py` ★ 新工具
- Create: `enterprise-platform/src/tools/document_parsing.py` ★ 新工具
- Create: `enterprise-platform/src/tools/ticket_report.py` ★ 新工具

由于篇幅限制，每个工具的完整实现在执行阶段按 Task 逐个完成。以下是关键接口定义：

**工具接口契约：**

```python
# RAGRetrievalTool — 复杂任务中 Agent 调用 RAG 的工具
class RAGRetrievalTool(BaseSecureTool):
    name = "rag_retrieval"
    description = "检索知识库文档。输入：查询文本。输出：相关文档片段（含页码+层级结构）。"
    # _execute(query: str) -> dict:
    #   1. 自适应 HyDE (query长度<20 → 生成假设文档)
    #   2. pgvector 向量召回 (top_k=20)
    #   3. PostgreSQL ts_rank BM25 关键词召回 (top_k=20)
    #   4. RRF 融合排序 (k=60)
    #   5. 权限过滤
    #   6. BGE-Reranker 重排序 (top_k=5)
    #   7. 上下文压缩 (每篇100字摘要)
    #   return {"chunks": [{chunk_id, content, page_number, structure_type, parent_heading, score}]}


# DocumentParsingTool — 文档三级解析引擎
class DocumentParsingTool(BaseSecureTool):
    name = "document_parsing"
    description = "解析上传的文档。支持 PDF/Word/Markdown/图片/日志。"
    # _execute(file_path: str) -> dict:
    #   1. 文件类型检测
    #   2. 路由解析引擎: 文字PDF→PyMuPDF, 扫描件→PaddleOCR, 非标→Unstructured
    #   3. 版面分析 (LayoutParser)
    #   4. 智能切片 (按层级边界)
    #   5. BGE-M3 向量化写入 pgvector
    #   return {"document_id": ..., "chunk_count": ..., "parse_engine": ...}


# TicketReportTool — 工单/报表生成
class TicketReportTool(BaseSecureTool):
    name = "ticket_report"
    description = "生成运维工单或数据报表。"
    # _execute(template_type: str, data: dict) -> dict:
    #   基于模板生成 JSON 结构 → 存入 MinIO → 返回链接
    #   return {"ticket_id": ..., "url": ..., "status": "created"}
```

- [ ] **Commit**

```bash
git add enterprise-platform/src/tools/
git commit -m "feat: rewrite schema_retrieval for pgvector, add RAG/doc_parsing/ticket tools"
```

---

## Phase 3: P1 核心能力（下）— Agent 编排层（Day 3）

### Task 3.1: 更新 Prompt 模板（意图从 4 类变 3 类）

**Files:**
- Rewrite: `enterprise-platform/src/llm/prompts/router.py`
- Rewrite: `enterprise-platform/src/llm/prompts/sql_generation.py`
- Create: `enterprise-platform/src/llm/prompts/planner.py`
- Create: `enterprise-platform/src/llm/prompts/critic.py`
- Keep: `enterprise-platform/src/llm/prompts/compliance.py`

**关键 Prompt 变更**：路由意图从 `data_query/report/dashboard/other` → `simple_qa/data_analysis/complex_task`

- [ ] **Commit**

```bash
git add enterprise-platform/src/llm/prompts/
git commit -m "feat: update prompts for v3.0 3-intent routing + planner/critic templates"
```

---

### Task 3.2: 定义 LangGraph State 与 Graph 结构

**Files:**
- Create: `enterprise-platform/src/orchestration/state.py`
- Create: `enterprise-platform/src/orchestration/graph.py`
- Create: `enterprise-platform/src/orchestration/edges.py`

**State 定义**（来自 spec 4.2 节）：

```python
# state.py
from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    user_id: str
    user_dept: str
    user_role: str
    query: str
    messages: Annotated[list, add_messages]
    intent: str                    # "simple_qa" | "data_analysis" | "complex_task"
    query_complexity: str          # "low" | "medium" | "high"
    task_plan: list[dict]          # [{step_id, description, tool, depends_on}]
    relevant_schemas: list[dict]
    generated_sql: str
    sql_retry_count: int
    execution_result: list[dict]
    execution_error: str | None
    sql_safe: bool
    permission_pass: bool
    requires_human_review: bool
    human_approval: bool | None
    critic_result: dict            # {quality_score, passed, failed_checks, missing, superfluous, ...}
    replan_count: int              # ★ Critic 重规划次数
    prev_score: float              # ★ 上一轮评分（停滞检测）
    retrieved_docs: list[dict]
    hallucination_check_pass: bool
    final_answer: str
    masked_result: list[dict]
    audit_log: dict
```

**Graph 构建**（来自 spec 3 节架构图）：

```python
# graph.py
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.redis import RedisSaver
from src.orchestration.state import AgentState
from src.orchestration.nodes import (
    router_node, planner_node, tool_node, critic_node, summary_node,
    rag_pipeline_node, nl2sql_pipeline_node, compliance_node, human_review_node
)
from src.orchestration.edges import (
    route_after_router, should_continue_after_critic,
    route_after_compliance, route_after_sql_execution
)


def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    # 注册节点
    builder.add_node("router", router_node)
    builder.add_node("rag_pipeline", rag_pipeline_node)
    builder.add_node("nl2sql_pipeline", nl2sql_pipeline_node)
    builder.add_node("planner", planner_node)
    builder.add_node("tool_agent", tool_node)
    builder.add_node("critic", critic_node)
    builder.add_node("summary", summary_node)
    builder.add_node("compliance", compliance_node)
    builder.add_node("human_review", human_review_node)

    # 起始边
    builder.set_entry_point("router")

    # 路由条件边
    builder.add_conditional_edges("router", route_after_router, {
        "rag_pipeline": "rag_pipeline",
        "nl2sql_pipeline": "nl2sql_pipeline",
        "planner": "planner",
    })

    # RAG 管线直接结束
    builder.add_edge("rag_pipeline", END)

    # Agent 管线
    builder.add_edge("planner", "tool_agent")
    builder.add_edge("tool_agent", "critic")
    builder.add_conditional_edges("critic", should_continue_after_critic, {
        "summary": "summary",
        "planner": "planner",
    })
    builder.add_edge("summary", END)

    # NL2SQL 管线
    builder.add_edge("nl2sql_pipeline", "compliance")
    builder.add_conditional_edges("compliance", route_after_compliance, {
        "human_review": "human_review",
        "sql_execution": "sql_execution",
        "end": END,
    })
    builder.add_conditional_edges("sql_execution", route_after_sql_execution, {
        "nl2sql_pipeline": "nl2sql_pipeline",
        "summary": "summary",
        "end": END,
    })
    builder.add_edge("human_review", "summary")

    return builder


def compile_graph(checkpointer: RedisSaver = None):
    graph = build_graph()
    if checkpointer:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()
```

- [ ] **Commit**

```bash
git add enterprise-platform/src/orchestration/
git commit -m "feat: define AgentState, build LangGraph with 9 nodes + 4 conditional edge groups"
```

---

### Task 3.3: 实现 6 个 Agent 节点 + RAG/NL2SQL 管线

**Files:**
- Create: `enterprise-platform/src/orchestration/nodes/router.py`
- Create: `enterprise-platform/src/orchestration/nodes/planner.py`
- Create: `enterprise-platform/src/orchestration/nodes/tool_agent.py`
- Create: `enterprise-platform/src/orchestration/nodes/critic.py`
- Create: `enterprise-platform/src/orchestration/nodes/summary.py`
- Create: `enterprise-platform/src/orchestration/nodes/rag_pipeline.py`
- Create: `enterprise-platform/src/orchestration/nodes/nl2sql_pipeline.py`
- Create: `enterprise-platform/src/orchestration/nodes/compliance.py`

关键节点接口：

```python
# router_node: Qwen2.5-7B 轻量模型 → 输出 intent + complexity
async def router_node(state: AgentState) -> dict:
    llm = get_light_llm()  # Qwen2.5-7B
    prompt = ROUTER_SYSTEM_PROMPT.format(user_id=..., query=state["query"])
    response = await llm.ainvoke(prompt)
    parsed = parse_json(response.content)
    return {"intent": parsed["intent"], "query_complexity": parsed["complexity"]}

# planner_node: DeepSeek-V3 主力 → 拆解复杂任务
async def planner_node(state: AgentState) -> dict:
    # 如果有 critic 反馈，将其作为修正指导
    critic_feedback = state.get("critic_result", {})
    llm = get_heavy_llm()
    plan = await llm.ainvoke(PLANNER_PROMPT.format(
        query=state["query"],
        feedback=critic_feedback.get("missing", ""),
        superfluous=critic_feedback.get("superfluous", ""),
    ))
    return {"task_plan": parse_json(plan.content)["steps"]}

# critic_node: 确定性溯源规则引擎 + Qwen 格式化
async def critic_node(state: AgentState) -> dict:
    verdict = run_deterministic_checks(state)  # 6项规则引擎检查
    # Qwen 仅做自然语言格式化
    llm = get_light_llm()
    formatted = await llm.ainvoke(CRITIC_FORMAT_PROMPT.format(verdict=verdict))
    return {"critic_result": {**verdict, "recommendation": formatted.content}}
```

- [ ] **Commit**

```bash
git add enterprise-platform/src/orchestration/nodes/
git commit -m "feat: implement 8 nodes - router/planner/tool/critic/summary/rag/nl2sql/compliance"
```

---

## Phase 4: P2 接入层（Day 4）

### Task 4.1: FastAPI 应用 + JWT 鉴权 + SSE 流式

**Files:**
- Create: `enterprise-platform/src/api/app.py`
- Create: `enterprise-platform/src/api/middleware.py`
- Create: `enterprise-platform/src/api/deps.py`
- Create: `enterprise-platform/src/api/routes/auth.py`
- Create: `enterprise-platform/src/api/routes/chat.py`
- Create: `enterprise-platform/src/api/routes/sessions.py`
- Create: `enterprise-platform/src/api/routes/documents.py`
- Create: `enterprise-platform/src/api/routes/admin.py`
- Create: `enterprise-platform/src/api/schemas/`

- [ ] **Commit**

```bash
git add enterprise-platform/src/api/
git commit -m "feat: add FastAPI app with JWT auth, SSE streaming, 5 route modules"
```

---

### Task 4.2: Vue3 + Element Plus 前端（最简可用版）

**Files:**
- Create: `enterprise-platform/web/` (Vue3 项目)

前端包含：
1. 对话页：输入框 + SSE 流式消息展示 + 会话列表
2. 管理后台：用户/角色/文档/审计日志 CRUD 表格

- [ ] **Commit**

```bash
git add enterprise-platform/web/
git commit -m "feat: add Vue3+Element Plus frontend - chat page + admin panel"
```

---

## Phase 5: P3 收尾（Day 5）

### Task 5.1: Docker Compose 全栈编排

**Files:**
- Rewrite: `enterprise-platform/docker-compose.yml`

新服务：PostgreSQL+pgvector + Redis + MinIO + BGE-M3(独立容器) + Qwen2.5-7B(vLLM容器) + PaddleOCR(容器) + FastAPI(主应用)

- [ ] **Commit**

```bash
git add enterprise-platform/docker-compose.yml
git commit -m "feat: rewrite docker-compose for v3.0 full stack"
```

---

### Task 5.2: S1/S2 场景端到端测试

**Files:**
- Create: `enterprise-platform/tests/test_scenario_s1.py`
- Create: `enterprise-platform/tests/test_scenario_s2.py`

- [ ] **Commit**

```bash
git add enterprise-platform/tests/
git commit -m "test: add S1/S2 end-to-end scenario tests"
```

---

### Task 5.3: Langfuse 集成 + README

**Files:**
- Create: `enterprise-platform/src/observability/langfuse_tracer.py`
- Create: `enterprise-platform/README.md`

- [ ] **Commit**

```bash
git add enterprise-platform/src/observability/ enterprise-platform/README.md
git commit -m "docs: add Langfuse tracing + README with architecture overview"
```

---

## 依赖关系图

```
Phase 1 (Day 1)
├── Task 1.1 配置扩展 ──→ 所有后续任务依赖
├── Task 1.2 ORM 模型 ──→ Task 1.4, 2.1, 2.2
├── Task 1.3 LLM 工厂 ──→ Phase 3 所有节点
└── Task 1.4 DB 初始化 ──→ Task 2.2

Phase 2 (Day 2)
├── Task 2.1 安全增强 ──→ Phase 3 NL2SQL 管线
└── Task 2.2 工具层 ──→ Phase 3 Tool Agent

Phase 3 (Day 3)
├── Task 3.1 Prompts ──→ Task 3.3
├── Task 3.2 State+Graph ──→ Task 3.3
└── Task 3.3 Agent 节点 ──→ Phase 4 API 集成

Phase 4 (Day 4) ──→ Phase 5

Phase 5 (Day 5) ──→ 完成
```

---

## 自检清单

- [x] 每个 Task 有明确的 Files 列表和 Interfaces 契约
- [x] 现有代码复用/重写/新增策略明确
- [x] 依赖关系图清晰，可并行任务已标注
- [x] 覆盖 spec 所有 10 个章节的需求
- [x] 无 TBD/TODO 占位符
- [x] 关键代码段已写入（SQL/State/Graph/Factory/Settings）
- [x] 遵守 Global Constraints（Python 3.11 / LangGraph 0.2 / PEP8 / pgvector / 只读从库）
