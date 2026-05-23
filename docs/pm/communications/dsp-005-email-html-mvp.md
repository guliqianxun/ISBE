---
pm_id: dsp-005
pm_type: dispatch
target: notify
action: develop
feature: ft-002
priority: P1
status: completed
created_at: 2026-05-23
deadline: 2026-05-27
completed_at: 2026-05-23
---

# 任务: 邮件 HTML 渲染 MVP —— 学术墨色

## 背景

[ft-002](../features/ft-002-email-html-rendering.md) 已锁品牌方向「学术墨色」与
技术栈（markdown + premailer + jinja2 + multipart）。本派遣实现 MVP 代码 + 测试。
线上实测仍是用户在 server 上做（与 dsp-003 同样模式）。

## 要求

### 1. 新增依赖

在 `pyproject.toml` 的 `dependencies` 数组追加（保持字母序合理位置）：

```
"markdown>=3.6",
"premailer>=3.10",
```

跑 `uv lock` 更新 `uv.lock`。两个新依赖都是纯 Python，没系统依赖。

### 2. 渲染模块 `src/isbe/notify/render.py`（新文件）

实现一个 `render_html(*, topic_label, period_label, artifact_md, artifact_path) -> str` 函数：

- 用 `markdown.markdown(artifact_md, extensions=["tables", "fenced_code", "footnotes"])` 解析
- 用 jinja2 模板渲染 envelope（顶 banner + 底 footer + 中间嵌入 markdown HTML）
- 模板里 `<style>` 段定义品牌 CSS（见 ft-002「品牌规范」段），元素全部 class 化
- 用 `premailer.transform(html)` 把 `<style>` 转 inline
- 失败必须 raise（让上层决定 fallback）—— 本函数不吞异常

**brand CSS 必须精确实现 ft-002 规范：**
- 背景 `#fdfcf8`，正文 `#1a1a1a`，次要 `#666`，accent `#0d6e6e`，rule `#e0ddd5`，code-bg `#f4f1ea`
- 字体栈精确照搬 ft-002 那两段
- 容器 max-width 640px 居中，padding 24px
- 行高 1.65
- H1/H2/H3 全部 serif、同色（不染 accent）
- 引用块左 3px accent 实线
- 表格上下 ink 实线、行间 rule 线
- 链接 accent + dotted underline
- 代码块 code-bg + 4px 圆角 + 12px padding

模板里 envelope 文本：
- 顶 banner 上下各 1 条 2px ink 实线，中间居中两行：`ISBE · {{ topic_label_upper }}` / `{{ period_label }}`
- 底 footer：居中 `·  ·  ·` + 下一行 ink-2 灰字 `ISBE · self-hosted research radar`

### 3. 修改 `src/isbe/notify/__init__.py`

`send_digest_notification` 内：

- 当 `artifact_path` 可读时，构造 multipart：
  ```python
  msg.set_content(plaintext_body)                  # 现有 plaintext（保持）
  try:
      html_body = render_html(...)
      msg.add_alternative(html_body, subtype="html")
  except Exception as e:
      _warn(f"HTML render failed; sending plaintext only: {e}")
      # 不 add_alternative，仍发 plaintext
  ```
- 当 `artifact_path` 不可读时（现有 excerpt 路径）：保持单 part plaintext，**不**走 HTML 路径
- 契约不变：`notify` 永不 raise；env 未配 no-op；HTML 失败 fallback 到 plaintext

### 4. 测试 `tests/test_notify_email.py`（扩充）

**TDD：每一个新行为先写 failing 测试**：

- 测 `render_html` 输出含特定 inline style（如 `style="...color: #0d6e6e..."` 在 `<a>` 元素上）
- 测 `render_html` 输出含 `font-family` 字体栈
- 测渲染 markdown 表格 → `<table>` + inline 边线
- 测代码块 → `<pre>` 含 code-bg 背景
- 测 `send_digest_notification` 产出的 `EmailMessage`：
  - `is_multipart()` 为 True 当 `artifact_path` 可读
  - 两个 part：`text/plain` + `text/html`
  - HTML part 含 `topic_label` 全大写、`period_label`
