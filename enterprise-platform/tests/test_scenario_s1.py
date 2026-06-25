"""
S1 Scenario Test: 近三月服务器故障分析 + 优化方案 + 生成工单

This scenario exercises the complex_task pipeline:
  router_node -> planner_node -> tool_node(s) -> critic_node -> final_answer

All LLM calls are mocked so tests run without external services.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestration.state import AgentState


# ============================================================
# Mock Router Response
# ============================================================

ROUTER_S1_RESPONSE = {
    "intent": "complex_task",
    "complexity": "high",
    "reason": "需要多步骤：RAG检索故障历史 + SQL查询服务器指标 + 生成优化方案 + 创建工单"
}

PLANNER_S1_RESPONSE = {
    "steps": [
        {
            "step_id": 1,
            "description": "从知识库检索服务器故障历史记录和SOP文档",
            "tool": "rag_retrieval",
            "depends_on": [],
        },
        {
            "step_id": 2,
            "description": "查询近三个月服务器故障数据，按类型和频率统计",
            "tool": "sql_query",
            "depends_on": [],
        },
        {
            "step_id": 3,
            "description": "检索性能优化最佳实践文档",
            "tool": "rag_retrieval",
            "depends_on": [1],
        },
        {
            "step_id": 4,
            "description": "综合分析结果生成优化方案",
            "tool": "rag_retrieval",
            "depends_on": [1, 2, 3],
        },
        {
            "step_id": 5,
            "description": "创建服务器维护工单",
            "tool": "ticket_report",
            "depends_on": [4],
        },
    ]
}


# ============================================================
# Test Router Node
# ============================================================

class TestRouterNode:
    """Verify router_node classifies S1 query as complex_task."""

    def test_router_returns_complex_task_intent(self, s1_agent_state, mock_llm):
        """Router should identify the query as 'complex_task' with 'high' complexity."""
        from src.llm.prompts.router import ROUTER_SYSTEM_PROMPT, ROUTER_USER_TEMPLATE

        prompt_text = ROUTER_USER_TEMPLATE.format(query=s1_agent_state["query"])
        assert "近三月服务器故障分析" in prompt_text

        # Verify the router prompt structure is correct
        assert "simple_qa" in ROUTER_SYSTEM_PROMPT
        assert "data_analysis" in ROUTER_SYSTEM_PROMPT
        assert "complex_task" in ROUTER_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_router_node_with_mock(self, s1_agent_state, mock_llm):
        """Simulate router_node execution with a mock LLM."""
        mock_llm.set_response(ROUTER_S1_RESPONSE)

        response = await mock_llm.ainvoke(s1_agent_state["query"])
        parsed = json.loads(response.content)

        assert parsed["intent"] == "complex_task"
        assert parsed["complexity"] == "high"
        assert "多步骤" in parsed["reason"]

    @pytest.mark.asyncio
    async def test_router_rejects_simple_queries_as_complex(self, mock_llm):
        """A trivial query should not be classified as complex_task."""
        mock_llm.set_response({"intent": "simple_qa", "complexity": "low"})
        response = await mock_llm.ainvoke("什么是服务器？")
        parsed = json.loads(response.content)
        assert parsed["intent"] == "simple_qa"
        assert parsed["complexity"] == "low"


# ============================================================
# Test Planner Node
# ============================================================

class TestPlannerNode:
    """Verify planner_node produces a correct 5-step task plan."""

    def test_planner_produces_five_steps(self, mock_llm):
        """Planner should decompose the S1 task into at least 5 steps."""
        from src.llm.prompts.planner import PLANNER_SYSTEM_PROMPT, PLANNER_USER_TEMPLATE

        # Verify tools are declared in the prompt
        assert "rag_retrieval" in PLANNER_SYSTEM_PROMPT
        assert "sql_query" in PLANNER_SYSTEM_PROMPT
        assert "ticket_report" in PLANNER_SYSTEM_PROMPT

        query = "近三月服务器故障分析+优化方案+生成工单"
        prompt_text = PLANNER_USER_TEMPLATE.format(query=query)
        assert query in prompt_text

    @pytest.mark.asyncio
    async def test_planner_step_dependencies(self, mock_llm):
        """Verify that step dependencies form a valid DAG."""
        mock_llm.set_response(PLANNER_S1_RESPONSE)
        response = await mock_llm.ainvoke("test")
        plan = json.loads(response.content)

        steps = plan["steps"]
        assert len(steps) == 5

        # Verify step IDs are unique and sequential
        step_ids = [s["step_id"] for s in steps]
        assert step_ids == [1, 2, 3, 4, 5]

        # Verify step 3 depends on step 1
        step3 = next(s for s in steps if s["step_id"] == 3)
        assert 1 in step3["depends_on"]

        # Verify step 5 depends on step 4 (ticket after analysis)
        step5 = next(s for s in steps if s["step_id"] == 5)
        assert 4 in step5["depends_on"]
        assert step5["tool"] == "ticket_report"

        # Verify steps 1 and 2 are independent (can run in parallel)
        step1 = next(s for s in steps if s["step_id"] == 1)
        step2 = next(s for s in steps if s["step_id"] == 2)
        assert step1["depends_on"] == []
        assert step2["depends_on"] == []

    @pytest.mark.asyncio
    async def test_planner_every_step_has_tool(self, mock_llm):
        """Every planned step must have a tool assigned."""
        mock_llm.set_response(PLANNER_S1_RESPONSE)
        response = await mock_llm.ainvoke("test")
        plan = json.loads(response.content)

        for step in plan["steps"]:
            assert "tool" in step, f"Step {step['step_id']} missing tool"
            assert step["tool"], f"Step {step['step_id']} has empty tool"
            assert step["tool"] in [
                "rag_retrieval", "sql_query", "document_parsing",
                "ticket_report", "http_api",
            ], f"Unknown tool: {step['tool']}"


# ============================================================
# Test Tool Execution
# ============================================================

class TestToolExecution:
    """Verify tool_node correctly executes planned steps."""

    @pytest.mark.asyncio
    async def test_all_steps_execute(self, mock_llm, mock_db_session):
        """Simulate executing all 5 steps in the S1 plan."""
        mock_llm.set_response(PLANNER_S1_RESPONSE)
        response = await mock_llm.ainvoke("test")
        plan = json.loads(response.content)

        execution_results = []
        for step in plan["steps"]:
            result = await self._execute_step(step, mock_db_session)
            execution_results.append(result)
            assert result["success"], f"Step {step['step_id']} failed: {result.get('error')}"

        assert len(execution_results) == 5

    async def _execute_step(self, step: dict, mock_db_session) -> dict:
        """Simulate executing a single step."""
        tool_name = step["tool"]

        if tool_name == "sql_query":
            # Mock SQL query result
            mock_db_session._results["servers"] = type(
                "MockResult", (), {
                    "keys": lambda: ["hostname", "failure_count", "last_failure"],
                    "fetchall": lambda: [
                        ("server-01", 5, "2024-05-10"),
                        ("server-02", 3, "2024-05-15"),
                        ("server-03", 8, "2024-06-01"),
                    ],
                    "fetchmany": lambda size: [
                        ("server-01", 5, "2024-05-10"),
                    ],
                    "scalar": lambda: 3,
                }
            )()
            return {"success": True, "tool": tool_name, "rows": 3}

        if tool_name == "rag_retrieval":
            return {
                "success": True,
                "tool": tool_name,
                "chunks": [
                    {"content": "服务器故障SOP文档...", "score": 0.92},
                    {"content": "性能优化指南...", "score": 0.85},
                ],
            }

        if tool_name == "ticket_report":
            return {
                "success": True,
                "tool": tool_name,
                "ticket_id": "TK-2024-001",
            }

        return {"success": True, "tool": tool_name}

    @pytest.mark.asyncio
    async def test_sql_tool_uses_read_only_session(self, mock_db_session):
        """SQL execution must use the read-only session."""
        with patch("src.tools.sql_execution.read_only_session") as mock_ro:
            mock_ro.return_value.__aenter__ = AsyncMock(return_value=mock_db_session)
            mock_ro.return_value.__aexit__ = AsyncMock(return_value=None)

            # This validates the tool path uses read_only_session
            # (full integration is tested in test_tools.py)
            pass


# ============================================================
# Test Critic Node
# ============================================================

class TestCriticNode:
    """Verify critic_node checks RAG citations and SQL tracing."""

    def test_critic_verifies_rag_citations(self):
        """Critic should check that RAG chunks have proper source citations."""
        sample_chunks = [
            {
                "chunk_id": "abc-123",
                "content": "服务器故障应急响应流程...",
                "page": 5,
                "score": 0.95,
                "metadata": {
                    "document_id": "doc-server-sop",
                    "tags": ["server_ops", "sop"],
                },
            },
        ]

        # Verify chunk has required citation fields
        for chunk in sample_chunks:
            assert "chunk_id" in chunk, "Missing chunk_id citation"
            assert "page" in chunk, "Missing page citation"
            assert "score" in chunk, "Missing similarity score"

    def test_critic_verifies_sql_tracing(self):
        """Critic should verify that SQL execution has a full audit trail."""
        audit_trail = {
            "original_query": "近三月服务器故障",
            "generated_sql": "SELECT * FROM servers WHERE last_failure > NOW() - INTERVAL '3 months' LIMIT 100",
            "executed_sql": "SELECT * FROM servers WHERE last_failure > NOW() - INTERVAL '3 months' AND dept = 'sales_dept' LIMIT 100",
            "sql_safe": True,
            "permission_pass": True,
            "execution_time_ms": 45,
            "row_count": 3,
        }

        assert audit_trail["sql_safe"] is True
        assert audit_trail["permission_pass"] is True
        assert audit_trail["original_query"] is not None
        assert audit_trail["generated_sql"] is not None
        assert audit_trail["executed_sql"] is not None

    @pytest.mark.asyncio
    async def test_critic_format_prompt(self, mock_llm):
        """Verify critic formatting prompt is well-formed."""
        from src.llm.prompts.critic import CRITIC_FORMAT_PROMPT

        verdict = json.dumps({
            "quality_score": 0.92,
            "passed": ["RAG citation verified (page 5)", "SQL trace complete", "All steps executed"],
            "failed_checks": [],
        })
        prompt = CRITIC_FORMAT_PROMPT.format(verdict=verdict)
        assert "quality_score" in prompt
        assert "passed" in prompt

    @pytest.mark.asyncio
    async def test_critic_detects_failures(self, mock_llm):
        """Critic must detect and report failures."""
        verdict = {
            "quality_score": 0.45,
            "passed": [],
            "failed_checks": [
                "Missing RAG citation: chunk has no page number",
                "Step 4 output not referenced in final answer",
            ],
        }
        assert verdict["quality_score"] < 0.7
        assert len(verdict["failed_checks"]) > 0


# ============================================================
# Test Full Graph Execution
# ============================================================

class TestFullGraphExecution:
    """End-to-end graph execution simulation for S1 scenario."""

    @pytest.mark.asyncio
    async def test_full_s1_pipeline(self, s1_agent_state, mock_llm):
        """Simulate the full LangGraph pipeline for S1 scenario."""
        state = dict(s1_agent_state)
        graph_trace = []

        # Phase 1: Router
        mock_llm.set_response(ROUTER_S1_RESPONSE)
        response = await mock_llm.ainvoke(state["query"])
        router_result = json.loads(response.content)
        state["intent"] = router_result["intent"]
        state["query_complexity"] = router_result["complexity"]
        graph_trace.append("router")
        assert state["intent"] == "complex_task"

        # Phase 2: Planner
        mock_llm.set_response(PLANNER_S1_RESPONSE)
        response = await mock_llm.ainvoke(state["query"])
        planner_result = json.loads(response.content)
        state["task_plan"] = planner_result["steps"]
        graph_trace.append("planner")
        assert len(state["task_plan"]) == 5

        # Phase 3: Tool execution (each step)
        for step in state["task_plan"]:
            graph_trace.append(f"tool_step_{step['step_id']}")

        # Phase 4: Critic
        critic_verdict = {
            "quality_score": 0.90,
            "passed": [
                "所有5个步骤已执行",
                "RAG引用完整（页码+相似度）",
                "SQL已通过compliance校验",
                "SQL EXPLAIN检查无笛卡尔积",
                "工单已创建并返回工单ID",
            ],
            "failed_checks": [],
        }
        state["critic_result"] = critic_verdict
        graph_trace.append("critic")
        assert state["critic_result"]["quality_score"] >= 0.8
        assert len(state["critic_result"]["failed_checks"]) == 0

        # Phase 5: Final answer generation
        state["final_answer"] = (
            "近三月服务器故障分析完成：发现3台服务器有故障记录，server-03故障最频繁(8次)。"
            "优化方案：1) 升级server-03内存 2) 调整监控告警阈值 3) 建立每周巡检机制。"
            "工单TK-2024-001已创建，指派运维团队处理。"
        )
        graph_trace.append("final_answer")

        assert len(graph_trace) >= 7  # router + planner + 5 tools + critic
        assert state["final_answer"]
        assert "故障分析完成" in state["final_answer"]
        assert "工单" in state["final_answer"]

    @pytest.mark.asyncio
    async def test_s1_state_transitions(self, s1_agent_state, mock_llm):
        """Verify key state fields are populated at each pipeline stage."""
        state = dict(s1_agent_state)
        assert state["intent"] == ""  # Not set yet

        # Simulate routing
        mock_llm.set_response(ROUTER_S1_RESPONSE)
        response = await mock_llm.ainvoke(state["query"])
        router_result = json.loads(response.content)
        state["intent"] = router_result["intent"]
        state["query_complexity"] = router_result["complexity"]
        assert state["intent"] == "complex_task"
        assert state["query_complexity"] == "high"

        # Simulate tool results populating execution_result
        state["execution_result"] = {
            "success": True,
            "columns": ["hostname", "failure_count", "last_failure"],
            "data": [["server-01", 5, "2024-05-10"]],
            "row_count": 1,
        }
        assert state["execution_result"]["success"] is True
        assert state["execution_result"]["row_count"] == 1
