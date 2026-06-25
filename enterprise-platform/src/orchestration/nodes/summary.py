"""
Summary Node - 最终答案生成

汇聚所有执行结果、Critic评价等信息，生成最终的自然语言回复。
对于数据分析场景，自动检测数值型数据并生成 ECharts 可视化配置。
"""

import json
import logging
import re
from typing import Any

from src.llm.factory import get_heavy_llm
from src.orchestration.state import AgentState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

SUMMARY_SYSTEM_PROMPT = """\
你是一个企业数据智能平台的最终答案生成助手。你的任务是根据用户的原始问题和系统执行过程中收集到的所有信息，生成一份清晰、专业、全面的自然语言回复。

## 回复结构要求

1. **直接回答**：首先用 1-2 句话直接回答用户的问题。
2. **详细分析**：展开说明分析过程、关键发现和数据解读。
3. **数据展示**：如果涉及表格数据，用清晰的 Markdown 表格展示关键数据（不超过 20 行）。
4. **可视化建议**：如果数据包含数值列且适合绘图，生成 ECharts 可视化配置。
5. **注意事项**：如有数据局限、安全限制或需要人工审核的事项，请在末尾注明。

## ECharts 可视化规则

当执行结果包含数值列（如 count、sum、amount、total、avg、rate、percentage 等）且行数 >= 2 时，生成一个 ECharts 配置。

- 默认使用柱状图（bar），如果有时间/日期列则优先使用折线图（line）。
- 如果只有一个数值列且分类列是文本，推荐饼图（pie）或柱状图。
- ECharts JSON 必须包裹在 ```echarts 代码块中。
- 生成的 option 必须是一个合法的 JSON 对象，包含 title、xAxis/yAxis（或 series 中的 data）、tooltip、legend 等标准字段。
- 数据直接内联在 option 中，不要使用占位符。
- 颜色使用企业级配色：['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4']

## 语气

- 专业但不生硬
- 若数据为空或执行失败，诚实说明原因
- 若经过人工审核，注明审核状态
"""

SUMMARY_USER_TEMPLATE = """\
## 用户原始问题
{query}

## 任务类型
{intent}

## 执行结果
{execution_results}

## Critic 质量评估
{critic_summary}

## 脱敏后的数据结果
{masked_data}

---

请根据以上信息生成最终答案。"""


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _format_execution_result(execution_result: dict) -> str:
    """将执行结果格式化为可读文本。"""
    if not execution_result:
        return "（无执行结果）"

    parts = []

    success = execution_result.get("success", False)
    columns = execution_result.get("columns", [])
    data = execution_result.get("data", [])
    row_count = execution_result.get("row_count", len(data))
    error = execution_result.get("error")
    execution_time = execution_result.get("execution_time_ms", 0)

    if error:
        parts.append(f"执行状态: 失败")
        parts.append(f"错误信息: {error}")
        return "\n".join(parts)

    parts.append(f"执行状态: {'成功' if success else '失败'}")
    if execution_time:
        parts.append(f"执行耗时: {execution_time}ms")
    parts.append(f"返回行数: {row_count}")

    if columns and data:
        parts.append(f"\n字段列表: {', '.join(columns)}")
        parts.append(f"\n数据预览（前 20 行）:")
        # 表头
        header = "| " + " | ".join(columns) + " |"
        separator = "|" + "|".join([" --- " for _ in columns]) + "|"
        parts.append(header)
        parts.append(separator)
        for row in data[:20]:
            formatted_row = "| " + " | ".join(str(v) if v is not None else "-" for v in row) + " |"
            parts.append(formatted_row)
        if len(data) > 20:
            parts.append(f"\n... 共 {len(data)} 行，仅展示前 20 行")

    return "\n".join(parts)


