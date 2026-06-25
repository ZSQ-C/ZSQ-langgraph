"""
Critic Node - 确定性溯源校验 + LLM自然语言反馈

对工具执行结果运行6项确定性规则检查（非LLM决策），
仅使用轻量LLM将检查结果格式化为自然语言建议。
"""

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage

from src.llm.factory import get_light_llm
from src.llm.prompts.critic import CRITIC_FORMAT_PROMPT
from src.orchestration.state import AgentState

logger = logging.getLogger(__name__)


# ============================================================================
# 辅助函数
# ============================================================================

def _extract_numbers(text: str) -> list[float]:
    """从文本中提取所有数值（整数和浮点数）。

    Args:
        text: 待提取文本

    Returns:
        提取到的数值列表
    """
    if not text:
        return []
    pattern = r"\d+\.?\d*"
    matches = re.findall(pattern, str(text))
    return [float(m) for m in matches if m]


def _split_sentences(text: str) -> list[str]:
    """按句号、换行等将文本拆分为句子列表，过滤空白句。

    Args:
        text: 待拆分文本

    Returns:
        句子列表
    """
    if not text:
        return []
    # 按句号、感叹号、问号、换行拆分
    raw = re.split(r"[。！？\n.!?]+", str(text))
    return [s.strip() for s in raw if len(s.strip()) > 3]


def _get_tool_outputs_text(execution_result: dict) -> str:
    """从执行结果中提取所有工具原始输出的合并文本。

    Args:
        execution_result: 执行结果字典

    Returns:
        所有工具输出的文本合并
    """
    parts = []
    raw_outputs = execution_result.get("raw_outputs", {})
    if isinstance(raw_outputs, dict):
        for step_id, output in raw_outputs.items():
            parts.append(json.dumps(output, ensure_ascii=False, default=str))
    return "\n".join(parts)


# ============================================================================
# 6项确定性检查
# ============================================================================

def _check_rag_traceability(execution_result: dict, combined_summary: str) -> dict:
    """检查1: RAG溯源检查。

    如果执行结果中有 rag_retrieval 步骤，验证摘要中包含
    chunk_id 或 [source: ...] 或 [chunk: ...] 等溯源标记。

    Args:
        execution_result: 执行结果
        combined_summary: 合并后的摘要文本

    Returns:
        检查结果字典
    """
    evidence: list[str] = []

    # 判断是否有 RAG 步骤
    steps = execution_result.get("steps", [])
    rag_steps = [s for s in steps if isinstance(s, dict) and s.get("tool_name") == "rag_retrieval"]

    if not rag_steps:
        return {
            "name": "RAG溯源检查",
            "passed": True,
            "detail": "无RAG检索步骤，跳过此检查。",
            "evidence": ["无RAG步骤，不适用"],
        }

    # 收集RAG输出中的 chunk_id 信息
    for step in rag_steps:
        tool_output = step.get("tool_output", {})
        if isinstance(tool_output, dict):
            chunks = tool_output.get("chunks", [])
            if isinstance(chunks, list):
                for chunk in chunks:
                    if isinstance(chunk, dict):
                        cid = chunk.get("chunk_id", "")
                        source = chunk.get("source", "")
                        if cid:
                            evidence.append(f"chunk_id={cid} source={source}")

    # 在摘要中搜索溯源标记
    summary_lower = combined_summary.lower()
    has_chunk_id = bool(re.search(r"chunk_id", summary_lower))
    has_source = bool(re.search(r"\[source:", summary_lower))
    has_chunk = bool(re.search(r"\[chunk:", summary_lower))
    has_found = has_chunk_id or has_source or has_chunk

    if has_found:
        return {
            "name": "RAG溯源检查",
            "passed": True,
            "detail": "摘要中包含溯源引用标记。",
            "evidence": evidence,
        }

    # 进一步检查：摘要是否引用了具体的chunk内容
    # 检查摘要中是否包含任意 chunk 内容的片段
    chunk_contents = []
    for step in rag_steps:
        tool_output = step.get("tool_output", {})
        if isinstance(tool_output, dict):
            chunks = tool_output.get("chunks", [])
            if isinstance(chunks, list):
                for chunk in chunks:
                    if isinstance(chunk, dict):
                        content = chunk.get("content", "")
                        if content:
                            chunk_contents.append(content)

    # 检查摘要句子是否与任意chunk有内容重叠
    summary_sentences = _split_sentences(combined_summary)
    referenced = False
    for sentence in summary_sentences:
        for ck in chunk_contents:
            words = set(sentence.lower().split()) & set(ck.lower().split())
            if len(words) >= 3:
                referenced = True
                break
        if referenced:
            break

    if referenced:
        return {
            "name": "RAG溯源检查",
            "passed": True,
            "detail": "摘要内容与检索文档有实际重叠（基于词级匹配）。",
            "evidence": evidence,
        }

    return {
        "name": "RAG溯源检查",
        "passed": False,
        "detail": "摘要中缺少 chunk_id 或 [source:] 等溯源引用标记，且未检测到与检索文档的内容重叠。",
        "evidence": evidence,
    }


