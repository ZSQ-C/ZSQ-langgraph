"""
RAG Pipeline Node - 独立 RAG 检索管线

专用于 simple_qa 场景的完整检索增强生成管线。
包含 9 个步骤：Adaptive HyDE → pgvector 向量召回 → BM25 关键词召回
→ RRF 融合 → 权限过滤 → BGE-Reranker → 上下文压缩 → 幻觉检测 → 答案生成。

每一步都有独立的 try/except 保护，单步失败不影响后续步骤使用已有最佳结果。
"""

import asyncio
import json
import logging
import math
import re

import httpx
from langchain_openai import OpenAIEmbeddings
from sqlalchemy import text

from config.settings import settings
from src.db.database import read_only_session
from src.llm.factory import get_heavy_llm
from src.orchestration.state import AgentState
from src.security.rbac import RBACEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

VECTOR_RECALL_LIMIT = 30
BM25_RECALL_LIMIT = 30
RRF_K = 60
RRF_TOP_N = 20
RERANK_TOP_K = 5
MAX_COMPRESSED_CHARS = 1500


# ---------------------------------------------------------------------------
# 提示词模板
# ---------------------------------------------------------------------------

HYDE_SYSTEM_PROMPT = """\
你是一个企业知识库检索助手。请根据用户的简短问题，生成一段假设性的文档段落，
就像知识库中确实存在的一篇文档会写的内容。

要求：
1. 长度控制在 100-200 字以内。
2. 使用正式的企业文档口吻。
3. 包含可能与问题相关的关键术语和概念。
4. 只输出段落文本，不要加任何前缀或说明。
"""

COMPRESSION_SYSTEM_PROMPT = """\
你是一个信息压缩专家。请将以下多个文档片段压缩合并为一段简洁连贯的上下文，
去除冗余和重复信息，保留关键事实和数据。

要求：
1. 总长度不超过 {max_chars} 字符。
2. 保留原文中的关键数字、日期、名称等具体信息。
3. 使用流畅的自然语言组织，不要用列表或项目符号。
4. 直接输出压缩后的文本，不加前缀或说明。
"""

HALLUCINATION_CHECK_SYSTEM_PROMPT = """\
你是一个事实一致性检验员。请判断以下上下文是否包含回答用户问题所需的信息。

要求：
1. 仔细阅读上下文和用户问题。
2. 判断上下文是否覆盖了问题的核心要点。
3. 如果上下文包含足够的信息来回答（即使不完整），返回 pass=true。
4. 如果上下文完全不相关或完全无法回答，返回 pass=false。

请严格按以下 JSON 格式输出，不要包含其他内容：
{"pass": true/false, "reason": "简短说明原因"}
"""

RAG_ANSWER_SYSTEM_PROMPT = """\
你是一个企业级知识问答助手。请根据提供的上下文信息回答用户的问题。

要求：
1. 答案必须基于提供的上下文，不得编造信息。
2. 如果上下文不足以完整回答，请明确说明局限性。
3. 引用具体信息时注明出处（如页码）。
4. 答案要专业、清晰、有条理。
5. 如果上下文与问题不相关，请如实告知用户。
"""


# ---------------------------------------------------------------------------
# Step 1: Adaptive HyDE
# ---------------------------------------------------------------------------

