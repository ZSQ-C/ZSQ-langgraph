"""
会话表 ORM模型

v3.0: 增加对话会话管理
"""

from sqlalchemy import Column, ForeignKey, String, Text

from src.db.database import Base


class Session(Base):
    """对话会话表"""
    __tablename__ = "sessions"

    title = Column(String(200), nullable=False, comment="会话标题")
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, comment="所属用户")
    thread_id = Column(String(100), nullable=False, unique=True, comment="LangGraph thread_id")
    status = Column(String(20), default="active", comment="状态: active/archived/deleted")
    last_message = Column(Text, comment="最后一条消息摘要")

    def __repr__(self):
        return f"<Session(id={self.id}, title={self.title}, user_id={self.user_id})>"
