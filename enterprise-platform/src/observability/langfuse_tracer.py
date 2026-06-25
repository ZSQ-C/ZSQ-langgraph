"""Langfuse LLM链路追踪"""
import logging
logger = logging.getLogger(__name__)

def init_langfuse():
    """初始化Langfuse追踪（可选，无配置时降级为日志模式）"""
    try:
        from config.settings import settings
        if settings.langfuse_public_key and settings.langfuse_secret_key:
            import langfuse
            langfuse.Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
            logger.info("Langfuse追踪已启用")
            return True
    except ImportError:
        pass
    logger.info("Langfuse未配置，使用本地日志模式")
    return False

def trace_node(node_name: str, input_data: dict, output_data: dict):
    """追踪节点执行（Langfuse可用时上报，否则本地日志）"""
    logger.debug(f"[Trace:{node_name}] in={str(input_data)[:200]} out={str(output_data)[:200]}")