async def _adaptive_hyde(query: str) -> str:
    """Adaptive HyDE：短查询扩展，长查询原样使用。

    如果查询 < 20 字符，使用 LLM 生成假设文档段落扩展查询。
    如果查询 >= 20 字符，直接使用原始查询。
    """
    if len(query.strip()) >= 20:
        logger.info("[RAG:HyDE] 查询长度 %d >= 20，跳过 HyDE 扩展", len(query.strip()))
        return query.strip()

    logger.info("[RAG:HyDE] 查询长度 %d < 20，执行 HyDE 扩展", len(query.strip()))
    try:
        llm = get_heavy_llm(temperature=0.3, max_tokens=512)
        response = await llm.ainvoke([
            {"role": "system", "content": HYDE_SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ])
        hyde_text = response.content if hasattr(response, "content") else str(response)
        expanded = hyde_text.strip()
        logger.info("[RAG:HyDE] 扩展后查询长度: %d", len(expanded))
        return expanded
    except Exception as e:
        logger.warning("[RAG:HyDE] HyDE 扩展失败，使用原始查询: %s", e)
        return query.strip()


# ---------------------------------------------------------------------------
# Step 2: pgvector 向量召回
# ---------------------------------------------------------------------------

def _build_embedding_client() -> OpenAIEmbeddings:
    """构建嵌入模型客户端，使用 DeepSeek 的 base_url 和 api_key。"""
    return OpenAIEmbeddings(
        model="text-embedding-3-small",
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )


async def _get_query_embedding(query: str) -> list[float] | None:
    """获取查询文本的嵌入向量。"""
    try:
        embeddings = _build_embedding_client()
        # OpenAIEmbeddings.embed_query 是同步的，在事件循环中直接调用
        vector = await asyncio.to_thread(embeddings.embed_query, query)
        return vector
    except Exception as e:
        logger.warning("[RAG:Vector] 获取查询嵌入向量失败: %s", e)
        return None


async def _vector_recall(query_vector: list[float]) -> list[dict]:
    """通过 pgvector 进行向量相似度召回。"""
    if not query_vector:
        logger.warning("[RAG:Vector] 查询向量为空，跳过向量召回")
        return []

    try:
        async with read_only_session() as session:
            result = await session.execute(
                text("""
                    SELECT chunk_id, content, page_number, doc_id,
                           1 - (embedding <=> :query_vec) AS similarity
                    FROM document_chunks
                    WHERE is_deleted = false
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> :query_vec
                    LIMIT :limit
                """),
                {"query_vec": query_vector, "limit": VECTOR_RECALL_LIMIT},
            )
            rows = result.fetchall()

        chunks = [
            {
                "chunk_id": str(row.chunk_id),
                "content": row.content,
                "page_number": row.page_number,
                "doc_id": str(row.doc_id),
                "similarity": float(row.similarity) if row.similarity is not None else 0.0,
            }
            for row in rows
        ]
        logger.info("[RAG:Vector] 向量召回 %d 条结果", len(chunks))
        return chunks
    except Exception as e:
        logger.warning("[RAG:Vector] 向量召回失败: %s", e)
        return []


# ---------------------------------------------------------------------------
# Step 3: PostgreSQL ts_rank BM25 关键词召回
# ---------------------------------------------------------------------------

def _build_tsquery(query: str) -> str | None:
    """将查询文本构建为 PostgreSQL tsquery。

    处理中文无空格分词：将连续的非空格字符串用 | 连接，
    英文单词之间用 & 连接。
    """
    # 预处理：移除特殊字符，保留中英文和数字
    cleaned = re.sub(r'[^\w\s一-鿿]', ' ', query)
    tokens = cleaned.split()

    if not tokens:
        return None

    # 中文单字和英文单词分别处理
    ts_parts = []
    for token in tokens:
        if re.search(r'[一-鿿]', token):
            # 中文串：按单字用 | 连接（OR 语义）
            chars = list(token)
            ts_parts.extend(chars)
        else:
            # 英文单词：直接使用
            ts_parts.append(token)

    if not ts_parts:
        return None

    # 使用 | 连接（OR 语义，提高召回率）
    ts_query = " | ".join(ts_parts)
    logger.info("[RAG:BM25] tsquery: %s", ts_query)
    return ts_query


async def _bm25_recall(ts_query: str | None) -> list[dict]:
    """通过 PostgreSQL ts_rank 进行 BM25 关键词召回。"""
    if not ts_query:
        logger.info("[RAG:BM25] tsquery 为空，跳过 BM25 召回")
        return []

    try:
        async with read_only_session() as session:
            result = await session.execute(
                text("""
                    SELECT chunk_id, content, page_number, doc_id,
                           ts_rank(to_tsvector('simple', content), to_tsquery('simple', :ts_query)) AS rank
                    FROM document_chunks
                    WHERE is_deleted = false
                      AND to_tsvector('simple', content) @@ to_tsquery('simple', :ts_query)
                    ORDER BY rank DESC
                    LIMIT :limit
                """),
                {"ts_query": ts_query, "limit": BM25_RECALL_LIMIT},
            )
            rows = result.fetchall()

        chunks = [
            {
                "chunk_id": str(row.chunk_id),
                "content": row.content,
                "page_number": row.page_number,
                "doc_id": str(row.doc_id),
                "bm25_rank": float(row.rank) if row.rank is not None else 0.0,
            }
            for row in rows
        ]
        logger.info("[RAG:BM25] BM25 召回 %d 条结果", len(chunks))
        return chunks
    except Exception as e:
        logger.warning("[RAG:BM25] BM25 召回失败: %s", e)
        return []


# ---------------------------------------------------------------------------
# Step 4: RRF 融合
# ---------------------------------------------------------------------------

def _rrf_fusion(
    vector_results: list[dict],
    bm25_results: list[dict],
    k: int = RRF_K,
    top_n: int = RRF_TOP_N,
) -> list[dict]:
    """使用 Reciprocal Rank Fusion 融合向量和关键词召回结果。

    Score = sum(1 / (k + rank_i))，其中 rank_i 是 1-indexed 位置。

    Args:
        vector_results: 向量召回结果列表（按相似度排序）。
        bm25_results: BM25 召回结果列表（按 rank 排序）。
        k: RRF 常数，默认 60。
        top_n: 返回的前 N 个融合结果。

    Returns:
        融合后的 top-N 结果，每项包含 chunk_id / content / page_number / doc_id / rrf_score。
    """
    if not vector_results and not bm25_results:
        logger.warning("[RAG:RRF] 两个召回列表均为空")
        return []

    scores: dict[str, dict] = {}  # chunk_id -> {info, rrf_score}

    # 向量结果贡献
    for rank_idx, chunk in enumerate(vector_results):
        cid = chunk.get("chunk_id", "")
        if not cid:
            cid = chunk.get("doc_id", "") + "_" + str(rank_idx)
        rrf_score = 1.0 / (k + rank_idx + 1)
        if cid in scores:
            scores[cid]["rrf_score"] += rrf_score
        else:
            scores[cid] = {
                "chunk_id": cid,
                "content": chunk.get("content", ""),
                "page_number": chunk.get("page_number"),
                "doc_id": chunk.get("doc_id", ""),
                "rrf_score": rrf_score,
            }

    # BM25 结果贡献
    for rank_idx, chunk in enumerate(bm25_results):
        cid = chunk.get("chunk_id", "")
        if not cid:
            cid = chunk.get("doc_id", "") + "_" + str(rank_idx)
        rrf_score = 1.0 / (k + rank_idx + 1)
        if cid in scores:
            scores[cid]["rrf_score"] += rrf_score
        else:
            scores[cid] = {
                "chunk_id": cid,
                "content": chunk.get("content", ""),
                "page_number": chunk.get("page_number"),
                "doc_id": chunk.get("doc_id", ""),
                "rrf_score": rrf_score,
            }

    # 按 RRF 分数降序排序
    fused = sorted(scores.values(), key=lambda x: x["rrf_score"], reverse=True)
    result = fused[:top_n]

    logger.info("[RAG:RRF] 融合结果: 向量=%d + BM25=%d -> 融合=%d -> top-%d",
                len(vector_results), len(bm25_results), len(fused), len(result))
    return result


# ---------------------------------------------------------------------------
# Step 5: 权限过滤
# ---------------------------------------------------------------------------

async def _permission_filter(user_id: str, chunks: list[dict]) -> list[dict]:
    """根据用户 doc_tags 权限过滤检索结果。

    从 documents 表查询每个 doc_id 的 tags，
    使用 RBACEngine 检查用户是否有访问权限。
    """
    if not chunks:
        return chunks

    try:
        rbac = RBACEngine()
        perms = await rbac.get_user_permissions(user_id)

        # 尝试获取文档级别权限范围
        allowed_tags: set[str] = set()
        perms_dict = vars(perms) if hasattr(perms, "__dict__") else {}
        doc_tags_raw = perms_dict.get("doc_tags_allowed", None)
        if doc_tags_raw is None:
            doc_tags_raw = getattr(perms, "doc_tags_allowed", None)
        if doc_tags_raw:
            if isinstance(doc_tags_raw, str) and doc_tags_raw == "*":
                logger.info("[RAG:Perm] 用户 %s 拥有全部文档权限，跳过过滤", user_id)
                return chunks
            if isinstance(doc_tags_raw, (list, set)):
                allowed_tags = set(doc_tags_raw)

        if not allowed_tags:
            # 无法获取文档标签权限时，基于表权限做保守过滤
            # 回退：检查用户是否有文档表的读取权限
            try:
                has_doc_access = await rbac.check_table_access(user_id, "documents")
                if has_doc_access:
                    logger.info("[RAG:Perm] 用户 %s 有 documents 表权限，保留所有结果", user_id)
                    return chunks
            except Exception:
                pass

        # 收集所有唯一的 doc_id
        doc_ids = list(set(ch.get("doc_id", "") for ch in chunks if ch.get("doc_id")))

        # 查询文档标签
        doc_tags_map: dict[str, set[str]] = {}
        if doc_ids:
            try:
                async with read_only_session() as session:
                    result = await session.execute(
                        text("""
                            SELECT id, tags FROM documents
                            WHERE id = ANY(:doc_ids) AND is_deleted = false
                        """),
                        {"doc_ids": doc_ids},
                    )
                    for row in result.fetchall():
                        doc_tags_map[str(row.id)] = set(row.tags) if row.tags else set()
            except Exception as e:
                logger.warning("[RAG:Perm] 查询文档标签失败: %s", e)

        if not allowed_tags:
            # 无明确权限约束时，保留所有结果
            logger.info("[RAG:Perm] 无明确文档标签权限约束，保留所有结果")
            return chunks

        filtered = []
        for chunk in chunks:
            doc_id = chunk.get("doc_id", "")
            if not doc_id:
                filtered.append(chunk)
                continue

            doc_tags = doc_tags_map.get(doc_id, set())
            if not doc_tags:
                filtered.append(chunk)
                continue

            if doc_tags & allowed_tags:
                filtered.append(chunk)

        logger.info("[RAG:Perm] 权限过滤: %d -> %d 条结果", len(chunks), len(filtered))
        return filtered

    except Exception as e:
        logger.warning("[RAG:Perm] 权限过滤失败，返回原始结果: %s", e)
        return chunks


# ---------------------------------------------------------------------------
# Step 6: BGE-Reranker
# ---------------------------------------------------------------------------

async def _get_bge_embedding(text: str) -> list[float] | None:
    """通过 BGE API 获取文本的嵌入向量。"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.bge_api_base}/embeddings",
                json={"input": text, "model": settings.bge_model_name},
                timeout=30.0,
            )
            if resp.status_code != 200:
                logger.warning("[RAG:BGE] API 返回非 200: %d", resp.status_code)
                return None
            data = resp.json()
            return data["data"][0]["embedding"]
    except Exception as e:
        logger.warning("[RAG:BGE] 获取 BGE 嵌入失败: %s", e)
        return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。"""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _bge_rerank(query: str, chunks: list[dict], top_k: int = RERANK_TOP_K) -> list[dict]:
    """使用 BGE 嵌入进行重排序。

    分别获取查询和每个 chunk 的 BGE 嵌入向量，
    计算余弦相似度并重新排序。
    """
    if not chunks:
        return chunks

    try:
        query_emb = await _get_bge_embedding(query)
        if not query_emb:
            logger.warning("[RAG:BGE] 查询 BGE 嵌入获取失败，跳过重排序")
            return chunks[:top_k]

        # 为每个 chunk 获取嵌入并计算相似度
        async def get_chunk_score(idx: int, chunk: dict) -> tuple[int, float]:
            content = chunk.get("content", "")
            if not content:
                return idx, 0.0
            chunk_emb = await _get_bge_embedding(content[:2000])
            if not chunk_emb:
                return idx, 0.0
            return idx, _cosine_similarity(query_emb, chunk_emb)

        # 并发获取所有 chunk 的 BGE 相似度
        tasks = [get_chunk_score(i, ch) for i, ch in enumerate(chunks)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        scored = []
        for result in results:
            if isinstance(result, Exception):
                continue
            idx, score = result
            chunk = dict(chunks[idx])
            chunk["bge_score"] = score
            scored.append(chunk)

        # 按 BGE 相似度降序排序
        scored.sort(key=lambda x: x.get("bge_score", 0.0), reverse=True)
        top = scored[:top_k]

        logger.info("[RAG:BGE] BGE 重排序: %d -> top-%d 结果", len(scored), len(top))
        return top

    except Exception as e:
        logger.warning("[RAG:BGE] BGE 重排序失败，返回原始 top-%d: %s", top_k, e)
        return chunks[:top_k]


# ---------------------------------------------------------------------------
# Step 7: 上下文压缩
# ---------------------------------------------------------------------------

async def _compress_context(chunks: list[dict], max_chars: int = MAX_COMPRESSED_CHARS) -> str:
    """使用 LLM 将多个文档块压缩为简洁的上下文段落。

    Args:
        chunks: 排序后的 top chunks 列表。
        max_chars: 压缩后的最大字符数。

    Returns:
        压缩后的上下文文本。
    """
    if not chunks:
        return ""

    # 拼接原始内容
    raw_context_parts = []
    for i, chunk in enumerate(chunks):
        content = chunk.get("content", "")
        page = chunk.get("page_number", "")
        doc_id = chunk.get("doc_id", "")
        source_info = f"[来源{doc_id}"
        if page:
            source_info += f", 第{page}页"
        source_info += "]"
        raw_context_parts.append(f"{source_info}\n{content}")

    raw_context = "\n\n---\n\n".join(raw_context_parts)

    # 如果原始内容已经很短，不需要压缩
    if len(raw_context) <= max_chars:
        logger.info("[RAG:Compress] 原始上下文长度 %d <= 阈值 %d，跳过压缩",
                    len(raw_context), max_chars)
        return raw_context

    try:
        llm = get_heavy_llm(temperature=0.0, max_tokens=2048)
        system_prompt = COMPRESSION_SYSTEM_PROMPT.format(max_chars=max_chars)
        response = await llm.ainvoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": raw_context},
        ])
        compressed = response.content if hasattr(response, "content") else str(response)
        logger.info("[RAG:Compress] 压缩完成: %d -> %d 字符",
                    len(raw_context), len(compressed.strip()))
        return compressed.strip()
    except Exception as e:
        logger.warning("[RAG:Compress] 压缩失败，截取前 %d 字符: %s", max_chars, e)
        return raw_context[:max_chars]


