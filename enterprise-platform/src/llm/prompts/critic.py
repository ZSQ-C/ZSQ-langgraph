"""Critic Agent Prompt - 确定性溯源格式化"""
CRITIC_FORMAT_PROMPT = """根据以下溯源检查结果，生成自然语言反馈。

## 检查结果
{verdict}

## 输出格式
如果全部通过：输出"所有检查项通过。"
如果有失败项：说明缺少什么(missing)和多余什么(superfluous)，给出具体修正建议。
"""
