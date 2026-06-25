"""
文档元数据表 ORM 模型

v3.0: 文档解析与管理
"""

from sqlalchemy import Boolean, Column, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from src.db.database import Base


class Document(Base):
    """文档元数据表"""
    __tablename__ = "documents"

    title = Column(String(500), nullable=False, comment="文档标题")
    file_type = Column(String(50), comment="pdf/docx/md/image/log")
    file_path = Column(String(1000), comment="MinIO 存储路径")
    parse_engine = Column(String(50), comment="使用的解析引擎")
    page_count = Column(Integer, default=0, comment="总页数")
    tags = Column(JSON, default=[], comment="文档标签")
    chunk_count = Column(Integer, default=0, comment="切片数量")
    is_parsed = Column(Boolean, default=False, comment="是否已解析")
    parse_error = Column(Text, comment="解析失败原因")
    uploaded_by = Column(String(36), comment="上传者")

    chunks = relationship(
        "DocumentChunk", back_populates="document", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Document(id={self.id}, title={self.title}, file_type={self.file_type})>"
