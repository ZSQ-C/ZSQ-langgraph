"""统一LLM工厂 - v3.0 模型分级路由"""
from langchain_openai import ChatOpenAI
from config.settings import settings

def get_heavy_llm(temperature=None, max_tokens=None) -> ChatOpenAI:
    """主力模型 DeepSeek-V3 - Planner/Tool Agent/Summary用"""
    return ChatOpenAI(
        model=settings.deepseek_model_name,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=temperature if temperature is not None else settings.llm_temperature,
        max_tokens=max_tokens or settings.llm_max_tokens,
    )

def get_light_llm(max_tokens=1024) -> ChatOpenAI:
    """轻量模型 Qwen2.5-7B本地 - Router/合规审核/Critic格式化用"""
    return ChatOpenAI(
        model=settings.qwen_model_name,
        api_key=settings.qwen_api_key,
        base_url=settings.qwen_api_base,
        temperature=0.0,
        max_tokens=max_tokens,
    )

def get_sql_llm() -> ChatOpenAI:
    """SQL生成专用 - temperature=0确保稳定"""
    return get_heavy_llm(temperature=0.0)

def get_llm() -> ChatOpenAI:
    """向后兼容默认"""
    return get_heavy_llm()
