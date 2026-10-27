# Docker 部署、升级与备份

## Docker

先准备宿主机 `data/`，确保容器内 UID/GID `1000:1000` 可读写。Linux 可用 `sudo install -d -m 700 -o 1000 -g 1000 data` 创建目录；复制主题时也需要相应权限。

```bash
docker build -t isbe .
docker run --rm -v "$PWD/data:/data" isbe init
cp examples/topics/nowcasting.yaml data/topics/
# ISBE_ADMIN_PASSWORD_FILE 指向仓库外、仅含初始密码的受保护文件。
docker run --rm -v "$PWD/data:/data" -v "$ISBE_ADMIN_PASSWORD_FILE:/run/admin-password:ro" \
  isbe account-add admin --password-file /run/admin-password --admin

docker run --rm -p 127.0.0.1:8080:8080 -v "$PWD/data:/data" \
  isbe serve --shared --bind 0.0.0.0:8080
```

在浏览器中保存设置后，从另一个终端运行：

```bash
docker run --rm -v "$PWD/data:/data" isbe collect nowcasting
docker run --rm -v "$PWD/data:/data" isbe digest nowcasting
```

每次运行共享同一个数据挂载。若采用环境变量配置，可在镜像名 `isbe` 前加入 `--env-file .env`；它会覆盖设置页保存的对应值。容器内监听 `0.0.0.0`，上面的端口映射仍只开放给宿主机本地访问。

构建镜像时 Docker Hub、PyPI 与 npm 镜像源的可选参数见 [Dockerfile](../Dockerfile)。前端在单独的 Node 构建阶段完成，运行镜像不含 Node。

### 升级

升级前停止服务，并备份整个数据目录（见下文「数据与备份」）：新版程序会按需迁移数据库，旧版不能打开已升级的数据库。不要让两个实例同时使用同一个数据目录。

```bash
git pull --ff-only
docker build -t isbe .
docker run --rm isbe --version
docker run --rm --entrypoint python isbe -c "import isbe.web.server; print('ok')"
```

确认 `--version` 显示预期版本、第二条命令输出 `ok` 后，再用新镜像重启服务。从早期 Rust 版升级见 [docs/upgrading-from-rust.md](upgrading-from-rust.md)。

## 数据与备份

```text
data/
├── isbe.db             # 本机/首位管理员数据、账户、会话与任务队列
├── workspaces/<用户ID>/ # 其他账户的数据库、主题、报告、图片与 memory
├── settings.json       # 设置页保存的服务配置，可能含密钥
├── topics/             # 网页或人工维护的主题 YAML
├── reports/<主题>/     # 各周期 Markdown 报告
├── research/<主题>/<版本>/ # 专题冻结原件、材料文本和引用清单
├── figures/            # 论文与导入报告的配图
└── memory/
    ├── topics/         # 人工维护的主题记忆
    └── .pending/       # 模型提出的待审阅草稿
```

正式 memory 是带 YAML frontmatter 的 Markdown。报告会读取该主题的 `topic` 笔记以及 `feedback`、`user` 笔记；`.pending/` 不参与读取。当前需人工审阅并维护正式笔记，尚无 `review` 命令。

升级数据库前先保留整个数据目录备份；旧版程序不能打开升级后的新数据库。备份前停止写入数据的命令与服务，再备份整个数据目录；仅备份数据库无法保留报告、图片与记忆。LLM 调用记录包含提示词和回复，备份应按研究数据保护。
