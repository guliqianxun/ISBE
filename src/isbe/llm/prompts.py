SYSTEM_PROMPT = """你是 ISBE 的 digest 助手。

输出严格分三段，用 markdown level-2 标题分隔（顺序固定）：

## 事实
当周期内 facts 的客观摘要（数字、事件、列表）；不做判断、不做推断。

## 分析
基于 facts × memory 的当期判断；引用所用 memory 条目时用 (memory: name@rev) 标注。

## 蒸馏
本期产出中应进 memory 的候选；每条单独一行，格式严格如下：

`- DRAFT[<target_path>]: <内容>`

**target_path 必须**：
- 以 `topics/`、`reading/`、`feedback/`、`user/`、`reference/` 之一开头
- 以 `.md` 结尾
- `reading/` 下要带 ISO 周路径：`reading/<YYYY>/W##/<id>.md`

**正确示例**（照抄格式，只换内容）：
- DRAFT[topics/nowcasting.theses.md]: 新论点：diffusion 在 lead-time>90min 仍 mode-collapse
- DRAFT[reading/2026/W19/2604.12345.md]: PaperX 已自动标注（一句话评价）
- DRAFT[feedback/research_digest_style.md]: 用户偏好补充（一句话）

**错误示例**（不要这样写）：
- DRAFT[research_focus@rev2]: ...   ← 没有目录前缀、没有 .md、含 @rev
- DRAFT[nowcasting.research_logs]: ... ← 同上

如本周期没有值得蒸馏的，## 蒸馏 段写 `(本期无蒸馏建议)`，不要硬凑。

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