def _check_sql_traceability(execution_result: dict, combined_summary: str) -> dict:
    """检查2: SQL溯源检查。

    如果执行结果中有 sql_query 步骤，验证：
    - 结果字典包含 table_name 和 row_count 键
    - 摘要中提及表名和行数

    Args:
        execution_result: 执行结果
        combined_summary: 合并后的摘要文本

    Returns:
        检查结果字典
    """
    evidence: list[str] = []

    steps = execution_result.get("steps", [])
    sql_steps = [s for s in steps if isinstance(s, dict) and s.get("tool_name") == "sql_query"]

    if not sql_steps:
        return {
            "name": "SQL溯源检查",
            "passed": True,
            "detail": "无SQL查询步骤，跳过此检查。",
            "evidence": ["无SQL步骤，不适用"],
        }

    all_passed = True
    detail_parts = []

    for step in sql_steps:
        tool_output = step.get("tool_output", {})
        if not isinstance(tool_output, dict):
            all_passed = False
            detail_parts.append(f"Step{step.get('step_id', '?')}: 输出不是字典")
            continue

        table_name = tool_output.get("table_name")
        row_count = tool_output.get("row_count")

        evidence.append(f"table_name={table_name} row_count={row_count}")

        if table_name is None and row_count is None:
            all_passed = False
            detail_parts.append(f"Step{step.get('step_id', '?')}: 缺少 table_name 和 row_count")
        elif table_name is None:
            all_passed = False
            detail_parts.append(f"Step{step.get('step_id', '?')}: 缺少 table_name")
        elif row_count is None:
            all_passed = False
            detail_parts.append(f"Step{step.get('step_id', '?')}: 缺少 row_count")

    # 检查摘要中是否提及
    summary_lower = combined_summary.lower()
    if not all_passed:
        return {
            "name": "SQL溯源检查",
            "passed": False,
            "detail": "; ".join(detail_parts) if detail_parts else "SQL结果缺少溯源字段",
            "evidence": evidence,
        }

    # 检查摘要是否引用了表名和行数
    table_mentioned = False
    rows_mentioned = False
    for step in sql_steps:
        tool_output = step.get("tool_output", {})
        if isinstance(tool_output, dict):
            tn = str(tool_output.get("table_name", ""))
            if tn and tn.lower() in summary_lower:
                table_mentioned = True
            rc = str(tool_output.get("row_count", ""))
            if rc and rc in summary_lower:
                rows_mentioned = True

    if not table_mentioned and not rows_mentioned:
        all_passed = False
        detail_parts.append("摘要中未提及表名和行数")

    return {
        "name": "SQL溯源检查",
        "passed": all_passed,
        "detail": "; ".join(detail_parts) if detail_parts else "SQL结果包含完整溯源信息，摘要中已引用。",
        "evidence": evidence,
    }


def _check_tool_chain_completeness(task_plan: list[dict], execution_result: dict) -> dict:
    """检查3: 工具链完整性。

    验证 task_plan 中每个步骤在 execution_result["steps"] 中都有对应的执行记录。
    同时检查有无多余的执行记录。

    Args:
        task_plan: 任务规划步骤列表
        execution_result: 执行结果

    Returns:
        检查结果字典
    """
    evidence: list[str] = []

    if not task_plan:
        return {
            "name": "工具链完整性",
            "passed": True,
            "detail": "无任务计划，跳过此检查。",
            "evidence": ["无任务计划"],
        }

    planned_ids = {s["step_id"] for s in task_plan if isinstance(s, dict)}
    exec_steps = execution_result.get("steps", [])
    executed_ids = {s.get("step_id") for s in exec_steps if isinstance(s, dict)}

    evidence.append(f"planned_ids={sorted(planned_ids)}")
    evidence.append(f"executed_ids={sorted(executed_ids)}")

    missing_ids = planned_ids - executed_ids
    extra_ids = executed_ids - planned_ids

    if not missing_ids and not extra_ids:
        return {
            "name": "工具链完整性",
            "passed": True,
            "detail": f"所有{len(planned_ids)}个计划步骤均已执行。",
            "evidence": evidence,
        }

    detail_parts = []
    passed = True

    if missing_ids:
        passed = False
        detail_parts.append(f"缺失执行记录: step_ids={sorted(missing_ids)}")

    if extra_ids:
        detail_parts.append(f"多余执行记录: step_ids={sorted(extra_ids)} (可能来自状态合并)")

    return {
        "name": "工具链完整性",
        "passed": passed,
        "detail": "; ".join(detail_parts),
        "evidence": evidence,
    }


