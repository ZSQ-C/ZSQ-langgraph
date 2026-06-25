"""
用户表 ORM模型

继承 Base 基类，自动获得 id / create_time / update_time / is_deleted
v3.0: 增加 password_hash 字段
"""

from sqlalchemy import Boolean, Column, ForeignKey, String
from sqlalchemy.orm import relationship

from src.db.database import Base


class User(Base):
    """用户表"""

    username = Column(String(100), unique=True, nullable=False, comment="用户名")
    password_hash = Column(String(255), nullable=False, comment="密码哈希")
    dept = Column(String(200), nullable=False, comment="部门")
    role_id = Column(String(36), ForeignKey("roles.id"), nullable=False, comment="关联角色")
    is_active = Column(Boolean, default=True, comment="是否启用")

    # 关联
    role = relationship("Role", backref="users", lazy="selectin")

    def __repr__(self):
        return f"<User(id={self.id}, username={self.username}, dept={self.dept})>"