def _format_critic_summary(critic_result: dict) -> str:
    """格式化 Critic 评价摘要。"""
    if not critic_result:
        return "（未经过 Critic 质量评估，直接路径）"

    score = critic_result.get("quality_score", "N/A")
    passed = critic_result.get("passed", [])
    failed = critic_result.get("failed_checks", [])
    feedback = critic_result.get("feedback", "")

    lines = [f"质量评分: {score}"]
    if isinstance(passed, list) and passed:
        lines.append(f"通过检查项: {', '.join(str(p) for p in passed)}")
    if isinstance(failed, list) and failed:
        lines.append(f"未通过检查项: {', '.join(str(f) for f in failed)}")
    if feedback:
        lines.append(f"评审意见: {feedback}")

    return "\n".join(lines)


def _format_masked_data(masked_result: dict) -> str:
    """格式化脱敏后的数据结果。"""
    if not masked_result:
        return "（无脱敏数据）"

    columns = masked_result.get("columns", [])
    data = masked_result.get("data", [])
    masked_fields = masked_result.get("masked_fields", [])
    row_count = masked_result.get("row_count", len(data))

    lines = [f"行数: {row_count}"]
    if masked_fields:
        lines.append(f"已脱敏字段: {', '.join(masked_fields)}")

    if columns and data:
        lines.append("")
        header = "| " + " | ".join(columns) + " |"
        separator = "|" + "|".join([" --- " for _ in columns]) + "|"
        lines.append(header)
        lines.append(separator)
        for row in data[:20]:
            formatted_row = "| " + " | ".join(str(v) if v is not None else "-" for v in row) + " |"
            lines.append(formatted_row)

    return "\n".join(lines)


def _has_numeric_columns(columns: list[str], data: list[list]) -> bool:
    """检测数据中是否存在数值型列。"""
    if not columns or not data:
        return False

    numeric_keywords = (
        "count", "sum", "total", "amount", "avg", "rate",
        "percentage", "num", "price", "cost", "revenue",
        "qty", "quantity", "score", "value", "ratio",
        "salary", "age", "year", "month", "day",
    )

    for i, col in enumerate(columns):
        col_lower = col.lower()
        # 列名匹配
        if any(kw in col_lower for kw in numeric_keywords):
            # 验证至少有一个非空数值
            for row in data:
                if i < len(row) and row[i] is not None:
                    try:
                        float(str(row[i]).replace(",", "").replace("%", ""))
                        return True
                    except (ValueError, TypeError):
                        pass

    # 回退：直接检查任意列的值是否为数字
    for i, col in enumerate(columns):
        numeric_count = 0
        for row in data:
            if i < len(row) and row[i] is not None:
                try:
                    float(str(row[i]).replace(",", "").replace("%", ""))
                    numeric_count += 1
                except (ValueError, TypeError):
                    pass
        if numeric_count >= len(data) * 0.5 and len(data) >= 2:
            return True

    return False


def _get_category_column(columns: list[str]) -> int:
    """找出最适合作为分类轴（x 轴）的列索引。"""
    for i, col in enumerate(columns):
        col_lower = col.lower()
        if any(kw in col_lower for kw in ("name", "dept", "category", "type", "product", "region", "date", "time", "month", "year", "day", "title", "label")):
            return i
    return 0


def _get_time_column(columns: list[str]) -> int | None:
    """检测是否有时间/日期列，返回列索引。"""
    for i, col in enumerate(columns):
        col_lower = col.lower()
        if any(kw in col_lower for kw in ("date", "time", "month", "year", "day", "timestamp")):
            return i
    return None