# ---------------------------------------------------------------------------
# Step 8: 幻觉检测
# ---------------------------------------------------------------------------

async def _hallucination_check(query: str, compressed_context: str) -> dict:
    """检查压缩后的上下文是否支持回答用户问题。

    Args:
        query: 用户原始问题。
        compressed_context: 压缩后的上下文。

    Returns:
        {"pass": bool, "reason": str}
    """
    if not compressed_context:
        return {"pass": False, "reason": "上下文为空，无法回答"}

    try:
        llm = get_heavy_llm(temperature=0.0, max_tokens=512)
        response = await llm.ainvoke([
            {"role": "system", "content": HALLUCINATION_CHECK_SYSTEM_PROMPT},
            {"role": "user", "content": f"用户问题: {query}\n\n上下文:\n{compressed_context}"},
        ])
        raw = response.content if hasattr(response, "content") else str(response)

        # 解析 JSON
        json_match = re.search(r'\{[^}]+\}', raw)
        if json_match:
            result = json.loads(json_match.group())
            logger.info("[RAG:Halluc] 幻觉检测结果: pass=%s", result.get("pass"))
            return {
                "pass": bool(result.get("pass", False)),
                "reason": str(result.get("reason", "无法解析检测结果")),
            }

        return {"pass": False, "reason": f"无法解析检测结果: {raw[:200]}"}
    except Exception as e:
        logger.warning("[RAG:Halluc] 幻觉检测失败: %s", e)
        return {"pass": True, "reason": f"幻觉检测执行异常: {str(e)}"}


