# ADR — OSS-survey-first for hard infrastructure problems

**Date**: 2026-05-14
**Status**: Accepted
**Supersedes / amends**: nothing
**Scope**: applies to all "hard infra" subproblems hit during v1 / v2 work

## 决策

对**硬基础设施问题**（反爬、签名算法、协议适配、长尾平台 API），动手前**先做 OSS 调研**：

1. 找一找 GitHub / RSSHub / 雪人 / Awesome-* 清单里有没有人解过同样问题
2. 评估候选 OSS 的"最近一次提交时间 / star / 实际维护活性"
3. **明确：自建的 ROI 是不是 OSS 的 2× 以上**？不到 2× 直接用 OSS

只有以下情况绕过 OSS 调研：
- 问题在我们本仓库的领域逻辑内（business logic）—— 自己写永远更对位
- OSS 调研耗时已经 > 自建估时的 30% —— 那不如直接自建
- OSS 候选都已死（最近提交 > 18 个月），且我们没能力维护 fork

## 起因

2026-05-13 末打算硬刚知乎 `x-zse-96` 签名（自己写 sm3 + 解 zse-ck JS 算法 + Crawl4AI patchright stealth 兜底），估时 2 周。

2026-05-14 用户拍板："网上应该有开源方案，没必要自己重头死磕。"

调研 1 小时发现：

- **RSSHub master 2026-03-07 起自带** `x-zse-96` 签名实现 (`lib/routes/zhihu/utils.ts` + `execlib/x-zse-96-v3.ts`)
- 而且**自动取 `__zse_ck`**（从 `https://static.zhihu.com/zse-ck/v3.js` 拉出来跑）
- 自建工作直接归零，**剩下只是粘 cookies**

后来又遇到知乎 risk-control 卡 RSSHub（即便 d_c0+z_c0 都齐还是 403），第二次走 OSS 调研：

- `lilydjwg/morerssplz`（Python tornado，0.4 版本，2025-03-13 还在维护）
- HTML 抓取绕开 API 签名整条线
- **30 分钟从 "compose build from git" 到生产入库**

## 后果

**好的**：

- 知乎接入 2 周 → 1 天
- 整套调研流程沉淀进 [[zhihu_scraping]] memory（Claude memory）和 `docs/howto/zhihu-cookies.md`
- 学会区分 "API 签名层" vs "risk-control 层" 是两层独立屏障

**风险**：

- OSS 上游死了我们就跟着死。缓解：(a) 锁住 verified commit (`isbe-morerssplz` 用 `build: <git URL>` 隐式跟 master 走，已经识别到风险但暂不修)；(b) RSSHub 用 `:latest` tag，每月 manual pull 一次看变化
- OSS 调研可能"漏掉一个更好的方案"。缓解：调研有时间盒，1-2 小时找不到就 fallback 到自建估算
- 引入 OSS 等于引入新的可观察性 / 升级 / 运维表面。缓解：只接 docker-compose 服务，不入 Python 依赖；这样退出成本低

## 适用判断

> "这个问题是不是有人已经解过 5 遍？"

✅ 适用（先 OSS 调研）：
- 反爬 / 签名 / 指纹 / 验证码（知乎、小红书、微博）
- 协议适配（IMAP / OAuth / WebDAV / S3 兼容）
- 数据源标准化（RSS、ICS、SEC EDGAR）
- 基础设施（OTel collector、cron scheduler、queue）

❌ 不适用（直接自建）：
- 我们独有的 prompt / 5 段 digest 模板 / topic 抽象
- memory frontmatter schema / `.pending` review 流
- 系统提示工程 / domain prompt
- 测试 / E2E

## 历史回放（用户视角）

| 日期 | 决策瞬间 | 影响 |
|---|---|---|
| 2026-05-13 | 末提到要硬刚知乎签名 | 估 2 周自建 |
| 2026-05-14 早 | 用户："网上应该有开源方案" | 调研 1h 发现 RSSHub master 已支持 |
| 2026-05-14 中 | 决策：用 RSSHub + cookies | 砍掉 2 周自建 |
| 2026-05-15 早 | RSSHub 仍 403 | 第二次走 OSS 调研，发现 morerssplz |
| 2026-05-15 午 | morerssplz 入栈 | 30 分钟落地，绕开签名+风控整条线 |

这条原则不写下来，下次遇到反爬问题 LLM 默认会"先自建，遇阻再调研"，重复 5-13 的弯路。

## 应用方法

下次遇到"硬基础设施问题"，开 issue / 起 task 时先写一段：

```
## OSS survey
- 候选 1：<repo URL, last commit, 适配度>
- 候选 2：...
- 候选 3：...

## 决定
- ☐ 直接用 OSS X
- ☐ 自建（理由：<2× ROI 没满足 / OSS 都死了 / 在 business logic 内>）
```

PROGRESS.md 里"明天的待办"按这条原则筛过：硬基础设施类的 todo（"接入 X 平台"）默认走 OSS 调研，业务类的不走。
