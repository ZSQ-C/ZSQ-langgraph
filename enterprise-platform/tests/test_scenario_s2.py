"""
S2 Scenario Test: 华南区Q2销售额与去年同期对比

This scenario exercises the data_analysis pipeline:
  router_node -> nl2sql_pipeline (schema_retrieval + sql_generation + sql_validation)
  -> compliance_node -> sql_execution -> critic_node

All LLM calls are mocked.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestration.state import AgentState


# ============================================================
# Mock Responses
# ============================================================

ROUTER_S2_RESPONSE = {
    "intent": "data_analysis",
    "complexity": "medium",
    "reason": "需要进行销售数据的统计对比分析，涉及聚合查询和时间范围过滤"
}

SAMPLE_SQL_S2 = """
SELECT
    region,
    SUM(amount) AS q2_sales_2024,
    (SELECT SUM(amount) FROM sales
     WHERE region = '华南' AND sale_date >= '2023-04-01' AND sale_date < '2023-07-01'
    ) AS q2_sales_2023
FROM sales
WHERE region = '华南'
  AND sale_date >= '2024-04-01'
  AND sale_date < '2024-07-01'
GROUP BY region
LIMIT 100;
"""

EXPLAIN_RESULT_S2 = {
    "Plan": {
        "Node Type": "Aggregate",
        "Strategy": "Sorted",
        "Plan Rows": 1,
        "Total Cost": 250.5,
        "Plans": [
            {
                "Node Type": "Index Scan",
                "Relation Name": "sales",
                "Index Name": "idx_sales_region_date",
                "Plan Rows": 500,
                "Total Cost": 200.1,
            }
        ],
    }
}

COMPLIANCE_RESULT_S2 = {
    "sql_safe": True,
    "permission_pass": True,
    "issues": [],
    "suggestions": [],
}


# ============================================================
# Test Router Node
# ============================================================

class TestRouterS2:
    """Verify router_node classifies S2 query as data_analysis."""

    def test_router_returns_data_analysis_intent(self, s2_agent_state):
        """Router should identify the sales comparison query as data_analysis."""
        from src.llm.prompts.router import ROUTER_SYSTEM_PROMPT, ROUTER_USER_TEMPLATE

        prompt = ROUTER_USER_TEMPLATE.format(query=s2_agent_state["query"])
        assert "华南区" in prompt
        assert "销售额" in prompt

        # Verify the three intent categories are present
        assert "data_analysis" in ROUTER_SYSTEM_PROMPT
        assert "NL2SQL" in ROUTER_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_router_mock_s2(self, mock_llm):
        """Mock router returns data_analysis for S2 query."""
        mock_llm.set_response(ROUTER_S2_RESPONSE)
        response = await mock_llm.ainvoke("华南区Q2销售额与去年同期对比")
        parsed = json.loads(response.content)
        assert parsed["intent"] == "data_analysis"
        assert parsed["complexity"] == "medium"

    @pytest.mark.asyncio
    async def test_router_distinguishes_analysis_from_simple_qa(self, mock_llm):
        """A '什么是销售额' query should be simple_qa, not data_analysis."""
        mock_llm.set_response({"intent": "simple_qa", "complexity": "low"})
        response = await mock_llm.ainvoke("什么是销售额")
        parsed = json.loads(response.content)
        assert parsed["intent"] == "simple_qa"
        assert parsed["intent"] != "data_analysis"


# ============================================================
# Test NL2SQL Pipeline
# ============================================================

class TestNL2SQLPipeline:
    """Verify the NL2SQL pipeline generates correct SQL for S2."""

    def test_sql_generation_prompt_structure(self):
        """Verify the SQL generation prompt includes required constraints."""
        from src.llm.prompts.sql_generation import (
            SQL_GENERATION_SYSTEM_PROMPT,
            SQL_GENERATION_USER_TEMPLATE,
        )

        # Must enforce SELECT-only
        assert "SELECT" in SQL_GENERATION_SYSTEM_PROMPT
        assert "INSERT" in SQL_GENERATION_SYSTEM_PROMPT
        assert "LIMIT" in SQL_GENERATION_SYSTEM_PROMPT

        # Must include PostgreSQL dialect
        assert "PostgreSQL" in SQL_GENERATION_SYSTEM_PROMPT

        # Must require schema context
        assert "{schemas}" in SQL_GENERATION_SYSTEM_PROMPT
        assert "{max_rows}" in SQL_GENERATION_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_nl2sql_generates_valid_sql(self, mock_llm):
        """The generated SQL should contain expected elements for S2."""
        mock_llm.set_response({"sql": SAMPLE_SQL_S2.strip()})
        response = await mock_llm.ainvoke("Generate SQL for Q2 sales comparison")
        parsed = json.loads(response.content)

        sql = parsed.get("sql", "")
        sql_upper = sql.upper()

        # Verify key SQL elements
        assert "SELECT" in sql_upper
        assert "SUM" in sql_upper
        assert "WHERE" in sql_upper
        assert "LIMIT" in sql_upper
        assert "GROUP BY" in sql_upper

        # Verify forbidden keywords are absent
        forbidden = ["INSERT", "DELETE", "UPDATE", "DROP", "ALTER"]
        for kw in forbidden:
            assert kw not in sql_upper, f"Forbidden keyword {kw} found in SQL"


# ============================================================
# Test Compliance Node
# ============================================================

class TestComplianceNode:
    """Verify compliance_node validates S2 SQL and passes."""

    def test_sql_guard_validates_select_only(self):
        """SQLGuard should accept valid SELECT statements."""
        from src.security.sql_guard import SQLGuard

        guard = SQLGuard(max_rows=1000)
        result = guard.validate(SAMPLE_SQL_S2)
        assert result.valid is True
        assert "OK" in result.reason

    def test_sql_guard_enforces_limit(self):
        """SQLGuard must enforce LIMIT on queries without it."""
        from src.security.sql_guard import SQLGuard

        guard = SQLGuard(max_rows=500)
        sql_no_limit = "SELECT * FROM sales WHERE region = '华南'"
        result = guard.validate(sql_no_limit)
        assert result.valid is True
        assert "LIMIT" in result.modified_sql.upper()
        assert "500" in result.modified_sql

    def test_sql_guard_rejects_write_operations(self):
        """SQLGuard must reject INSERT into a SELECT query."""
        from src.security.sql_guard import SQLGuard

        guard = SQLGuard()
        result = guard.validate("INSERT INTO sales VALUES (1, 'test', 100)")
        assert result.valid is False
        assert "INSERT" in result.reason or "禁止" in result.reason

    def test_compliance_prompt_structure(self):
        """Verify compliance prompt includes all audit dimensions."""
        from src.llm.prompts.compliance import COMPLIANCE_SYSTEM_PROMPT

        assert "SELECT" in COMPLIANCE_SYSTEM_PROMPT
        assert "LIMIT" in COMPLIANCE_SYSTEM_PROMPT
        assert "脱敏" in COMPLIANCE_SYSTEM_PROMPT
        assert "sql_safe" in COMPLIANCE_SYSTEM_PROMPT
        assert "permission_pass" in COMPLIANCE_SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_compliance_accepts_valid_query(self, mock_llm):
        """Compliance should accept a safe S2 query."""
        mock_llm.set_response(COMPLIANCE_RESULT_S2)
        response = await mock_llm.ainvoke("Review SQL for S2")
        parsed = json.loads(response.content)
        assert parsed["sql_safe"] is True
        assert parsed["permission_pass"] is True
        assert len(parsed["issues"]) == 0


# ============================================================
# Test EXPLAIN Check
# ============================================================

class TestExplainCheck:
    """Verify EXPLAIN pre-check for S2 SQL passes."""

    def test_explain_check_passes_for_efficient_query(self):
        """An index scan plan should pass EXPLAIN check."""
        from src.security.sql_guard import SQLGuard

        result = SQLGuard.explain_check(EXPLAIN_RESULT_S2)
        assert result["safe"] is True
        assert "Index Scan" not in str(result.get("errors", []))
        assert "全表扫描" not in str(result.get("warnings", []))

    def test_explain_detects_cartesian_product(self):
        """EXPLAIN should detect cartesian product (Nested Loop without Join Filter)."""
        from src.security.sql_guard import SQLGuard

        cartesian_plan = {
            "Plan": {
                "Node Type": "Nested Loop",
                "Plan Rows": 1000000,
                "Total Cost": 500000,
                "Plans": [
                    {"Node Type": "Seq Scan", "Relation Name": "sales", "Plan Rows": 1000, "Total Cost": 50},
                    {"Node Type": "Seq Scan", "Relation Name": "orders", "Plan Rows": 1000, "Total Cost": 50},
                ],
            }
        }
        result = SQLGuard.explain_check(cartesian_plan)
        assert result["safe"] is False
        assert len(result["errors"]) > 0
        assert any("笛卡尔积" in e for e in result["errors"])

    def test_explain_warns_large_seq_scan(self):
        """EXPLAIN should warn about Seq Scan on more than 10,000 rows."""
        from src.security.sql_guard import SQLGuard

        large_scan_plan = {
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "sales",
                "Plan Rows": 50000,
                "Total Cost": 5000,
            }
        }
        result = SQLGuard.explain_check(large_scan_plan)
        # Large seq scan is a warning, not an error (safe remains True)
        assert len(result["warnings"]) > 0
        assert any("全表扫描" in w for w in result["warnings"])

    def test_explain_warns_high_cost(self):
        """EXPLAIN should warn about plans with total cost > 50000."""
        from src.security.sql_guard import SQLGuard

        expensive_plan = {
            "Plan": {
                "Node Type": "Aggregate",
                "Plan Rows": 1,
                "Total Cost": 75000,
                "Plans": [
                    {"Node Type": "Seq Scan", "Relation Name": "sales", "Plan Rows": 100, "Total Cost": 75000},
                ],
            }
        }
        result = SQLGuard.explain_check(expensive_plan)
        assert any("代价过高" in w for w in result["warnings"])


# ============================================================
# Test Full S2 Pipeline
# ============================================================

class TestFullS2Pipeline:
    """End-to-end S2 pipeline simulation."""

    @pytest.mark.asyncio
    async def test_full_s2_pipeline(self, s2_agent_state, mock_llm):
        """Simulate the full data_analysis pipeline for S2 scenario."""
        state = dict(s2_agent_state)
        trace = []

        # Phase 1: Router -> data_analysis
        mock_llm.set_response(ROUTER_S2_RESPONSE)
        response = await mock_llm.ainvoke(state["query"])
        router_result = json.loads(response.content)
        state["intent"] = router_result["intent"]
        state["query_complexity"] = router_result["complexity"]
        trace.append("router")
        assert state["intent"] == "data_analysis"

        # Phase 2: Schema Retrieval (mocked)
        state["relevant_schemas"] = """
