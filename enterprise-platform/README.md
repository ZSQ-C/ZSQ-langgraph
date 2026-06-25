# 企业智能数据分析平台 (Enterprise Data Analysis Platform)

基于 LangGraph 的多 Agent NL2SQL + RAG 智能数据分析平台，支持自然语言查询数据库、知识库检索、多步骤任务编排与人工审核。

## 架构图

```
                           用户
                            |
                     +------+------+
                     |   FastAPI   |  :8000
                     +------+------+
                            |
                 +----------+-----------+
                 |   LangGraph Graph    |
                 |  +----------------+  |
                 |  |  Router Node   |  |  意图分类
                 |  +-------+--------+  |
                 |          |           |
                 |  +-------+--------+  |
                 |  |  Planner Node  |  |  任务规划
                 |  +-------+--------+  |
                 |          |           |
                 |  +-------+--------+  |
                 |  |   Tool Node    |  |  工具执行
                 |  | (RAG/SQL/API)  |  |
                 |  +-------+--------+  |
                 |          |           |
                 |  +-------+--------+  |
                 |  | Compliance Nd  |  |  SQL安全+RBAC
                 |  +-------+--------+  |
                 |          |           |
                 |  +-------+--------+  |
                 |  |  Critic Node   |  |  质量评估+溯源
                 |  +----------------+  |
                 +----------------------+
                            |
          +-----------------+------------------+
          |                 |                   |
    +-----+-----+    +-----+-----+     +-------+------+
    | PostgreSQL |    |   Redis   |     |  MinIO       |
    | + pgvector |    | 缓存/状态  |     |  文档存储     |
    +-----+-----+    +-----------+     +--------------+
          |
    +-----+---------+
    | 模型服务        |
    | BGE-M3 (嵌入)  | :8002
    | Qwen2.5-7B     | :8001
    | DeepSeek-V3    | (API)
    +----------------+
```

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 编排引擎 | LangGraph 0.2+ | 多Agent状态图编排 |
| LLM 主力 | DeepSeek-V3 | 任务规划、SQL生成、总结 |
| LLM 轻量 | Qwen2.5-7B (本地 vLLM) | 路由、合规审核、Critic格式化 |
| 嵌入模型 | BGE-M3 (本地 TEI) | Schema语义检索、文档向量化 |
| 向量数据库 | pgvector (PostgreSQL 16) | Schema元数据 + 文档切片向量存储 |
| 关系数据库 | PostgreSQL 16 | 业务数据 + 平台元数据 |
| 缓存 | Redis 7 | 会话状态持久化 + 查询缓存 |
| 对象存储 | MinIO | 文档存储 (PDF/DOCX/MD/图片) |
| 安全 | sqlparse + RBAC | SQL白名单校验 + 行级权限 |
| 可观测 | Langfuse | LLM链路追踪 (可选) |
| 后端框架 | FastAPI + Uvicorn | REST API + SSE流式 |
| 文档解析 | PyMuPDF + Unstructured | PDF/DOCX多引擎解析 |
| OCR | PaddleOCR | 图片文字识别 |

## 快速开始

### 前置要求

- Docker & Docker Compose v2
- NVIDIA GPU (可选，用于本地运行 Qwen2.5-7B)
- DeepSeek API Key

### 一键启动

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY

# 2. 启动全部服务
docker-compose up -d

# 3. 初始化数据库 + 种子数据
docker exec eda-app python scripts/init_db.py
docker exec eda-app python scripts/seed_data.py

# 4. 验证服务
curl http://localhost:8000/health
```

### 本地开发

```bash
# 安装依赖
pip install poetry
poetry install

# 启动数据库和基础设施
docker-compose up -d postgres redis minio

# 启动嵌入服务 (需要GPU)
docker-compose up -d bge-m3

# 启动开发服务器
uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload

