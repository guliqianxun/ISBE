# 命令

在仓库目录中，以下 `isbe` 写作 `uv run isbe`；Docker 镜像的入口就是 `isbe`，见 [Docker 部署](deployment.md#docker)。

| 命令 | 作用 |
| --- | --- |
| `isbe init` | 创建数据目录与数据库；可重复运行 |
| `isbe topics` | 校验并列出主题 |
| `isbe collect [主题…]` | 采集论文、RSS 与 GitHub 仓库动态 |
| `isbe triage [主题…]` | 对窗口内的条目进行筛选 |
| `isbe enrich [主题…]` | 补充待评论文的全文、配图与事实卡片 |
| `isbe digest [主题…]` | 筛选、补充全文、生成论文报告 |
| `isbe research <清单.json>` | 从固定材料摘录生成中文专题，冻结原件与引用；同版本不覆盖 |
| `isbe sources …` | 配置、发现并采集 RSS／网页来源，管理联网检索方案；不调用模型，见[来源接口](source-interface.md) |
| `isbe media-weekly <方案 ID> --topic <主题> --week-end <日期>` | 把已保存的检索候选整理成不可变的媒体周报草稿；不调用模型 |
| `isbe media-briefing <编辑稿.json> --topic <主题> --source-period <期号>` | 发布编辑稿行业周报，见[媒体材料与行业周报](research.md#媒体材料与行业周报) |
| `isbe question "<问题>" [--lang en] [--from 年] [--to 年]` | 研究一个问题：生成检索计划、检索 essaies、模型精筛候选论文；运行同时出现在控制台，见[问题研究 API](question/api.md) |
| `isbe question-survey <运行 ID> [--max-papers N] [--no-fulltext]` | 从问题运行纳入的论文写综述报告：有 arXiv 全文的读全文并抽取核验事实，其余只读摘要并如实标注；报告进书架，见[综述报告](question/survey.md) |
| `isbe serve [--bind 地址] [--shared] [--secure-cookies]` | 启动单机控制台或共享登录服务 |
| `isbe settings import <文件>` / `isbe settings export [--secrets]` | 从 JSON 文件导入服务设置（先整体校验再保存）/ 导出当前生效的设置，默认不含密钥；见 [settings.md](settings.md) |
| `isbe account-add <用户名> --password-file <文件> [--admin]` | 部署者创建账户；首次共享启动需管理员 |
| `isbe import-reports <目录>` | 导入 `<主题>/<周期>/latest.md` 布局的旧版报告 |

所有命令支持 `--data <目录>`。省略主题名时处理所有启用的主题；具体参数见 `isbe --help` 或 `isbe <命令> --help`。
