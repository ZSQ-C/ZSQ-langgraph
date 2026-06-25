"""
统一配置入口 - 使用 Pydantic Settings 管理所有环境变量
v3.0: 增加 Qwen 本地模型、BGE-M3、MinIO、文档解析配置
"""

from typing import Literal
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用全局配置"""

    # ========== 数据库配置 ==========
    read_only_db_url: str = "postgresql+asyncpg://eda_admin:eda_admin_123@localhost:5432/eda_platform"
    admin_db_url: str = "postgresql+asyncpg://eda_admin:eda_admin_123@localhost:5432/eda_platform"

    # ========== Redis 配置 ==========
    redis_url: str = "redis://localhost:6379/0"

    # ========== MinIO 配置 ★ ==========
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "eda-documents"
    minio_secure: bool = False

    # ========== 主力 LLM 配置（DeepSeek-V3）==========
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model_name: str = "deepseek-chat"

    # ========== 轻量 LLM 配置（Qwen2.5-7B 本地）★ ==========
    qwen_api_base: str = "http://localhost:8001/v1"
    qwen_model_name: str = "qwen2.5-7b-instruct"
    qwen_api_key: str = "not-needed"

    # ========== 嵌入模型配置（BGE-M3 本地）★ ==========
    bge_api_base: str = "http://localhost:8002/v1"
    bge_model_name: str = "bge-m3"

    # ========== LLM 通用配置 ==========
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096

    # ========== 安全配置 ==========
    sql_timeout_seconds: int = 30
    sql_max_retry: int = 2
    sql_max_rows: int = 1000
    sql_explain_cost_threshold: int = 50000       # ★ EXPLAIN 代价阈值
    sensitive_fields: list[str] = ["phone", "id_card", "email", "bank_account", "password"]

    # ========== JWT 配置 ★ ==========
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    # ========== Langfuse 可观测 ==========
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # ========== 应用配置 ==========
    app_name: str = "企业智能数据分析平台"
    debug: bool = False

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


# 全局单例
settings = Settings()