def _generate_echarts_option(columns: list[str], data: list[list]) -> dict | None:
    """根据数据生成 ECharts option 配置。

    Returns:
        ECharts option dict，如果数据不适合可视化则返回 None。
    """
    if not columns or not data or len(data) < 2:
        return None

    # 找出数值列
    numeric_col_indices = []
    for i, col in enumerate(columns):
        col_lower = col.lower()
        is_numeric_name = any(
            kw in col_lower
            for kw in ("count", "sum", "total", "amount", "avg", "rate",
                       "percentage", "num", "price", "cost", "revenue",
                       "qty", "quantity", "score", "value", "ratio",
                       "salary", "age")
        )
        if is_numeric_name:
            numeric_col_indices.append(i)

    if not numeric_col_indices:
        return None

    # 找到分类列
    cat_idx = _get_category_column(columns)
    if cat_idx in numeric_col_indices and len(numeric_col_indices) > 1:
        numeric_col_indices.remove(cat_idx)

    if not numeric_col_indices:
        return None

    # 提取分类标签和数据系列
    categories = []
    for row in data:
        val = row[cat_idx] if cat_idx < len(row) else ""
        categories.append(str(val) if val is not None else "")

    time_idx = _get_time_column(columns)
    is_time_series = time_idx is not None

    series_list = []
    colors = ['#5470c6', '#91cc75', '#fac858', '#ee6666', '#73c0de', '#3ba272', '#fc8452', '#9a60b4']

    for si, ci in enumerate(numeric_col_indices):
        values = []
        for row in data:
            if ci < len(row) and row[ci] is not None:
                try:
                    values.append(float(str(row[ci]).replace(",", "").replace("%", "")))
                except (ValueError, TypeError):
                    values.append(0.0)
            else:
                values.append(0.0)

        series_list.append({
            "name": columns[ci],
            "type": "line" if is_time_series else "bar",
            "data": values,
            "itemStyle": {"color": colors[si % len(colors)]},
        })

    use_pie = (
        len(numeric_col_indices) == 1
        and not is_time_series
        and len(data) <= 10
    )

    option: dict[str, Any] = {
        "title": {
            "text": "数据可视化",
            "left": "center",
        },
        "tooltip": {
            "trigger": "item" if use_pie else "axis",
        },
        "legend": {
            "orient": "horizontal",
            "bottom": 0,
        } if not use_pie else {
            "orient": "vertical",
            "right": 10,
            "top": "center",
        },
        "color": colors,
    }

    if use_pie:
        pie_data = []
        for row in data:
            name_val = str(row[cat_idx]) if cat_idx < len(row) and row[cat_idx] is not None else ""
            num_val = 0.0
            if numeric_col_indices[0] < len(row) and row[numeric_col_indices[0]] is not None:
                try:
                    num_val = float(str(row[numeric_col_indices[0]]).replace(",", ""))
                except (ValueError, TypeError):
                    pass
            pie_data.append({"name": name_val, "value": num_val})
        option["series"] = [{
            "name": columns[numeric_col_indices[0]],
            "type": "pie",
            "radius": "60%",
            "data": pie_data,
            "emphasis": {
                "itemStyle": {"shadowBlur": 10, "shadowOffsetX": 0, "shadowColor": "rgba(0, 0, 0, 0.5)"},
            },
        }]
    else:
        if is_time_series:
            option["xAxis"] = {
                "type": "category",
                "data": categories,
                "axisLabel": {"rotate": 30} if len(categories) > 6 else {},
            }
            option["yAxis"] = {"type": "value"}
        else:
            option["xAxis"] = {
                "type": "category",
                "data": categories,
                "axisLabel": {"rotate": 30} if len(categories) > 6 else {},
            }
            option["yAxis"] = {"type": "value"}
        option["series"] = series_list

    return option


# ---------------------------------------------------------------------------
# 主节点函数
# ---------------------------------------------------------------------------

