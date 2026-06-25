"""
Security Tests: SQLGuard, RBAC, Audit.

Covers:
  - SQLGuard rejects DROP/INSERT/UPDATE/DELETE
  - SQLGuard enforces LIMIT
  - SQLGuard.explain_check detects cartesian product and other risks
  - RBAC table/field/row-level permissions
  - RBAC document tag filtering
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.security.sql_guard import SQLGuard, SQLValidationResult
from src.security.rbac import RBACEngine, UserPermissions


# ============================================================
# SQLGuard Tests
# ============================================================

class TestSQLGuardForbiddenOperations:
    """Verify SQLGuard rejects all dangerous SQL operations."""

    @pytest.fixture
    def guard(self):
        return SQLGuard(max_rows=1000)

    def test_reject_drop_table(self, guard):
        result = guard.validate("DROP TABLE sales")
        assert result.valid is False
        assert "DROP" in result.reason

    def test_reject_drop_database(self, guard):
        result = guard.validate("DROP DATABASE eda_platform")
        assert result.valid is False
        assert "DROP" in result.reason

    def test_reject_insert(self, guard):
        result = guard.validate("INSERT INTO sales VALUES (1, 'test', 100)")
        assert result.valid is False
        assert "INSERT" in result.reason

    def test_reject_update(self, guard):
        result = guard.validate("UPDATE sales SET amount = 0 WHERE id = 1")
        assert result.valid is False
        assert "UPDATE" in result.reason

    def test_reject_delete(self, guard):
        result = guard.validate("DELETE FROM sales WHERE id = 1")
        assert result.valid is False
        assert "DELETE" in result.reason

    def test_reject_alter_table(self, guard):
        result = guard.validate("ALTER TABLE sales ADD COLUMN new_col VARCHAR")
        assert result.valid is False
        assert "ALTER" in result.reason

    def test_reject_truncate(self, guard):
        result = guard.validate("TRUNCATE TABLE sales")
        assert result.valid is False
        assert "TRUNCATE" in result.reason

    def test_reject_create_table(self, guard):
        result = guard.validate("CREATE TABLE test (id INT)")
        assert result.valid is False
        assert "CREATE" in result.reason

    def test_reject_grant(self, guard):
        result = guard.validate("GRANT SELECT ON sales TO analyst")
        assert result.valid is False
        assert "禁止" in result.reason

    def test_reject_revoke(self, guard):
        result = guard.validate("REVOKE SELECT ON sales FROM analyst")
        assert result.valid is False
        assert "禁止" in result.reason

    def test_reject_copy(self, guard):
        result = guard.validate("COPY sales TO '/tmp/sales.csv'")
        assert result.valid is False
        assert "禁止" in result.reason

    def test_reject_exec(self, guard):
        result = guard.validate("EXEC sp_dangerous")
        assert result.valid is False
        assert "禁止" in result.reason

    def test_reject_execute(self, guard):
        result = guard.validate("EXECUTE dangerous_function()")
        assert result.valid is False
        assert "禁止" in result.reason

    def test_reject_vacuum(self, guard):
        result = guard.validate("VACUUM ANALYZE sales")
        assert result.valid is False
        assert "禁止" in result.reason


class TestSQLGuardLimitEnforcement:
    """Verify SQLGuard enforces LIMIT on all SELECT queries."""

    @pytest.fixture
    def guard(self):
        return SQLGuard(max_rows=500)

    def test_accept_with_existing_limit(self, guard):
        result = guard.validate("SELECT * FROM sales LIMIT 100")
        assert result.valid is True

    def test_add_limit_when_missing(self, guard):
        result = guard.validate("SELECT * FROM sales")
        assert result.valid is True
        assert "LIMIT 500" in result.modified_sql.upper()

    def test_add_limit_with_order_by(self, guard):
        result = guard.validate("SELECT * FROM sales ORDER BY amount DESC")
        assert result.valid is True
        assert "LIMIT 500" in result.modified_sql.upper()

    def test_add_limit_with_where(self, guard):
        result = guard.validate("SELECT * FROM sales WHERE dept = 'sales_dept'")
        assert result.valid is True
        assert "LIMIT 500" in result.modified_sql.upper()

    def test_add_limit_with_group_by(self, guard):
        result = guard.validate(
            "SELECT dept, SUM(amount) FROM sales GROUP BY dept"
        )
        assert result.valid is True
        assert "LIMIT 500" in result.modified_sql.upper()

    def test_preserve_existing_limit_when_present(self, guard):
        result = guard.validate("SELECT * FROM sales LIMIT 10")
        assert result.valid is True
        assert result.modified_sql == "SELECT * FROM sales LIMIT 10"

    def test_accept_cte_with(self, guard):
        result = guard.validate(
            "WITH monthly AS (SELECT dept, SUM(amount) AS total FROM sales "
            "GROUP BY dept) SELECT * FROM monthly ORDER BY total DESC LIMIT 10"
        )
        assert result.valid is True

    def test_reject_empty_sql(self, guard):
        result = guard.validate("")
        assert result.valid is False
        assert "SQL为空" in result.reason

    def test_reject_system_table_access(self, guard):
        """访问 pg_catalog 系统表必须被拒绝."""
        result = guard.validate("SELECT * FROM pg_catalog.pg_tables LIMIT 10")
        assert result.valid is False
        assert "系统表" in result.reason

    def test_reject_information_schema(self, guard):
        result = guard.validate("SELECT * FROM information_schema.tables LIMIT 10")
        assert result.valid is False
        assert "系统表" in result.reason


class TestSQLGuardExplainCheck:
    """Verify EXPLAIN plan analysis detects dangerous patterns."""

    def test_detect_cartesian_product(self):
        """Nested Loop without Join Filter is a cartesian product."""
        plan = {
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
        result = SQLGuard.explain_check(plan)
        assert result["safe"] is False
        assert len(result["errors"]) >= 1
        error_text = " ".join(result["errors"])
        assert "笛卡尔积" in error_text or "Nested Loop" in error_text

    def test_detect_large_seq_scan(self):
        """Seq Scan on > 10000 rows should trigger a warning."""
        plan = {
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "sales",
                "Plan Rows": 50000,
                "Total Cost": 8000,
            }
        }
        result = SQLGuard.explain_check(plan)
        assert len(result["warnings"]) >= 1
        assert any("sales" in w for w in result["warnings"])

    def test_detect_high_cost(self):
        """Total Cost > 50000 should trigger a warning."""
        plan = {
            "Plan": {
                "Node Type": "Aggregate",
                "Plan Rows": 100,
                "Total Cost": 75000,
                "Plans": [
                    {"Node Type": "Seq Scan", "Relation Name": "big_table", "Plan Rows": 500000, "Total Cost": 74000},
                ],
            }
        }
        result = SQLGuard.explain_check(plan)
        assert len(result["warnings"]) >= 1
        assert any("代价过高" in w or "75000" in w for w in result["warnings"])

    def test_passes_clean_index_scan(self):
        """An efficient index scan plan should pass all checks."""
        plan = {
            "Plan": {
                "Node Type": "Aggregate",
                "Strategy": "Plain",
                "Plan Rows": 10,
                "Total Cost": 120.5,
                "Plans": [
                    {
                        "Node Type": "Index Scan",
                        "Relation Name": "sales",
                        "Index Name": "idx_sales_region",
                        "Plan Rows": 500,
                        "Total Cost": 100.1,
                    }
                ],
            }
        }
        result = SQLGuard.explain_check(plan)
        assert result["safe"] is True
        assert len(result["errors"]) == 0

    def test_handles_nested_plans_recursively(self):
        """EXPLAIN check must recurse into nested plan trees."""
        plan = {
            "Plan": {
                "Node Type": "Hash Join",
                "Plan Rows": 100,
                "Total Cost": 3000,
                "Plans": [
                    {"Node Type": "Seq Scan", "Relation Name": "sales", "Plan Rows": 300, "Total Cost": 50},
                    {
                        "Node Type": "Hash",
                        "Plan Rows": 200,
                        "Total Cost": 100,
                        "Plans": [
                            {"Node Type": "Seq Scan", "Relation Name": "orders", "Plan Rows": 200, "Total Cost": 50},
                        ],
                    },
                ],
            }
        }
        result = SQLGuard.explain_check(plan)
        # Neither child is > 10000 rows, so no large seq scan warning
        assert result["safe"] is True

    def test_small_seq_scan_no_warning(self):
        """Seq Scan on a small table (< 10000 rows) should not warn."""
        plan = {
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "config",
                "Plan Rows": 50,
                "Total Cost": 10,
            }
        }
        result = SQLGuard.explain_check(plan)
        assert result["safe"] is True
        assert len(result["warnings"]) == 0

    def test_handles_array_input(self):
        """EXPLAIN check should handle array-wrapped JSON plans."""
        plan = [EXPLAIN_RESULT_S2]
        result = SQLGuard.explain_check(plan)
        assert "safe" in result


# ============================================================
# RBAC Tests
# ============================================================

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


class TestRBACTablePermissions:
    """Verify RBAC table-level access control."""

    @pytest.fixture
    def engine(self):
        return RBACEngine()

    @pytest.fixture
    def analyst_perms(self):
        return UserPermissions(
            user_id="analyst-001",
            dept="sales_dept",
            role_name="analyst",
            table_permissions={
                "sales": ["read"],
                "orders": ["read"],
            },
            field_permissions={
                "sales": ["product_name", "category", "amount", "sale_date", "dept"],
                "orders": ["order_no", "amount", "status"],
            },
            row_conditions={
                "sales": "dept = '{{user_dept}}'",
            },
            can_export=False,
            max_query_rows=1000,
            doc_tags_allowed=["sales_docs", "public"],
        )

    @pytest.mark.asyncio
    async def test_analyst_can_read_sales(self, engine, analyst_perms):
        engine._cache["analyst-001"] = analyst_perms
        can = await engine.check_table_access("analyst-001", "sales")
        assert can is True

    @pytest.mark.asyncio
    async def test_analyst_cannot_access_servers(self, engine, analyst_perms):
        engine._cache["analyst-001"] = analyst_perms
        can = await engine.check_table_access("analyst-001", "servers")
        assert can is False

    @pytest.mark.asyncio
    async def test_analyst_cannot_access_unknown_table(self, engine, analyst_perms):
        engine._cache["analyst-001"] = analyst_perms
        can = await engine.check_table_access("analyst-001", "financials")
        assert can is False

    @pytest.mark.asyncio
    async def test_table_permission_without_read(self, engine):
        perms = UserPermissions(
            user_id="limited-user",
            dept="sales_dept",
            role_name="limited",
            table_permissions={"sales": []},  # No read permission
            field_permissions={},
            row_conditions={},
            doc_tags_allowed=[],
        )
        engine._cache["limited-user"] = perms
        can = await engine.check_table_access("limited-user", "sales")
        assert can is False

    @pytest.mark.asyncio
    async def test_admin_can_read_all(self, engine, admin_permissions):
        engine._cache["admin-001"] = UserPermissions(
            user_id=admin_permissions["user_id"],
            dept=admin_permissions["dept"],
            role_name=admin_permissions["role_name"],
            table_permissions=admin_permissions["table_permissions"],
            field_permissions=admin_permissions["field_permissions"],
            row_conditions=admin_permissions["row_conditions"],
            can_export=admin_permissions["can_export"],
            max_query_rows=admin_permissions["max_query_rows"],
            doc_tags_allowed=admin_permissions["doc_tags_allowed"],
        )
        for table in ["sales", "servers", "tickets", "employees"]:
            can = await engine.check_table_access("admin-001", table)
            assert can is True, f"Admin should have access to {table}"


class TestRBACFieldPermissions:
    """Verify RBAC field-level access control."""

    @pytest.fixture
    def engine(self):
        return RBACEngine()

    @pytest.fixture
    def analyst_perms(self):
        return UserPermissions(
            user_id="analyst-001",
            dept="sales_dept",
            role_name="analyst",
            table_permissions={"sales": ["read"]},
            field_permissions={
                "sales": ["product_name", "category", "amount", "sale_date", "dept"],
            },
            row_conditions={},
            doc_tags_allowed=[],
        )

    @pytest.mark.asyncio
    async def test_analyst_can_see_allowed_fields(self, engine, analyst_perms):
        engine._cache["analyst-001"] = analyst_perms
        can_see = await engine.check_field_access("analyst-001", "sales", "amount")
        assert can_see is True

    @pytest.mark.asyncio
    async def test_analyst_cannot_see_sensitive_fields(self, engine, analyst_perms):
        engine._cache["analyst-001"] = analyst_perms
        can_see = await engine.check_field_access("analyst-001", "sales", "customer_phone")
        assert can_see is False

    @pytest.mark.asyncio
    async def test_analyst_cannot_see_customer_email(self, engine, analyst_perms):
        engine._cache["analyst-001"] = analyst_perms
        can_see = await engine.check_field_access("analyst-001", "sales", "customer_email")
        assert can_see is False

    @pytest.mark.asyncio
    async def test_admin_wildcard_access(self, engine, admin_permissions):
        engine._cache["admin-001"] = UserPermissions(
            user_id=admin_permissions["user_id"],
            dept=admin_permissions["dept"],
            role_name=admin_permissions["role_name"],
            table_permissions=admin_permissions["table_permissions"],
            field_permissions=admin_permissions["field_permissions"],
            row_conditions=admin_permissions["row_conditions"],
            can_export=admin_permissions["can_export"],
            max_query_rows=admin_permissions["max_query_rows"],
            doc_tags_allowed=admin_permissions["doc_tags_allowed"],
        )
        # Admin with "*" field permission can access any field
        can_see = await engine.check_field_access("admin-001", "sales", "any_field")
        assert can_see is True

    @pytest.mark.asyncio
    async def test_get_allowed_fields(self, engine, analyst_perms):
        engine._cache["analyst-001"] = analyst_perms
        fields = await engine.get_allowed_fields("analyst-001", "sales")
        assert "amount" in fields
        assert "customer_phone" not in fields
        assert len(fields) == 5


class TestRBACRowLevelConditions:
    """Verify RBAC row-level data filtering."""

    @pytest.fixture
    def engine(self):
        return RBACEngine()

    @pytest.fixture
    def analyst_perms(self):
        return UserPermissions(
            user_id="analyst-002",
            dept="sales_dept",
            role_name="analyst",
            table_permissions={"sales": ["read"]},
            field_permissions={"sales": ["*"]},
            row_conditions={"sales": "dept = '{{user_dept}}'"},
            doc_tags_allowed=[],
        )

    @pytest.fixture
    def admin_perms(self):
        return UserPermissions(
            user_id="admin-002",
            dept="it_dept",
            role_name="admin",
            table_permissions={"sales": ["read", "write"], "servers": ["read", "write"]},
            field_permissions={"sales": ["*"], "servers": ["*"]},
            row_conditions={
                "sales": "1=1",  # No row restriction for admin
            },
            doc_tags_allowed=["*"],
        )

    @pytest.mark.asyncio
    async def test_analyst_row_condition_injects_dept(self, engine, analyst_perms):
        engine._cache["analyst-002"] = analyst_perms
        condition = await engine.get_row_condition("analyst-002", "sales")
        assert condition is not None
        assert "sales_dept" in condition
        assert "{{user_dept}}" not in condition  # Template should be resolved

    @pytest.mark.asyncio
    async def test_admin_no_row_restriction(self, engine, admin_perms):
        engine._cache["admin-002"] = admin_perms
        condition = await engine.get_row_condition("admin-002", "sales")
        assert condition is None  # 1=1 means no restriction, returned as None

    @pytest.mark.asyncio
    async def test_row_condition_for_unknown_table_is_none(self, engine, analyst_perms):
        engine._cache["analyst-002"] = analyst_perms
        condition = await engine.get_row_condition("analyst-002", "unknown_table")
        assert condition is None


class TestRBACDocumentTagFiltering:
    """Verify RBAC document tag-based access control."""

    @pytest.fixture
    def engine(self):
        return RBACEngine()

    @pytest.fixture
    def analyst_perms(self):
        return UserPermissions(
            user_id="analyst-001",
            dept="sales_dept",
            role_name="analyst",
            table_permissions={},
            field_permissions={},
            row_conditions={},
            doc_tags_allowed=["sales_docs", "public"],
        )

    @pytest.fixture
    def admin_perms(self):
        return UserPermissions(
            user_id="admin-001",
            dept="it_dept",
            role_name="admin",
            table_permissions={},
            field_permissions={},
            row_conditions={},
            doc_tags_allowed=["*"],
        )

    @pytest.mark.asyncio
    async def test_analyst_can_access_public_docs(self, engine, analyst_perms):
        engine._cache["analyst-001"] = analyst_perms
        can = await engine.check_document_access("analyst-001", ["public"])
        assert can is True

    @pytest.mark.asyncio
    async def test_analyst_cannot_access_it_docs(self, engine, analyst_perms):
        engine._cache["analyst-001"] = analyst_perms
        can = await engine.check_document_access("analyst-001", ["it_internal"])
        assert can is False

    @pytest.mark.asyncio
    async def test_analyst_cannot_access_mixed_tags(self, engine, analyst_perms):
        engine._cache["analyst-001"] = analyst_perms
        can = await engine.check_document_access("analyst-001", ["public", "it_internal"])
        assert can is False  # One tag not allowed means denied

    @pytest.mark.asyncio
    async def test_admin_can_access_all_tags(self, engine, admin_perms):
        engine._cache["admin-001"] = admin_perms
        can = await engine.check_document_access("admin-001", ["it_internal", "secret"])
        assert can is True

    @pytest.mark.asyncio
    async def test_filter_allowed_documents_blocks_disallowed(self, engine, analyst_perms):
        engine._cache["analyst-001"] = analyst_perms
        chunks = [
            {"content": "public report", "metadata": {"tags": ["public"]}},
            {"content": "sales strategy", "metadata": {"tags": ["sales_docs"]}},
            {"content": "it roadmap", "metadata": {"tags": ["it_internal"]}},
            {"content": "no tags", "metadata": {}},
        ]
        filtered = await engine.filter_allowed_documents("analyst-001", chunks)
        assert len(filtered) == 3  # it_internal blocked
        allowed_tags_list = []
        for c in filtered:
            tags = c.get("metadata", {}).get("tags", [])
            allowed_tags_list.extend(tags)
        assert "it_internal" not in allowed_tags_list

    @pytest.mark.asyncio
    async def test_filter_all_admin_sees_everything(self, engine, admin_perms):
        engine._cache["admin-001"] = admin_perms
        chunks = [
            {"content": "a", "metadata": {"tags": ["public"]}},
            {"content": "b", "metadata": {"tags": ["it_internal"]}},
            {"content": "c", "metadata": {"tags": ["secret"]}},
        ]
        filtered = await engine.filter_allowed_documents("admin-001", chunks)
        assert len(filtered) == 3

    @pytest.mark.asyncio
    async def test_filter_all_with_no_tags_passes(self, engine, analyst_perms):
        engine._cache["analyst-001"] = analyst_perms
        chunks = [
            {"content": "clean doc", "metadata": {}},
            {"content": "another doc", "metadata": {"other": "stuff"}},
        ]
        filtered = await engine.filter_allowed_documents("analyst-001", chunks)
        assert len(filtered) == 2  # No tags means not blocked by tag filter


class TestRBACPermissionCaching:
    """Verify permission cache behavior."""

    @pytest.fixture
    def engine(self):
        return RBACEngine()

    @pytest.mark.asyncio
    async def test_cache_hit_returns_same_object(self, engine, analyst_permissions):
        perms = UserPermissions(
            user_id=analyst_permissions["user_id"],
            dept=analyst_permissions["dept"],
            role_name=analyst_permissions["role_name"],
            table_permissions=analyst_permissions["table_permissions"],
            field_permissions=analyst_permissions["field_permissions"],
            row_conditions=analyst_permissions["row_conditions"],
            can_export=analyst_permissions["can_export"],
            max_query_rows=analyst_permissions["max_query_rows"],
            doc_tags_allowed=analyst_permissions["doc_tags_allowed"],
        )
        uid = analyst_permissions["user_id"]
        engine._cache[uid] = perms
        p1 = await engine.get_user_permissions(uid)
        p2 = await engine.get_user_permissions(uid)
        assert p1 is p2  # Same object from cache

    def test_clear_cache_single_user(self, engine):
        uid = "test-user"
        engine._cache[uid] = UserPermissions(
            user_id=uid, dept="test", role_name="test",
            table_permissions={}, field_permissions={},
            row_conditions={}, doc_tags_allowed=[],
        )
        assert uid in engine._cache
        engine.clear_cache(uid)
        assert uid not in engine._cache

    def test_clear_cache_all(self, engine):
        engine._cache["u1"] = UserPermissions(
            user_id="u1", dept="d1", role_name="r1",
            table_permissions={}, field_permissions={},
            row_conditions={}, doc_tags_allowed=[],
        )
        engine._cache["u2"] = UserPermissions(
            user_id="u2", dept="d2", role_name="r2",
            table_permissions={}, field_permissions={},
            row_conditions={}, doc_tags_allowed=[],
        )
        assert len(engine._cache) == 2
        engine.clear_cache()
        assert len(engine._cache) == 0
