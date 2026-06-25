"""FastAPI 应用入口 - 企业智能数据分析平台"""
import time
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from config.settings import settings
from src.api.routes import auth, chat, sessions, documents, admin

# 配置控制台日志格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("eda-platform")


# ═══════════════════════════════════════════════════════════
# 请求追踪中间件 —— 展示每个请求的完整生命周期
# ═══════════════════════════════════════════════════════════
class RequestTraceMiddleware(BaseHTTPMiddleware):
    """记录每个HTTP请求的处理链路"""
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        method = request.method
        path = request.url.path
        client = request.client.host if request.client else "unknown"

        print(f"\n{'='*60}")
        print(f"  [接入层] {client} → {method} {path}")
        print(f"{'='*60}")

        response = await call_next(request)

        elapsed_ms = int((time.time() - start) * 1000)
        status = response.status_code
        print(f"  [接入层] ← {status} ({elapsed_ms}ms)")
        print(f"{'─'*60}\n")
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"\n{'#'*60}")
    print(f"#  {settings.app_name} v3.0.0")
    print(f"#  API 文档: http://localhost:8000/docs")
    print(f"#  前端页面: http://localhost:5173")
    print(f"#  健康检查: http://localhost:8000/api/health")
    print(f"{'#'*60}\n")
    yield
    try:
        from src.db.database import close_db
        await close_db()
    except Exception:
        pass


app = FastAPI(title=settings.app_name, version="3.0.0", lifespan=lifespan)

# 请求追踪中间件（最外层，记录所有请求）
app.add_middleware(RequestTraceMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(chat.router, prefix="/api/chat", tags=["对话"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["会话"])
app.include_router(documents.router, prefix="/api/documents", tags=["文档"])
app.include_router(admin.router, prefix="/api/admin", tags=["管理"])


# ═══════════════════════════════════════════════════════════
# 演示端点 —— 无需数据库，展示完整 Agent 处理链路
# ═══════════════════════════════════════════════════════════
@app.get("/api/health")
async def health():
    try:
        from src.db.database import check_db_health
        db_health = await check_db_health()
    except Exception:
        db_health = {"status": "unavailable"}
    return {"status": "ok", "database": db_health, "app": settings.app_name}


@app.get("/api/demo/chat")
async def demo_chat(query: str = "近三月服务器故障分析+优化方案+生成工单"):
    """
    演示端点(GET)：无需数据库，模拟完整的 Agent 处理链路。
    浏览器直接访问: http://localhost:8000/api/demo/chat?query=你的问题
    终端中打印每个节点的处理日志。
    """
    print(f"\n{'█'*60}")
    print(f"█  [演示模式] 用户输入: {query}")
    print(f"{'█'*60}")

    # Step 1: 路由
    print(f"  [1/5.路由Agent] → 分析意图... (模型: Qwen2.5-7B 轻量)")
    if "故障" in query or "工单" in query:
        intent, complexity = "complex_task", "high"
    elif "销售额" in query or "对比" in query:
        intent, complexity = "data_analysis", "medium"
    else:
        intent, complexity = "simple_qa", "low"
    print(f"  [1/5.路由Agent] ← intent={intent}, complexity={complexity}")

    # Step 2: Planner（仅复杂任务）
    if intent == "complex_task":
        print(f"  [2/5.Planner Agent] → 拆解任务... (模型: DeepSeek-V3 主力)")
        steps = [
            {"step_id":1, "desc":"检索故障文档", "tool":"rag_retrieval", "depends_on":[]},
            {"step_id":2, "desc":"查询服务器指标", "tool":"sql_query", "depends_on":[]},
            {"step_id":3, "desc":"生成故障原因分析", "tool":"llm", "depends_on":[1,2]},
            {"step_id":4, "desc":"生成优化方案", "tool":"llm", "depends_on":[3]},
            {"step_id":5, "desc":"生成运维工单", "tool":"ticket_report", "depends_on":[4]},
        ]
        for s in steps:
            print(f"  [2/5.Planner Agent]    step{s['step_id']}: {s['desc']} (tool={s['tool']})")
    elif intent == "data_analysis":
        print(f"  [2/5.NL2SQL管线] → Schema检索 → SQL生成 → 合规审核 → 执行 → 脱敏")
        steps = [{"step_id":1, "desc":"NL2SQL全链路", "tool":"nl2sql_pipeline"}]
    else:
        print(f"  [2/5.RAG管线] → HyDE增强 → 混合检索 → RRF融合 → Reranker → 回答")
        steps = [{"step_id":1, "desc":"RAG检索+回答", "tool":"rag_pipeline"}]

    # Step 3: Tool 执行
    if intent == "complex_task":
        print(f"  [3/5.Tool Agent] → 按拓扑序调度工具... (模型: DeepSeek-V3 主力)")
        for s in steps:
            print(f"  [3/5.Tool Agent]   执行 step{s['step_id']}: 调用 {s['tool']}")

    # Step 4: Critic
    print(f"  [4/5.Critic Agent] → 确定性溯源检查... (规则引擎 + Qwen2.5-7B)")
    checks = [
        ("RAG溯源(chunk_id+页码)", True),
        ("SQL溯源(table_name+row_count)", True),
        ("工具链完整性", True),
        ("数值一致性", True),
        ("权限合规", True),
        ("反幻觉(无编造)", True),
    ]
    passed = 0
    for name, ok in checks:
        mark = "[PASS]" if ok else "[FAIL]"
        if ok:
            passed += 1
        print(f"  [4/5.Critic Agent]   {mark} {name}")
    quality_score = passed / len(checks)
    print(f"  [4/5.Critic Agent] ← quality_score={quality_score:.2f}, passed={quality_score>=0.8}")

    # Step 5: Summary
    print(f"  [5/5.Summary Agent] → 汇总输出... (模型: DeepSeek-V3 主力)")
    result = {
        "intent": intent,
        "complexity": complexity,
        "steps_executed": len(steps),
        "quality_score": quality_score,
        "critic_passed": quality_score >= 0.8,
        "dummy_answer": f"这是 [{query}] 的模拟结果。数据库未连接，走的是演示模式。"
    }
    print(f"  [5/5.Summary Agent] ← 输出 {len(str(result))} 字节")
    print(f"{'█'*60}\n")
    return result
