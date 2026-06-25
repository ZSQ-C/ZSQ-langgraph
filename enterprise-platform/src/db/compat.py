"""
数据库类型兼容层 — 自动适配 MySQL / PostgreSQL / SQLite

MySQL 不支持 PostgreSQL 特有的 UUID/JSONB/ARRAY 类型，
此模块根据数据库 URL 自动选择合适的列类型。
"""
from config.settings import settings

_is_mysql = "mysql" in settings.admin_db_url.lower()
_is_postgres = "postgresql" in settings.admin_db_url.lower()

# UUID: PG用UUID, MySQL/SQLite用CHAR(36)
if _is_postgres:
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID
    UUIDType = PG_UUID(as_uuid=True)
else:
    from sqlalchemy import String
    UUIDType = String(36)

# JSON: PG用JSONB, MySQL/SQLite用JSON
if _is_postgres:
    from sqlalchemy.dialects.postgresql import JSONB
    JSONType = JSONB
else:
    from sqlalchemy import JSON
    JSONType = JSON

# ARRAY: PG用ARRAY, MySQL/SQLite用JSON
if _is_postgres:
    from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
    def ArrayType(item_type):
        return PG_ARRAY(item_type)
else:
    from sqlalchemy import JSON as _JSON
    def ArrayType(item_type):
        return _JSON

# Vector: PG用pgvector, MySQL/SQLite用TEXT存储JSON
if _is_postgres:
    try:
        from pgvector.sqlalchemy import Vector
        def VectorType(dim=1024):
            return Vector(dim)
    except ImportError:
        from sqlalchemy import Text
        def VectorType(dim=1024):
            return Text
else:
    from sqlalchemy import Text
    def VectorType(dim=1024):
        return Text