async def summary_node(state: AgentState) -> dict:
    """生成最终的综合回复。

    汇聚所有管线产出的结果，由主力 LLM 撰写自然语言答案。
    对于数据分析结果，自动检测数值列并生成 ECharts 可视化建议。

    Args:
        state: 当前 AgentState.

    Returns:
        {"final_answer": str}
    """
    logger.info("[summary] 开始生成最终答案")

    query: str = state.get("query", "")
    intent: str = state.get("intent", "simple_qa")
    execution_result: dict = state.get("execution_result", {})
    critic_result: dict = state.get("critic_result", {})
    masked_result: dict = state.get("masked_result", {})
    execution_error: str = state.get("execution_error", "")
    requires_human_review: bool = state.get("requires_human_review", False)
    sql_safe: bool = state.get("sql_safe", True)
    permission_pass: bool = state.get("permission_pass", True)
    hallucination_check_pass: bool = state.get("hallucination_check_pass", True)
    generated_sql: str = state.get("generated_sql", "")

    try:
        # 1. 格式化各类信息
        exec_text = _format_execution_result(execution_result)

        # 附加脱敏数据（如果存在且与执行结果不同源）
        masked_text = ""
        if masked_result:
            masked_cols = masked_result.get("columns", [])
            exec_cols = execution_result.get("columns", [])
            if masked_cols != exec_cols:
                masked_text = _format_masked_data(masked_result)
            else:
                masked_text = _format_masked_data(masked_result)

        critic_text = _format_critic_summary(critic_result)

        # 2. 构建提示词
        system_prompt = SUMMARY_SYSTEM_PROMPT

        user_prompt = SUMMARY_USER_TEMPLATE.format(
            query=query,
            intent=intent,
            execution_results=exec_text,
            critic_summary=critic_text,
            masked_data=masked_text if masked_text else "（无额外脱敏数据）",
        )

        # 3. 调用 LLM 生成答案
        llm = get_heavy_llm(temperature=0.3, max_tokens=4096)
        response = await llm.ainvoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        final_answer_raw = response.content if hasattr(response, "content") else str(response)

        # 4. 自动注入 ECharts（如果 LLM 未生成且数据适合可视化）
        final_answer = _maybe_inject_echarts(
            final_answer_raw,
            execution_result,
            masked_result,
        )

        # 5. 附加系统状态信息
        final_answer = _append_status_notes(
            final_answer,
            requires_human_review=requires_human_review,
            sql_safe=sql_safe,
            permission_pass=permission_pass,
            hallucination_check_pass=hallucination_check_pass,
            execution_error=execution_error,
            generated_sql=generated_sql,
        )

        logger.info("[summary] 最终答案生成完成")
        return {"final_answer": final_answer}

    except Exception as e:
        logger.exception(f"[summary] 生成最终答案失败: {e}")
        return {"final_answer": f"抱歉，生成最终答案时遇到错误: {str(e)}"}


def _maybe_inject_echarts(
    answer: str,
    execution_result: dict,
    masked_result: dict,
) -> str:
    """如果 LLM 尚未生成 ECharts 代码块且数据适合，则自动注入。"""
    if re.search(r"```echarts", answer, re.IGNORECASE):
        return answer

    # 优先使用脱敏数据，其次使用原始执行结果
    source = masked_result if masked_result else execution_result
    columns = source.get("columns", [])
    data = source.get("data", [])

    if not _has_numeric_columns(columns, data):
        return answer

    option = _generate_echarts_option(columns, data)
    if option is None:
        return answer

    option_json = json.dumps(option, ensure_ascii=False, indent=2)

    echarts_block = f"""

---

### 数据可视化

```echarts
{option_json}
```
"""
    return answer + echarts_block


def _append_status_notes(
    answer: str,
    requires_human_review: bool = False,
    sql_safe: bool = True,
    permission_pass: bool = True,
    hallucination_check_pass: bool = True,
    execution_error: str = "",
    generated_sql: str = "",
) -> str:
    """在答案末尾附加系统状态说明。"""
    notes = []

    if requires_human_review:
        notes.append("- 此查询结果已经过人工审核确认")

    if not sql_safe:
        notes.append("- 注意：SQL 安全校验未完全通过，结果可能受限")

    if not permission_pass:
        notes.append("- 注意：数据权限校验未完全通过，结果可能受限")

    if not hallucination_check_pass:
        notes.append("- 注意：RAG 幻觉检测未通过，答案可能包含推测内容，请谨慎参考")

    if execution_error:
        notes.append(f"- SQL 执行遇到错误: {execution_error}")

    if generated_sql and sql_safe:
        # 截断过长的 SQL 用于展示
        sql_preview = generated_sql.strip()[:500]
        if len(generated_sql.strip()) > 500:
            sql_preview += "\n-- ... (SQL 已截断)"
        notes.append(f"\n<details>\n<summary>执行的 SQL</summary>\n\n```sql\n{sql_preview}\n```\n</details>")

    if not notes:
        return answer

    return answer + "\n\n---\n\n**系统说明:**\n" + "\n".join(notes)
