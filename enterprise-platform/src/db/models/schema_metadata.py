"""
Schema元数据表 ORM模型

v3.0: 增加向量字段（PostgreSQL用pgvector Vector，SQLite用TEXT存储JSON）
"""
from sqlalchemy import Boolean, Column, String, Text, UniqueConstraint

try:
    from pgvector.sqlalchemy import Vector
    EMBEDDING_TYPE = Vector(1024)
except ImportError:
    EMBEDDING_TYPE = Text  # SQLite降级：存储JSON格式向量

from src.db.database import Base


class SchemaMetadata(Base):
    """Schema元数据表"""
    __tablename__ = "schema_metadata"
    __table_args__ = (
        UniqueConstraint("table_name", "column_name", name="uq_table_column"),
    )

    table_name = Column(String(200), nullable=False, comment="表名")
    column_name = Column(String(200), nullable=False, comment="列名")
    data_type = Column(String(50), nullable=False, comment="数据类型")
    description = Column(Text, nullable=False, comment="业务含义描述")
    is_sensitive = Column(Boolean, default=False, comment="是否敏感字段")
    sample_values = Column(Text, comment="示例值，逗号分隔")
    table_comment = Column(Text, comment="表级别注释")
    embedding = Column(EMBEDDING_TYPE, comment="BGE-M3嵌入向量(PG:Vector/SQLite:TEXT)")

    def __repr__(self):
        return f"<SchemaMetadata(table={self.table_name}, column={self.column_name})>"