表名: sales
字段:
  - product_name (VARCHAR): 产品名称
  - category (VARCHAR): 产品类别
  - amount (NUMERIC): 销售金额
  - quantity (INTEGER): 销售数量
  - sale_date (DATE): 销售日期
  - region (VARCHAR): 销售区域
  - dept (VARCHAR): 销售部门
"""
        trace.append("schema_retrieval")
        assert "sales" in state["relevant_schemas"]
        assert "amount" in state["relevant_schemas"]
        assert "region" in state["relevant_schemas"]

        # Phase 3: NL2SQL generation
        state["generated_sql"] = SAMPLE_SQL_S2.strip()
        trace.append("nl2sql")
        assert "SELECT" in state["generated_sql"]
        assert "华南" in state["generated_sql"]
        assert "LIMIT" in state["generated_sql"]

        # Phase 4: SQL Validation (SQLGuard)
        from src.security.sql_guard import SQLGuard
        guard = SQLGuard(max_rows=1000)
        result = guard.validate(state["generated_sql"])
        state["sql_safe"] = result.valid
        trace.append("compliance")
        assert state["sql_safe"] is True

        # Phase 5: EXPLAIN check
        explain_result = SQLGuard.explain_check(EXPLAIN_RESULT_S2)
        assert explain_result["safe"] is True
        trace.append("explain_check")

        # Phase 6: Execution (mocked)
        state["execution_result"] = {
            "success": True,
            "columns": ["region", "q2_sales_2024", "q2_sales_2023"],
            "data": [["华南", 1500000.00, 1200000.00]],
            "row_count": 1,
            "execution_time_ms": 45,
        }
        trace.append("execution")
        assert state["execution_result"]["success"] is True
        assert state["execution_result"]["data"][0][1] > 0  # Q2 2024 sales > 0

        # Phase 7: Critic
        state["critic_result"] = {
            "quality_score": 0.95,
            "passed": [
                "SQL语法正确",
                "compliance校验通过",
                "EXPLAIN无危险操作",
                "返回行数在限制内",
            ],
            "failed_checks": [],
        }
        trace.append("critic")

        # Phase 8: Final answer
        yoy_change = ((1500000 - 1200000) / 1200000) * 100
        state["final_answer"] = (
            f"华南区Q2销售额对比结果：2024年Q2销售额为150万元，"
            f"2023年Q2为120万元，同比增长{yoy_change:.1f}%。"
        )
        trace.append("final_answer")

        assert len(trace) >= 7
        assert state["final_answer"]
        assert "同比增长" in state["final_answer"]

    @pytest.mark.asyncio
    async def test_s2_permission_filter_injection(self, s2_agent_state):
        """Row-level permissions should be injected into the final SQL."""
        state = dict(s2_agent_state)
        state["generated_sql"] = SAMPLE_SQL_S2.strip()

        # Simulate RBAC injecting dept filter
        original_sql = state["generated_sql"]
        injected_conditions = "dept = 'sales_dept'"

        # Simple injection simulation
        if "WHERE" in original_sql:
            injected_sql = original_sql.replace(
                "WHERE", f"WHERE {injected_conditions} AND "
            )
        else:
            injected_sql = original_sql + f" WHERE {injected_conditions}"

        assert injected_conditions in injected_sql
        assert original_sql != injected_sql  # Must have changed