- 测 HTML 渲染异常时：邮件仍发出（plaintext-only），无 raise
- 现有 11 个测试不能破

`uv run pytest` 全套绿。notify 测试从 11 增到 ≥ 18。

### 5. 分支 + commit

分支 `feat/email-html-brand`。3 个 conventional commit：

1. `chore(deps): add markdown + premailer for email HTML rendering`
2. `feat(notify): academic-ink HTML email template + jinja2 envelope`
3. `feat(notify): multipart HTML alternative with plaintext fallback`

每条结尾加：
```
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

## 严格不做

- 不动 `is_configured()` / no-op 契约
- 不动 digester（不改 markdown 内容生成）
- 不引外部资源（字体、图片、CSS CDN）
- 不实现多收件人（→ bl-005）
- 不动 server `.env` / 不在服务器做事
- 不 push、不 merge、不动 main

## 验收标准（你 PM 验收）

agent 回报必须包含：

- 分支名 + 3 个 commit short SHA（**结束前 `git log` 确认全部 commit 可见**）
- `git log --oneline main..HEAD` 输出（3 行）
- `git diff --stat main..HEAD` 输出（应见 `pyproject.toml`、`uv.lock`、
  `src/isbe/notify/__init__.py`、`src/isbe/notify/render.py`（new）、
  `tests/test_notify_email.py`、可能还有 `src/isbe/notify/templates/email.html.j2` (new)）
- `uv run pytest tests/test_notify_email.py -v` 最后 5 行（应见 ≥ 18 passed）
- `uv run pytest` 最后 2 行（应见 ≥ 164 passed，3 失败照旧是无 PG 的整合测试）
- **从测试里 dump 出一份示例 HTML 输出**（`uv run python -c "..."` 把一个 sample markdown 渲染出来），贴 HTML 前 30 行作为可视检查的证据
- 任何偏离明确写出

`git status` 在结束前必须 clean。

## 参考

- Feature: [ft-002](../features/ft-002-email-html-rendering.md)（含完整品牌规范）
- 前序: [dsp-002](dsp-002-email-digest-mvp.md) + [rpt-002](rpt-002-email-mvp-report.md)
- 现有 notify: `src/isbe/notify/__init__.py`
- markdown 库: https://python-markdown.github.io/
- premailer: https://github.com/peterbe/premailer

## 结果（2026-05-23）✅ completed

- 分支 `feat/email-html-brand`，3 个 commit：
  - `861fdca chore(deps): add markdown + premailer for email HTML rendering`
  - `e06b85c feat(notify): academic-ink HTML email template + jinja2 envelope`
  - `abf289b feat(notify): multipart HTML alternative with plaintext fallback`
- 合并入 main：merge commit `8799c15`
- 6 文件改动（+591 / −4）：
  - `pyproject.toml` + `uv.lock`（markdown ≥3.6, premailer ≥3.10）
  - `src/isbe/notify/render.py`（new，73 行）
  - `src/isbe/notify/templates/email.html.j2`（new，187 行）
  - `src/isbe/notify/__init__.py`（+18 / −4，multipart 改造）
  - `tests/test_notify_email.py`（+253 / 0）
- 测试：notify 单测 **24/24 通过**（11 → 24，新增 13 个）；整套 pytest **170 通过 / 3 失败**（3 失败仍是无 PG 的整合测试，pre-existing）
- 验收（agent 实跑可视检查）：
  - [x] HTML 含 inline brand 颜色：`#fdfcf8` bg / `#1a1a1a` 正文 / `#0d6e6e` 链接 / `#f4f1ea` 代码块
  - [x] Banner 上下 `2px solid #1a1a1a` 实线
  - [x] 表格 ink 顶/底 + rule 行间
  - [x] 链接 accent + dotted underline
  - [x] 字体栈精确照搬 ft-002 规范
  - [x] multipart text/plain + text/html 双轨
  - [x] render 失败时 fallback 到 plaintext，notify 不 raise
- 报告：[rpt-005](rpt-005-email-html-report.md)
