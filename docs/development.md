# 开发与贡献

```bash
scripts/py-test                  # 全部测试：先在固定的 python:3.14-slim 容器中跑 Python，再跑 scripts/web-test
scripts/py-test tests/py/web     # 只跑选定的 Python 测试
scripts/web-test                 # 前端：web/ 的 node:test，以及 React 的 lint、类型检查、Vitest 与构建
npm --prefix frontend run dev    # React 开发服务器（:5173），把 /api 转发到本地 isbe serve（:8080）
```

前端正在从 `web/` 中的原生 HTML/CSS/JavaScript 迁移到 `frontend/` 中的 React（React 19、TypeScript、Vite，测试用 Vitest）。迁移期间两者按路由并存：首页 `/` 与 `isbe/web/routes.py` 的 `REACT_PATHS`（目前为 `/galaxies`、`/settings`、`/system`、`/help`、`/question`）由 React 应答，原来的总览移到 `/overview`，旧页面列在"系统"下，其余页面仍由 `web/` 应答；两者共用 `web/app.css` 的主题 tokens、`localStorage` 中的外观设置与登录会话。React 构建产物在 `isbe/_assets/app/`，不入库，随 Python 包打入镜像。在仓库中运行时直接读取 `web/` 与 `prompts/`，修改后重启服务即可；改动 `frontend/` 后需重新构建；Docker 镜像需重新构建。开发服务器默认转发到 `http://127.0.0.1:8080`，可用 `ISBE_DEV_TARGET` 修改；原有控制台页面请直接在 `isbe serve` 的端口打开。代码结构与约定见[架构说明](architecture.md)，各阶段的设计见 [DESIGN.md](DESIGN.md)。

`examples/research/handoff.json` 是合成的多源专题研究交接示例，可运行 `node --test tests/research-handoff.test.cjs` 检查；示例通过不代表对应的生产能力已实现。

欢迎提交 Issue 和 Pull Request。问题报告请附复现步骤、版本与脱敏日志；行为变更请附相应测试。不要提交密钥、个人数据目录或带凭据的服务地址。

采用 [MIT License](../LICENSE)。
