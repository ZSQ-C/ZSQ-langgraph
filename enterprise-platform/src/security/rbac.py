"""
RBAC权限校验引擎

实现三维权限校验：
1. 表级权限：用户是否有该表的读取权限
2. 字段级权限：用户是否有该字段的访问权限
3. 行级数据范围：自动注入行级过滤条件

使用 admin_session 上下文管理器保证连接安全释放。
"""

from dataclasses import dataclass, field

from sqlalchemy import select

from src.db.database import admin_session
from src.db.models.role import Role
from src.db.models.user import User


@dataclass
class UserPermissions:
    """用户权限快照"""
    user_id: str
    dept: str
    role_name: str
    table_permissions: dict[str, list[str]] = field(default_factory=dict)
    field_permissions: dict[str, list[str]] = field(default_factory=dict)
    row_conditions: dict[str, str] = field(default_factory=dict)
    can_export: bool = False
    max_query_rows: int = 1000
    doc_tags_allowed: list[str] = field(default_factory=list)


class RBACEngine:
    """RBAC权限校验引擎"""

    def __init__(self):
        self._cache: dict[str, UserPermissions] = {}

    async def get_user_permissions(self, user_id: str) -> UserPermissions:
        """获取用户完整权限信息"""
        if user_id in self._cache:
            return self._cache[user_id]

        async with admin_session() as session:
            result = await session.execute(
                select(User, Role)
                .join(Role, User.role_id == Role.id)
                .where(User.id == user_id)
                .where(User.is_deleted == False)
            )
            row = result.one_or_none()
            if row is None:
                raise ValueError(f"用户不存在或已删除: {user_id}")

            user, role = row

            permissions = UserPermissions(
                user_id=str(user.id),
                dept=user.dept,
                role_name=role.role_name,
                table_permissions=role.table_permissions or {},
                field_permissions=role.field_permissions or {},
                row_conditions=role.row_conditions or {},
                can_export=role.can_export or False,
                max_query_rows=role.max_query_rows or 1000,
                doc_tags_allowed=role.doc_tags_allowed or [],
            )
            self._cache[user_id] = permissions
            return permissions

    async def check_table_access(self, user_id: str, table_name: str) -> bool:
        """检查用户是否有表的读取权限"""
        perms = await self.get_user_permissions(user_id)
        table_perms = perms.table_permissions.get(table_name, [])
        return "read" in table_perms

    async def check_field_access(self, user_id: str, table_name: str, field_name: str) -> bool:
        """检查用户是否有字段的读取权限"""
        perms = await self.get_user_permissions(user_id)
        fields = perms.field_permissions.get(table_name, [])
        if "*" in fields:
            return True
        return field_name in fields

    async def get_allowed_fields(self, user_id: str, table_name: str) -> list[str]:
        """获取用户对某表可访问的字段列表"""
        perms = await self.get_user_permissions(user_id)
        return perms.field_permissions.get(table_name, [])

    async def get_row_condition(self, user_id: str, table_name: str) -> str | None:
        """获取行级过滤条件，自动替换模板变量"""
        perms = await self.get_user_permissions(user_id)
        condition = perms.row_conditions.get(table_name)
        if condition is None or condition == "1=1":
            return None
        return condition.replace("{{user_dept}}", perms.dept)

    async def inject_row_conditions(self, user_id: str, sql: str) -> str:
        """
        向SQL注入行级权限条件

        策略：在WHERE子句中追加行级过滤条件
        """
        perms = await self.get_user_permissions(user_id)
        tables = self._extract_tables(sql)
        conditions = []
        for table in tables:
            cond = perms.row_conditions.get(table, "1=1")
            if cond and cond != "1=1":
                cond = cond.replace("{{user_dept}}", perms.dept)
                conditions.append(cond)

        if not conditions:
            return sql

        where_clause = " AND ".join(conditions)
        return self._inject_where(sql, where_clause)

    def _extract_tables(self, sql: str) -> set[str]:
        """从SQL中提取表名（简化实现）"""
        import sqlparse
        from sqlparse.tokens import Keyword, Name

        tables = set()
        from_seen = False
        join_seen = False

        for token in sqlparse.parse(sql)[0].flatten():
            if token.ttype is Keyword and token.value.upper() == "FROM":
                from_seen = True
                continue
            if token.ttype is Keyword and token.value.upper() in ("JOIN", "INNER JOIN", "LEFT JOIN", "RIGHT JOIN"):
                join_seen = True
                continue
            if (from_seen or join_seen) and token.ttype is Name:
                tables.add(token.value.strip('"'))
                from_seen = False
                join_seen = False

        return tables

    def _inject_where(self, sql: str, condition: str) -> str:
        """向SQL中注入WHERE条件"""
        sql_upper = sql.upper()
        where_pos = sql_upper.find("WHERE")

        if where_pos != -1:
            return sql[:where_pos + 5] + f" {condition} AND " + sql[where_pos + 5:]
        else:
            for keyword in ["GROUP BY", "ORDER BY", "LIMIT", "HAVING"]:
                pos = sql_upper.find(keyword)
                if pos != -1:
                    return sql[:pos] + f" WHERE {condition} " + sql[pos:]
            return sql.rstrip(";") + f" WHERE {condition}"

    async def check_document_access(self, user_id: str, doc_tags: list[str]) -> bool:
        perms = await self.get_user_permissions(user_id)
        allowed = set(perms.doc_tags_allowed)
        if not allowed or "*" in allowed:
            return True
        return all(tag in allowed for tag in doc_tags)

    async def filter_allowed_documents(self, user_id: str, chunks: list[dict]) -> list[dict]:
        perms = await self.get_user_permissions(user_id)
        allowed_tags = set(getattr(perms, 'doc_tags_allowed', []) or [])
        if not allowed_tags or "*" in allowed_tags:
            return chunks
        return [c for c in chunks if not c.get("metadata", {}).get("tags")
                or any(t in allowed_tags for t in c["metadata"]["tags"])]

    def clear_cache(self, user_id: str | None = None):
        """清除权限缓存"""
        if user_id:
            self._cache.pop(user_id, None)
        else:
            self._cache.clear()