def _check_numeric_consistency(execution_result: dict, combined_summary: str) -> dict:
    """检查4: 数值一致性。

    如果工具输出中包含数值结果，验证摘要中是否体现了相同的数值（允许四舍五入差异）。

    Args:
        execution_result: 执行结果
        combined_summary: 合并后的摘要文本

    Returns:
        检查结果字典
    """
    evidence: list[str] = []

    # 从所有工具输出中提取数值
    all_outputs_text = _get_tool_outputs_text(execution_result)
    output_numbers = _extract_numbers(all_outputs_text)

    # 从摘要中提取数值
    summary_numbers = _extract_numbers(combined_summary)

    evidence.append(f"output_numbers_count={len(output_numbers)}")
    evidence.append(f"summary_numbers_count={len(summary_numbers)}")

    if not output_numbers:
        return {
            "name": "数值一致性",
            "passed": True,
            "detail": "工具输出中无显著数值，跳过此检查。",
            "evidence": evidence,
        }

    # 检查每个工具输出中的关键数值是否在摘要中有所体现
    # 策略：对于工具输出中的每个数值，检查摘要中是否存在相同或接近的数值
    missing_in_summary = []
    for num in output_numbers:
        if num == 0:
            continue
        found = False
        for snum in summary_numbers:
            if snum == 0:
                continue
            # 允许1%的相对误差或绝对误差
            if abs(num - snum) <= max(abs(num) * 0.01, 1.0):
                found = True
                break
        if not found:
            missing_in_summary.append(num)

    if missing_in_summary:
        # 仅报告前5个缺失的数值
        sample = missing_in_summary[:5]
        return {
            "name": "数值一致性",
            "passed": False,
            "detail": f"工具输出中的{len(missing_in_summary)}个数值在摘要中未体现: {sample}",
            "evidence": evidence + [f"missing_values_sample={sample}"],
        }

    return {
        "name": "数值一致性",
        "passed": True,
        "detail": f"摘要中正确反映了工具输出的{len(output_numbers)}个数值。",
        "evidence": evidence,
    }


def _check_permission_compliance(execution_result: dict, audit_log: dict, combined_summary: str) -> dict:
    """检查5: 权限合规。

    检查是否有数据脱敏操作被应用，如果有 masked_fields 存在，
    验证审计日志中提及了数据脱敏。

    Args:
        execution_result: 执行结果
        audit_log: 审计日志
        combined_summary: 合并后的摘要文本

    Returns:
        检查结果字典
    """
    evidence: list[str] = []

    # 从执行结果中检查是否有脱敏标记
    masked_found = False
    masked_fields_list: list[str] = []

    raw_outputs = execution_result.get("raw_outputs", {})
    if isinstance(raw_outputs, dict):
        for step_id, output in raw_outputs.items():
            if isinstance(output, dict):
                mf = output.get("masked_fields", [])
                if mf:
                    masked_found = True
                    if isinstance(mf, list):
                        masked_fields_list.extend(mf)
                    else:
                        masked_fields_list.append(str(mf))

    # 同时检查步骤记录中是否有脱敏相关工具
    steps = execution_result.get("steps", [])
    for step in steps:
        if isinstance(step, dict):
            tool_output = step.get("tool_output", {})
            if isinstance(tool_output, dict):
                mf = tool_output.get("masked_fields", [])
                if mf:
                    masked_found = True
                    if isinstance(mf, list):
                        masked_fields_list.extend(mf)
                    else:
                        masked_fields_list.append(str(mf))

    evidence.append(f"masked_found={masked_found}")
    if masked_fields_list:
        evidence.append(f"masked_fields={masked_fields_list}")

    if not masked_found:
        return {
            "name": "权限合规",
            "passed": True,
            "detail": "未检测到需要脱敏的字段。",
            "evidence": evidence,
        }

    # 有脱敏操作 - 检查审计日志和摘要中是否提及
    audit_text = ""
    if isinstance(audit_log, dict):
        audit_text = json.dumps(audit_log, ensure_ascii=False, default=str)

    # 在审计日志和摘要中搜索脱敏相关关键词
    masking_keywords = ["脱敏", "mask", "masking", "data_mask", "masked"]
    audit_mentions = any(kw.lower() in audit_text.lower() for kw in masking_keywords)
    summary_mentions = any(kw.lower() in combined_summary.lower() for kw in masking_keywords)

    evidence.append(f"audit_mentions_masking={audit_mentions}")
    evidence.append(f"summary_mentions_masking={summary_mentions}")

    if audit_mentions or summary_mentions:
        return {
            "name": "权限合规",
            "passed": True,
            "detail": f"数据脱敏已应用 ({len(masked_fields_list)}个字段)，且审计/摘要中已体现。",
            "evidence": evidence,
        }

    return {
        "name": "权限合规",
        "passed": False,
        "detail": f"检测到{len(masked_fields_list)}个脱敏字段 ({masked_fields_list[:3]})，但审计日志和摘要均未提及数据脱敏操作。",
        "evidence": evidence,
    }


