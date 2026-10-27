# 快速开始（详细）

准备 Python 3.14 与 [uv](https://docs.astral.sh/uv/)，依赖版本由 `uv.lock` 固定；构建前端另需 Node.js 22 或更高版本。也可以只用 Docker，见 [Docker 部署](deployment.md#docker)。采集需要访问相应数据源；筛选和报告需要一个可用的 LLM API。SQLite 随 Python 提供，无需安装数据库服务。

在仓库根目录执行：

```bash
npm --prefix frontend ci && npm --prefix frontend run build
uv run isbe --data data init
cp examples/topics/nowcasting.yaml data/topics/
uv run isbe --data data topics
uv run isbe --data data serve
```

Windows PowerShell 的复制命令为 `Copy-Item examples/topics/nowcasting.yaml data/topics/`。第一行构建 React 前端（输出到 `isbe/_assets/app/`）；前端代码更新后需重新构建并重启服务。未构建时，原有控制台页面照常可用，问题研究等 React 页面会提示先构建。

打开 [控制台](http://127.0.0.1:8080)，进入 [设置](http://127.0.0.1:8080/settings)，选择模型服务、填写 API Key 并保存。新安装默认使用 LLM 全文抽取：获取可用的 arXiv HTML，再使用已配置的模型提取事实，无需另一个 API Key。可按需配置 essaies、metrail-web 和 GitHub Token。默认模型名称是代码中的初始值，请根据服务实际支持的模型调整。

从设置进入 [主题管理](http://127.0.0.1:8080/topics)，可编辑刚复制的 `nowcasting`，或创建自己的论文主题。保存新主题后，主题页和空报告页都会给出「开始生成报告」的入口，打开[帮助](http://127.0.0.1:8080/help)页里的分步指引。按指引确认模型与主题配置，再点击「生成报告」。页面依次显示采集、筛选、全文补充和报告生成的进度，完成后可直接打开本期报告。

网页任务先保存到 SQLite 队列，再由一个后台执行器按提交顺序运行。任务中心显示排队位置、阶段、结果和历史；每个账户最多保留 5 个排队或运行任务，可以撤回尚未执行的任务。重复提交同一个请求不会重复执行；同一账户同一主题已有活动任务时返回该任务。离开或刷新帮助页不会重复提交；回到帮助页或在任务中心点「重新检查」可继续查看进度。重复生成同一周期会更新该周期的报告。

服务重启后继续处理排队任务，将此前执行中的任务标记为“已中断”，不自动重跑状态不明的模型调用。主题配置在提交时固定；若非敏感服务配置在排队期间改变，任务会明确失败并要求重新提交。模型密钥在执行时读取，不写入任务记录。CLI 与网页任务使用同一执行锁：已有任务运行时，CLI 会报告忙；CLI 执行期间网页任务继续排队。

CLI 仍可用于手动或外部调度：先执行 `collect`，再执行 `digest`。`digest` 依次执行筛选、全文补充和报告生成，**不会执行采集**。

如果提示“当前窗口无可用论文”，先在筛选页查看逐篇排除理由。`Gate dropped` 表示标题规则排除，`Judge dropped` 表示模型按检索意图和质量标准排除；标题没有命中“必须包含的关键词”时，即使摘要相关也不会送给模型。采集日志的 `new=0` 只表示没有新入库记录；筛选日志的 `to_judge=0` 只表示没有需要重新判定的候选。筛选日志同时显示 `window_candidates`、`cached` 和 `cached_kept` / `cached_gate_dropped` / `cached_judge_dropped`（非零项），用于区分无候选与复用既有判定。修改并保存筛选规则后会自动重新判定，无需删除历史数据；重复生成且不改规则会复用缓存。若希望覆盖标题未提及风等目标变量的方法论文，可清空标题必含词，并在质量标准中区分直接验证与方法参考，仍由模型排除无关内容。

仅采集不需要 LLM Key。`triage`、`enrich`、`digest` 和 `research` 需要模型配置；模型请求可能产生服务费用。
