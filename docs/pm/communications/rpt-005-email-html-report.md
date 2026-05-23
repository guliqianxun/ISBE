---
pm_id: rpt-005
pm_type: report
workstream: notify
feature: ft-002
dispatch_ref: dsp-005
status: completed
created_at: 2026-05-23
---

# 报告: 邮件 HTML 渲染（学术墨色） — 代码部分完成

## 摘要

[dsp-005](dsp-005-email-html-mvp.md) 一次性收尾干净（执行约 25 分钟，81 tool uses）。
ft-002 的代码、模板、依赖、测试全部就位并已合并入 main。剩用户在 server 上 pull
+ restart worker + 触发 digest 看新邮件长得对不对，确认后 ft-002 即闭环。

## 交付

合并入 main，merge commit `8799c15`。

| SHA | Title |
|-----|-------|
| `861fdca` | chore(deps): add markdown + premailer for email HTML rendering |
| `e06b85c` | feat(notify): academic-ink HTML email template + jinja2 envelope |
| `abf289b` | feat(notify): multipart HTML alternative with plaintext fallback |

`git diff --stat`：

```
 pyproject.toml                          |   2 +
 src/isbe/notify/__init__.py             |  22 ++-
 src/isbe/notify/render.py               |  73 +++++++++  (new)
 src/isbe/notify/templates/email.html.j2 | 187 +++++++++++++++++++++++  (new)
 tests/test_notify_email.py              | 257 +++++++++++++++++++++++++++++++-
 uv.lock                                 |  54 +++++++
 6 files changed, 591 insertions(+), 4 deletions(-)
```

## 验收（agent 可视化证据节选）

agent 实跑 `render_html(...)` 把一段示例 markdown 渲染出来，HTML 输出关键行：

```html
<body style='margin:0; padding:0; background-color:#fdfcf8; color:#1a1a1a;
       font-family:"Source Serif 4", "Source Han Serif SC", Charter,
       Lora, Georgia, "Songti SC", STSong, serif;
       font-size:16px; line-height:1.65'>
<div class="container" style="max-width:640px; margin:0 auto; padding:24px;
       background-color:#fdfcf8">
  <div class="banner" style="border-top:2px solid #1a1a1a;
       border-bottom:2px solid #1a1a1a; padding:12px 0;
       text-align:center; margin-bottom:32px">
    <p style='...; color:#1a1a1a; ...'>ISBE · NOWCASTING</p>
    <p style='...; color:#666; ...'>2026-W21</p>
  </div>
  ...
  <a href="https://example.com"
     style="color:#0d6e6e; text-decoration:underline;
            text-decoration-style:dotted">a link</a>
  ...
  <pre style='background-color:#f4f1ea; border-radius:4px; padding:12px;
              color:#1a1a1a'>...</pre>
  ...
  <table style="border-collapse:collapse; width:100%;
                border-top:1px solid #1a1a1a;
                border-bottom:1px solid #1a1a1a">
    <th style="border-bottom:0.5px solid #e0ddd5; padding:6px 8px; ...">col1</th>
  </table>
</div>
</body>
```

品牌色全部正确 inline（premailer 干完活了）。无外链资源。

## 测试

- `tests/test_notify_email.py` **24 / 24 通过**（11 → 24，+13 新测试，覆盖 render / inline-style /
  multipart / fallback / render-failure / topic-period 注入 / artifact-missing path）
- 整套 `uv run pytest` **170 通过 / 3 失败**
- 3 失败仍是无 PG 的整合测试（`test_facts_db` + `test_nowcasting_facts`），pre-existing，不阻塞

## 状态变更

- `dsp-005`: pending → **completed**
- `ft-002`: 仍 🚧 WIP（等用户 server 上 pull + restart + 触发 digest 看新邮件落箱）
- v1.2 milestone 计划特性数 1 / 已完成 0（用户确认即 ✅）

## 用户下一步（在 server 上跑）

```bash
ssh lzhserver
cd /mnt/nas_iscsi/isbe
git pull --ff-only origin main

# 重建 radar worker（pyproject 改了 → 重建镜像；走阿里云镜像源避免 14 分钟下载）
docker compose -f docker-compose.yml -f docker-compose.server.yml --profile worker \
  build --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ radar-worker

docker compose -f docker-compose.yml -f docker-compose.server.yml --profile worker \
  up -d radar-worker

# 触发 digest，看邮箱
docker compose -f docker-compose.yml -f docker-compose.server.yml --profile worker \
  exec radar-worker radar topics run nowcasting --digest 2>&1 | tail -15
```

打开邮箱 —— 这次应是带学术墨色样式的 HTML：象牙白底、深青链接、加粗黑实线
banner、serif 字体的整篇周报。

满意 → PM 关 ft-002 + v1.2 milestone 收官；
不满意 → 把哪里别扭说一下，下一个 dispatch 微调（颜色、间距、字体 fallback 等）。
