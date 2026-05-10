---
name: finance_digest_style
description: NVDA 日报输出风格偏好（强红线：不给买卖建议）
type: feedback
tags: [finance, output]
created: 2026-05-11
updated: 2026-05-11
source: user-edited
revision: 1
---

# 红线（不可越界）

1. **不给买卖建议**：禁用「建议买入/卖出/减持/加仓」「目标价 $X」「止损 $Y」类语言。
2. **不算盈亏**：digest 内不出现任何含百分比 × 股数的金额计算。
3. **不喊单**：不出现「立即」「机会」「机不可失」类引导性词汇。

# 风格偏好

- 归因优先：每天的价格变动用「大盘 beta / 同业事件 / 公司特定」三段拆解。
- 事件 horizon：永远列出未来 30 天内的关键事件（财报、Fed、GTC）。
- Thesis 联动：每条 thesis 仅说「今日动作」（无新数据 / 强化 / 削弱 / 失效触发）。
- 术语翻译：英文术语首次出现给一行中文解释（capex / guidance / EPS / DCF 等）。
