"""
角色权限表 ORM模型

权限数据以JSONB格式存储，支持灵活扩展：
- table_permissions: 表级权限
- field_permissions: 字段级权限
- row_conditions: 行级条件
v3.0: 增加 doc_tags_allowed 字段
"""

from sqlalchemy import Boolean, Column, Integer, String, JSON
from src.db.database import Base


class Role(Base):
    """角色权限表"""

    role_name = Column(String(50), unique=True, nullable=False, comment="角色名称")
    table_permissions = Column(JSON, nullable=False, default=dict, comment="表级权限")
    field_permissions = Column(JSON, nullable=False, default=dict, comment="字段级权限")
    row_conditions = Column(JSON, nullable=False, default=dict, comment="行级数据范围")
    doc_tags_allowed = Column(JSON, default=[], comment="可访问文档标签(JSON数组)")
    can_export = Column(Boolean, default=False, comment="是否可导出数据")
    max_query_rows = Column(Integer, default=1000, comment="单次查询最大行数")

    def __repr__(self):
        return f"<Role(id={self.id}, role_name={self.role_name})>"