def _check_anti_hallucination(execution_result: dict, combined_summary: str) -> dict:
    """检查6: 反幻觉检查。

    检查摘要中的每个句子是否能在工具原始输出中找到词级重叠。
    如果某句子与所有工具输出的词交集为零，标记为可疑。

    Args:
        execution_result: 执行结果
        combined_summary: 合并后的摘要文本

    Returns:
        检查结果字典
    """
    evidence: list[str] = []

    if not combined_summary.strip():
        return {
            "name": "反幻觉检查",
            "passed": True,
            "detail": "摘要为空，跳过此检查。",
            "evidence": ["摘要为空"],
        }

    # 获取所有工具输出的合并文本
    all_outputs_text = _get_tool_outputs_text(execution_result)
    if not all_outputs_text.strip():
        return {
            "name": "反幻觉检查",
            "passed": True,
            "detail": "无工具输出数据，跳过此检查。",
            "evidence": ["无工具输出"],
        }

    output_words = set(all_outputs_text.lower().split())

    # 拆分摘要为句子
    sentences = _split_sentences(combined_summary)
    if not sentences:
        sentences = [combined_summary]

    hallucinated_sentences = []
    for i, sentence in enumerate(sentences):
        sentence_words = set(sentence.lower().split())
        # 过滤纯停用词/标点
        meaningful_words = {w for w in sentence_words if len(w) > 1 and not w.isdigit()}
        if not meaningful_words:
            continue

        overlap = meaningful_words & output_words
        if len(overlap) == 0:
            hallucinated_sentences.append(sentence[:100])
            evidence.append(f"hallucinated_sentence_{i}: {sentence[:120]}")

    if hallucinated_sentences:
        return {
            "name": "反幻觉检查",
            "passed": False,
            "detail": f"发现{len(hallucinated_sentences)}个句子在工具输出中无词级证据: {hallucinated_sentences[:3]}",
            "evidence": evidence,
        }

    return {
        "name": "反幻觉检查",
        "passed": True,
        "detail": f"摘要中所有{len(sentences)}个句子均有工具输出中的词级证据支持。",
        "evidence": evidence,
    }


# ============================================================================
# 主编排函数
# ============================================================================

