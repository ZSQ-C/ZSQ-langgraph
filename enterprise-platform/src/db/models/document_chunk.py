"""
文档切片表 ORM 模型

v3.0: 增加 page_number / structure_type / parent_heading 溯源字段
"""

try:
    from pgvector.sqlalchemy import Vector
    EMBEDDING_TYPE = Vector(1024)
except ImportError:
    EMBEDDING_TYPE = Text

from sqlalchemy import Column, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

JSON_TYPE = JSON
UUID_TYPE = String(36)

from src.db.database import Base


class DocumentChunk(Base):
    """文档切片表"""
    __tablename__ = "document_chunks"

    document_id = Column(
        UUID_TYPE,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属文档ID",
    )
    chunk_index = Column(Integer, nullable=False, comment="切片序号")
    content = Column(Text, nullable=False, comment="切片文本")
    embedding = Column(EMBEDDING_TYPE, comment="BGE-M3嵌入向量(PG:Vector/SQLite:TEXT)")
    page_number = Column(Integer, comment="原文档页码（溯源）")
    structure_type = Column(
        String(50), comment="heading/paragraph/table/figure_caption"
    )
    parent_heading = Column(String(500), comment="所属章节标题")
    metadata_ = Column("metadata", JSON_TYPE, default={}, comment="附加元数据")

    document = relationship("Document", back_populates="chunks")

    def __repr__(self):
        return f"<DocumentChunk(id={self.id}, doc_id={self.document_id}, chunk_index={self.chunk_index})>"
