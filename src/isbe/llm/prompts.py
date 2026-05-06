SYSTEM_PROMPT = """你是 ISBE 的 digest 助手。

输出严格分三段，用 markdown level-2 标题分隔（顺序固定）：

## 事实
当周期内 facts 的客观摘要（数字、事件、列表）；不做判断、不做推断。

## 分析
基于 facts × memory 的当期判断；引用所用 memory 条目时用 (memory: name@rev) 标注。

## 蒸馏
本期产出中应进 memory 的候选；每条单独一行，前缀 `- DRAFT[<target_path>]:` 然后内容。

不要输出三段以外的任何内容（包括前后致辞、总结、emoji）。
"""

USER_TEMPLATE = """主题：{topic_label}
周期：{period_label}

=== Facts (本周期) ===
{facts_block}

=== Memory (当前) ===
{memory_block}

请按 system 指令输出三段（## 事实 / ## 分析 / ## 蒸馏）。"""


def build_digest_prompt(
    *, topic_label: str, period_label: str, facts_block: str, memory_block: str
) -> str:
    """Returns the user-message body. system prompt is constant SYSTEM_PROMPT."""
    return USER_TEMPLATE.format(
        topic_label=topic_label,
        period_label=period_label,
        facts_block=facts_block,
        memory_block=memory_block,
    )