async def critic_node(state: AgentState) -> dict:
    """溯源校验节点：运行6项确定性规则检查，使用LLM生成自然语言反馈。

    从状态中读取：
    - execution_result: 工具执行结果
    - task_plan: 任务规划
    - audit_log: 审计日志
    - generated_sql: 生成的SQL (可选)

    返回：
    - critic_result: 包含 quality_score、passed、failed_checks、
      missing、superfluous、source_evidence、recommendation
    """
    execution_result = state.get("execution_result", {})
    if not isinstance(execution_result, dict):
        execution_result = {}

    task_plan: list[dict] = state.get("task_plan", [])
    if not isinstance(task_plan, list):
        task_plan = []

    audit_log = state.get("audit_log", {})
    if not isinstance(audit_log, dict):
        audit_log = {}

    # 获取合并摘要
    combined_summary = execution_result.get("summary", "")

    logger.info(f"[Critic] 开始6项溯源检查 plan_steps={len(task_plan)}")

    # ======== 执行6项确定性检查 ========
    checks: list[dict] = []

    # 检查1: RAG溯源
    check1 = _check_rag_traceability(execution_result, combined_summary)
    checks.append(check1)
    logger.info(f"[Critic] 1.RAG溯源: passed={check1['passed']}")

    # 检查2: SQL溯源
    check2 = _check_sql_traceability(execution_result, combined_summary)
    checks.append(check2)
    logger.info(f"[Critic] 2.SQL溯源: passed={check2['passed']}")

    # 检查3: 工具链完整性
    check3 = _check_tool_chain_completeness(task_plan, execution_result)
    checks.append(check3)
    logger.info(f"[Critic] 3.工具链完整性: passed={check3['passed']}")

    # 检查4: 数值一致性
    check4 = _check_numeric_consistency(execution_result, combined_summary)
    checks.append(check4)
    logger.info(f"[Critic] 4.数值一致性: passed={check4['passed']}")

    # 检查5: 权限合规
    check5 = _check_permission_compliance(execution_result, audit_log, combined_summary)
    checks.append(check5)
    logger.info(f"[Critic] 5.权限合规: passed={check5['passed']}")

    # 检查6: 反幻觉
    check6 = _check_anti_hallucination(execution_result, combined_summary)
    checks.append(check6)
    logger.info(f"[Critic] 6.反幻觉: passed={check6['passed']}")

    # ======== 汇总结果 ========
    passed_checks = [c for c in checks if c["passed"]]
    failed_checks = [c for c in checks if not c["passed"]]
    passed_count = len(passed_checks)
    quality_score = round(passed_count / 6.0, 4)

    # 识别 missing: 计划中但缺少证据的步骤
    planned_ids = {s["step_id"] for s in task_plan if isinstance(s, dict)}
    exec_steps = execution_result.get("steps", [])
    executed_ids = {s.get("step_id") for s in exec_steps if isinstance(s, dict)}
    missing = sorted(list(planned_ids - executed_ids))

    # 识别 superfluous: 摘要中有但工具输出中无证据的声明
    # 复用反幻觉检查中发现的句子
    superfluous_evidence = check6.get("evidence", [])
    superfluous = [e for e in superfluous_evidence if e.startswith("hallucinated_sentence")]

    # 收集所有证据
    source_evidence: list[str] = []
    for check in checks:
        ev = check.get("evidence", [])
        if isinstance(ev, list):
            source_evidence.extend(ev)

    # 构建 verdict
    verdict = {
        "quality_score": quality_score,
        "total_checks": 6,
        "passed_count": passed_count,
        "failed_count": len(failed_checks),
        "checks": [
            {
                "name": c["name"],
                "passed": c["passed"],
                "detail": c["detail"],
            }
            for c in checks
        ],
    }

    # ======== 使用LLM生成自然语言反馈 ========
    recommendation = ""
    try:
        llm = get_light_llm(max_tokens=512)
        verdict_json = json.dumps(verdict, ensure_ascii=False)
        prompt = CRITIC_FORMAT_PROMPT.format(verdict=verdict_json)
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        recommendation = response.content if hasattr(response, "content") else str(response)
        recommendation = recommendation.strip()
        logger.info(f"[Critic] LLM反馈: {recommendation[:200]}")
    except Exception as e:
        logger.warning(f"[Critic] LLM格式化失败: {e}")
        # 回退：基于规则生成简单反馈
        if failed_checks:
            failed_names = [c["name"] for c in failed_checks]
            recommendation = f"发现{len(failed_checks)}项问题: {', '.join(failed_names)}。"
            if missing:
                recommendation += f" 缺少步骤: {missing}。"
            if superfluous:
                recommendation += f" 存在{len(superfluous)}处无证据声明。"
        else:
            recommendation = "所有检查项通过。"

    logger.info(f"[Critic] 质量评分: {quality_score} passed={passed_count}/6")

    return {
        "critic_result": {
            "quality_score": quality_score,
            "passed": [c["name"] for c in passed_checks],
            "failed_checks": failed_checks,
            "missing": missing,
            "superfluous": superfluous,
            "source_evidence": source_evidence,
            "recommendation": recommendation,
        }
    }