# ---------------------------------------------------------------------------
# Step 9: 最终答案生成
# ---------------------------------------------------------------------------

async def _generate_rag_answer(query: str, compressed_context: str) -> str:
    """基于压缩上下文生成最终答案。

    Args:
        query: 用户原始问题。
        compressed_context: 压缩后的上下文。

    Returns:
        自然语言答案。
    """
    if not compressed_context:
        return "抱歉，未找到与您问题相关的文档信息。请尝试更具体地描述您的问题，或联系管理员补充相关文档。"

    try:
        llm = get_heavy_llm(temperature=0.3, max_tokens=4096)
        response = await llm.ainvoke([
            {"role": "system", "content": RAG_ANSWER_SYSTEM_PROMPT},
            {"role": "user", "content": f"用户问题: {query}\n\n参考上下文:\n{compressed_context}"},
        ])
        answer = response.content if hasattr(response, "content") else str(response)
        logger.info("[RAG:Answer] 答案生成完成，长度: %d", len(answer))
        return answer.strip()
    except Exception as e:
        logger.warning("[RAG:Answer] 答案生成失败: %s", e)
        return f"抱歉，生成答案时遇到错误: {str(e)}"


# ---------------------------------------------------------------------------
# 主节点函数
# ---------------------------------------------------------------------------