# 运行测试
python -m pytest tests/ -x -v
```

## 核心功能

### S1 场景：复杂多步骤任务

**输入**: "近三月服务器故障分析+优化方案+生成工单"

**执行流程**:
1. Router Node 识别意图为 `complex_task`，复杂度 `high`
2. Planner Node 拆解为 5 个有序步骤（RAG检索故障历史 -> SQL查询服务器指标 -> 检索优化文档 -> 综合生成方案 -> 创建工单）
3. Tool Node 按 DAG 依赖依次执行，无依赖步骤并行
4. Critic Node 校验 RAG 引用完整性（页码+相似度）+ SQL 溯源追踪 + 工单验证
5. 生成最终分析报告和工单链接

### S2 场景：数据查询分析

**输入**: "华南区Q2销售额与去年同期对比"

**执行流程**:
1. Router Node 识别意图为 `data_analysis`，复杂度 `medium`
2. Schema Retrieval 语义检索 sales 表结构
3. NL2SQL 生成 PostgreSQL 查询（含同比子查询）
4. Compliance Node 校验 SQL 安全（SELECT-only + LIMIT + RBAC）
5. EXPLAIN 预检（无笛卡尔积、无大表全扫）
6. 执行查询并格式化同比结果

### 安全机制

- **SQL 白名单**: 只允许 SELECT/WITH，禁止 DROP/INSERT/UPDATE/DELETE 等 13 类危险操作
- **强制 LIMIT**: 所有查询必须包含行数限制，防止全表返回
- **EXPLAIN 预检**: 执行前分析查询计划，检测笛卡尔积、大表全扫、高代价查询
- **RBAC 三维权限**: 表级（哪些表可查）+ 字段级（哪些列可见）+ 行级（自动注入部门过滤条件）
- **数据脱敏**: 手机号/身份证/邮箱/银行卡自动脱敏
- **文档标签过滤**: 基于文档标签的细粒度知识库访问控制

## 项目结构

```
enterprise-platform/
├── docker-compose.yml          # 完整服务编排 (PG16+Redis+MinIO+BGE+Qwen+App)
├── Dockerfile                  # FastAPI 应用镜像
├── pyproject.toml              # Poetry 依赖管理
├── .env / .env.example         # 环境变量配置
├── README.md                   # 项目文档
├── config/
│   └── settings.py             # Pydantic Settings 统一配置
├── src/
│   ├── api/
│   │   ├── app.py              # FastAPI 应用入口
│   │   ├── routes/             # API 路由
│   │   └── schemas/            # Pydantic 请求/响应模型
│   ├── agents/                 # Agent 定义
│   ├── orchestration/
│   │   ├── state.py            # AgentState TypedDict 定义
│   │   └── nodes/              # Graph 节点实现 (router/planner/tool/critic)
│   ├── llm/
│   │   ├── factory.py          # LLM 工厂 (DeepSeek/Qwen/BGE)
│   │   └── prompts/            # Prompt 模板
│   │       ├── router.py       # 路由分类 Prompt
│   │       ├── planner.py      # 任务规划 Prompt
│   │       ├── sql_generation.py # NL2SQL Prompt
│   │       ├── compliance.py   # 合规审核 Prompt
│   │       └── critic.py       # Critic 评估 Prompt
│   ├── tools/
│   │   ├── base.py             # 安全工具基类
│   │   ├── schema_retrieval.py # Schema 语义检索 (pgvector)
│   │   ├── sql_execution.py    # SQL 只读执行 (超时熔断)
│   │   ├── sql_validation.py   # SQL 安全校验
│   │   └── data_masking.py     # 数据脱敏
│   ├── security/
│   │   ├── sql_guard.py        # SQL 白名单校验 + EXPLAIN 预检
│   │   ├── rbac.py             # RBAC 三维权限引擎
│   │   └── audit.py            # 审计日志写入器
│   ├── db/
│   │   ├── database.py         # 异步引擎 + 连接池 + 重试机制
│   │   ├── models/             # ORM 模型
│   │   │   ├── user.py
│   │   │   ├── role.py
│   │   │   ├── schema_metadata.py
│   │   │   ├── document.py
│   │   │   ├── document_chunk.py
│   │   │   └── audit_log.py
│   │   └── repositories/       # 数据访问层
│   └── observability/
│       ├── __init__.py
│       └── langfuse_tracer.py  # Langfuse 链路追踪
├── scripts/
│   ├── init_db.py              # 数据库表初始化
│   ├── init_postgres.sql       # pgvector 扩展 + 索引
│   └── seed_data.py            # 测试种子数据
├── tests/
│   ├── conftest.py             # Pytest Fixtures
│   ├── test_scenario_s1.py     # S1 场景测试
│   ├── test_scenario_s2.py     # S2 场景测试
│   ├── test_security.py        # 安全模块测试
│   └── test_tools.py           # 工具模块测试
└── web/                        # 前端资源
```

## 环境变量配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `READ_ONLY_DB_URL` | 只读数据库连接串 | `postgresql+asyncpg://eda_admin:...@localhost:5432/eda_platform` |
| `ADMIN_DB_URL` | 管理数据库连接串 | 同上 |
| `REDIS_URL` | Redis 连接串 | `redis://localhost:6379/0` |
| `MINIO_ENDPOINT` | MinIO 地址 | `localhost:9000` |
| `MINIO_ACCESS_KEY` | MinIO 访问密钥 | `minioadmin` |
| `MINIO_SECRET_KEY` | MinIO 秘密密钥 | `minioadmin` |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | (必填) |
| `DEEPSEEK_BASE_URL` | DeepSeek API 地址 | `https://api.deepseek.com/v1` |
| `QWEN_API_BASE` | Qwen 本地服务地址 | `http://localhost:8001/v1` |
| `BGE_API_BASE` | BGE-M3 嵌入服务地址 | `http://localhost:8002/v1` |
| `JWT_SECRET_KEY` | JWT 签名密钥 | `change-me-in-production` |
| `SQL_TIMEOUT_SECONDS` | SQL 执行超时 (秒) | `30` |
| `SQL_MAX_ROWS` | 单次查询最大行数 | `1000` |
| `LANGFUSE_PUBLIC_KEY` | Langfuse 公钥 (可选) | (空) |
| `LANGFUSE_SECRET_KEY` | Langfuse 密钥 (可选) | (空) |

## API 接口速查

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `POST` | `/api/v1/chat` | 对话接口 (SSE 流式) |
| `POST` | `/api/v1/chat/stream` | 流式对话 (SSE) |
| `POST` | `/api/v1/documents/upload` | 上传文档 |
| `GET` | `/api/v1/documents` | 文档列表 |
| `POST` | `/api/v1/query/execute` | 直接执行 SQL (需审核) |
| `POST` | `/api/v1/tickets` | 创建工单 |
| `POST` | `/api/v1/auth/login` | 用户登录 |
| `GET` | `/api/v1/audit/logs` | 审计日志查询 |
| `GET` | `/api/v1/schema/tables` | 可查询表列表 |
