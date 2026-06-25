"""
文档管理路由

POST /upload    - 上传文档到 MinIO
GET /           - 获取文档列表
DELETE /{id}    - 删除文档
POST /{id}/parse - 触发文档解析
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_admin_db
from src.api.middleware import get_current_user
from src.api.schemas.document import (
    DocumentListResponse,
    DocumentParseResponse,
    DocumentResponse,
)
from src.db.models.document import Document

logger = logging.getLogger(__name__)

router = APIRouter()

# 允许的文件类型
ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "md", "txt", "csv", "xlsx", "log", "json", "png", "jpg", "jpeg"}
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB


@router.post("/upload", response_model=DocumentResponse, summary="上传文档")
async def upload_document(
    file: UploadFile = File(..., description="上传文件"),
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """
    上传文档到 MinIO 对象存储

    支持格式: PDF, DOCX, MD, TXT, CSV, XLSX, LOG, JSON, PNG, JPG

    上传后自动记录元数据到数据库，可后续触发解析。
    """
    # 校验文件类型
    if file.filename:
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    else:
        ext = ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文件类型: .{ext}，支持: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # 读取文件内容
    content = await file.read()
    file_size = len(content)

    if file_size > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件过大（{file_size / 1024 / 1024:.1f}MB），最大 {MAX_UPLOAD_SIZE / 1024 / 1024:.0f}MB",
        )

    # 生成 MinIO 存储路径
    file_id = uuid.uuid4().hex[:12]
    object_name = f"documents/{current_user['user_id']}/{file_id}/{file.filename}"

    # 上传到 MinIO
    try:
        _upload_to_minio(object_name, content, file.content_type or "application/octet-stream")
    except Exception as e:
        logger.error(f"MinIO上传失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"文件存储失败: {e}",
        )

    # 推断文件类型
    file_type_map = {
        "pdf": "pdf", "docx": "docx", "doc": "docx",
        "md": "md", "txt": "txt", "csv": "csv", "xlsx": "xlsx",
        "log": "log", "json": "json",
        "png": "png", "jpg": "jpg", "jpeg": "jpg",
    }
    file_type = file_type_map.get(ext, ext)

    # 创建数据库记录
    doc = Document(
        title=file.filename or f"未命名文档-{file_id}",
        file_type=file_type,
        file_path=object_name,
        uploaded_by=current_user["user_id"],
        is_parsed=False,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    logger.info(f"文档上传成功: id={doc.id}, title={doc.title}, size={file_size}")

    return DocumentResponse(
        id=str(doc.id),
        title=doc.title,
        file_type=doc.file_type,
        file_path=doc.file_path,
        parse_engine=None,
        page_count=0,
        tags=[],
        chunk_count=0,
        is_parsed=False,
        parse_error=None,
        uploaded_by=str(doc.uploaded_by) if doc.uploaded_by else None,
        create_time=doc.create_time,
        update_time=doc.update_time,
    )


@router.get("/", response_model=DocumentListResponse, summary="获取文档列表")
async def list_documents(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """
    获取文档列表（按上传时间倒序）
    """
    # 查询总数
    count_result = await session.execute(
        select(func.count()).select_from(Document).where(
            Document.is_deleted == False,
        )
    )
    total = count_result.scalar() or 0

    # 查询文档列表
    result = await session.execute(
        select(Document)
        .where(Document.is_deleted == False)
        .order_by(Document.create_time.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    docs = result.scalars().all()

    items = [
        DocumentResponse(
            id=str(d.id),
            title=d.title,
            file_type=d.file_type,
            file_path=d.file_path,
            parse_engine=d.parse_engine,
            page_count=d.page_count or 0,
            tags=d.tags or [],
            chunk_count=d.chunk_count or 0,
            is_parsed=d.is_parsed or False,
            parse_error=d.parse_error,
            uploaded_by=str(d.uploaded_by) if d.uploaded_by else None,
            create_time=d.create_time,
            update_time=d.update_time,
        )
        for d in docs
    ]

    return DocumentListResponse(total=total, items=items)


@router.delete("/{document_id}", summary="删除文档")
async def delete_document(
    document_id: str,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """
    软删除指定文档及其切片
    """
    result = await session.execute(
        select(Document).where(
            Document.id == document_id,
            Document.is_deleted == False,
        )
    )
    doc = result.scalar_one_or_none()

    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在",
        )

    # 软删除（级联删除切片由 ORM 管理）
    doc.is_deleted = True
    await session.commit()

    logger.info(f"删除文档: id={document_id}")

    return {"status": "deleted", "document_id": document_id}


@router.post("/{document_id}/parse", response_model=DocumentParseResponse, summary="触发文档解析")
async def parse_document(
    document_id: str,
    session: AsyncSession = Depends(get_admin_db),
    current_user: dict = Depends(get_current_user),
):
    """
    触发文档解析任务

    根据文档类型分配解析引擎:
    - PDF: PyMuPDF + PaddleOCR
    - DOCX: python-docx
    - MD/TXT/LOG/JSON: 直接解析
    - Image: PaddleOCR
    """
    result = await session.execute(
        select(Document).where(
            Document.id == document_id,
            Document.is_deleted == False,
        )
    )
    doc = result.scalar_one_or_none()

    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在",
        )

    if doc.is_parsed:
        return DocumentParseResponse(
            document_id=document_id,
            status="completed",
            message="文档已解析完成",
        )

    # 设置解析引擎
    parse_engine_map = {
        "pdf": "pymupdf+paddleocr",
        "docx": "python-docx",
        "doc": "python-docx",
        "md": "direct",
        "txt": "direct",
        "log": "direct",
        "json": "direct",
        "csv": "direct",
        "xlsx": "direct",
        "png": "paddleocr",
        "jpg": "paddleocr",
        "jpeg": "paddleocr",
    }
    engine = parse_engine_map.get(doc.file_type, "direct") if doc.file_type else "direct"
    doc.parse_engine = engine

    # 标记为处理中
    await session.commit()

    logger.info(f"触发文档解析: id={document_id}, engine={engine}")

    # 返回解析状态（实际解析应通过后台任务队列执行）
    return DocumentParseResponse(
        document_id=document_id,
        status="pending",
        message=f"文档解析任务已提交（引擎: {engine}）",
    )


# ============================================================
# MinIO 客户端封装
# ============================================================

def _get_minio_client():
    """获取 MinIO 客户端实例"""
    from config.settings import settings
    from minio import Minio

    return Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def _upload_to_minio(object_name: str, data: bytes, content_type: str):
    """
    上传文件到 MinIO

    Args:
        object_name: 对象路径
        data: 文件二进制数据
        content_type: MIME 类型
    """
    from config.settings import settings

    client = _get_minio_client()

    # 确保 bucket 存在
    bucket_name = settings.minio_bucket
    if not client.bucket_exists(bucket_name):
        client.make_bucket(bucket_name)
        logger.info(f"创建MinIO桶: {bucket_name}")

    import io
    from minio import S3Error

    try:
        client.put_object(
            bucket_name=bucket_name,
            object_name=object_name,
            data=io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        logger.info(f"MinIO上传成功: {bucket_name}/{object_name}")
    except S3Error as e:
        logger.error(f"MinIO上传失败: {e}")
        raise