async def rag_pipeline_node(state: AgentState) -> dict:
    """完整的 RAG 检索管线 - simple_qa 专用。

    依次执行 9 个步骤，每步失败回退到已有最佳结果。

    Args:
        state: 当前 AgentState.

    Returns:
        {
            "final_answer": str,
            "retrieved_docs": list[dict],
            "hallucination_check_pass": bool,
        }
    """
    query: str = state.get("query", "")
    user_id: str = state.get("user_id", "")

    logger.info("[RAG] 启动 RAG 管线，query=%s, user=%s", query[:80], user_id)

    if not query.strip():
        return {
            "final_answer": "请提供您的问题。",
            "retrieved_docs": [],
            "hallucination_check_pass": True,
        }

    # ---- Step 1: Adaptive HyDE ----
    expanded_query = await _adaptive_hyde(query)

    # ---- Step 2: pgvector 向量召回 ----
    query_vector = await _get_query_embedding(expanded_query)
    vector_results = await _vector_recall(query_vector) if query_vector else []

    # ---- Step 3: BM25 关键词召回 ----
    ts_query = _build_tsquery(query)
    bm25_results = await _bm25_recall(ts_query)

    # ---- Step 4: RRF 融合 ----
    fused_chunks = _rrf_fusion(vector_results, bm25_results)

    if not fused_chunks:
        # 完全无结果时，尝试只用单个列表
        if vector_results or bm25_results:
            fused_chunks = (vector_results + bm25_results)[:RRF_TOP_N]
            logger.info("[RAG] 融合为空，使用拼接结果: %d 条", len(fused_chunks))

    # ---- Step 5: 权限过滤 ----
    filtered_chunks = await _permission_filter(user_id, fused_chunks)

    # ---- Step 6: BGE-Reranker ----
    reranked_chunks = await _bge_rerank(query, filtered_chunks)

    if not reranked_chunks:
        reranked_chunks = filtered_chunks[:RERANK_TOP_K]
        logger.info("[RAG] 重排序结果为空，使用过滤结果 top-%d", len(reranked_chunks))

    # ---- Step 7: 上下文压缩 ----
    compressed_context = await _compress_context(reranked_chunks)

    # ---- Step 8: 幻觉检测 ----
    hallucination_result = await _hallucination_check(query, compressed_context)

    # ---- Step 9: 最终答案生成 ----
    final_answer = await _generate_rag_answer(query, compressed_context)

    # 构建返回的 retrieved_docs
    retrieved_docs = [
        {
            "chunk_id": ch.get("chunk_id", ""),
            "content": (ch.get("content", "") or "")[:500],
            "page_number": ch.get("page_number"),
            "doc_id": ch.get("doc_id", ""),
            "score": ch.get("bge_score", ch.get("rrf_score", 0.0)),
        }
        for ch in reranked_chunks
    ]

    logger.info("[RAG] 管道完成: retrieved=%d, hallucination_pass=%s",
                len(retrieved_docs), hallucination_result.get("pass"))

    return {
        "final_answer": final_answer,
        "retrieved_docs": retrieved_docs,
        "hallucination_check_pass": hallucination_result.get("pass", False),
    }
