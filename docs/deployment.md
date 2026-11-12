# 开发与云端部署

推荐开发与云端沿用 Ubuntu LTS。当前应用以单个 FastAPI 进程、SQLite、文件目录和同机 OpenSearch 部署；数据与模型独立于 Git 准备。

## 环境和持久盘

准备 Python 3.11、uv、Node.js 20.19+ 或 22.12+、npm。Ubuntu 24.04 的默认 Python 不满足项目版本约束，应另外安装 3.11。不要从 Windows 复制虚拟环境和 node_modules。

建议应用根目录 `/srv/zhouxu`，把以下数据放在持久磁盘：

- `data/relational/`：业务 SQLite；`backend/.runtime/`：RunStore 与进程锁。
- `data/files/`：材料原件、解析缓存和会话文件。
- `data/cases/`：匹配配置的案例快照。
- `opensearch/corpus/`、`opensearch/import/`：法规正文、登记表与向量。
- `opensearch/vector_db_data/`：OpenSearch 数据；BGE-M3 本地模型另按配置准备。

模型凭证默认通过后端配置读取进程环境或本地 `.env`；OpenSearch 凭证文件默认在 `opensearch/opensearch_delivery/dev/.env`。根据服务器路径检查 `backend/config/defaults.toml` 的模型、数据和凭证路径。凭证不进入 Git、前端 VITE 配置或镜像。

## 依赖和任务执行

在应用根目录：

```sh
uv sync --project backend --extra rag --extra materials --locked
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Docker 任务环境使用版本化源码重建：

```sh
docker build -t zhouxu-task-runtime:0.2 deploy/task-runtime
```

这里的镜像只承担任务中的文件和命令执行，FastAPI 与前端依照各自部署方式运行。Provider 管理容器的网络、只读根文件系统、资源和会话工作区挂载。

云机无 Docker 时安装 Bash、LibreOffice Writer/Calc、Poppler、中文字体，并确保服务进程 PATH 包含应用虚拟环境中的 Python。应用的 materials extra 提供 python-docx 等文档库：

```sh
sudo apt-get update
sudo apt-get install -y bash libreoffice-writer libreoffice-calc poppler-utils fonts-noto-cjk
export ZHOUXU_EXECUTION_PROVIDER=native
```

环境覆盖作用于默认配置；显式传入的 TOML 应直接设置 `execution.provider="native"`。Native 命令以应用服务账号运行，适合受控专用云机。为互不信任用户开放任意代码执行前，应在现有 Provider 合同下接入独立沙箱并验证隔离。

## OpenSearch 与数据发布

本地 Docker 使用 [dev/compose.yaml](../opensearch/opensearch_delivery/dev/compose.yaml)；无 Docker 云机使用 [native/README.md](../opensearch/opensearch_delivery/native/README.md)。两者保持 OpenSearch 2.19、HTTPS 回环端口 19200 和相同的检索别名。

部署源码、索引定义、导入与发布校验工具均纳入 Git。语料和向量需单独复制到上述持久目录。应用启动不创建索引、不导入数据、不切换别名。

法规依次执行 validate → import → verify → acceptance → activate；发布工具及固定部署查询见 [OpenSearch 说明](../opensearch/opensearch_delivery/README.md)。当前数据包为 93,858 个法规块；以所部署包的清单校验为准。

案例快照当前为 `case_eb1be2c7fa1cd1fb`，825 案、3,156 块。配置匹配快照后，从应用根目录显式发布：

```sh
uv run --project backend --extra rag --locked python -m backend.infrastructure.corpus.import_cases
```

详细来源、校验与升级边界见[案例库说明](../backend/infrastructure/corpus/README.md)。升级先排空或暂停活动任务，再配套切换快照、索引和配置并重启；保留可回滚的数据与索引。

## 应用服务与前端托管

以专用低权限账号、应用根目录为工作目录运行单个 worker：

```sh
uv run --project backend --extra rag --extra materials --locked uvicorn backend.interfaces.api.app:create_app --factory --host 127.0.0.1 --port 8010 --workers 1
```

系统服务应使用虚拟环境的绝对路径与固定工作目录，给予业务 SQLite、RunStore、材料和工作区必要权限。应用账号不拥有 OpenSearch 数据目录。当前 RunStore 不支持多个 Uvicorn worker 或多个应用实例共享运行库。

将 `frontend/dist/` 交给 Nginx 等 Web 服务托管，配置 HTTPS、SPA 回退到 index.html，将 `/api/` 去掉前缀后反向代理到 `http://127.0.0.1:8010/`。事件流关闭代理缓冲，并给予足够读取时限。Vite 开发服务器和 preview 仅用于本地验证。

当前仓库提供任务镜像与 OpenSearch 部署资产；应用 systemd 服务、反向代理、域名和 TLS 按实际云环境配置，尚无整套一键上线编排。

## 开放服务前的实际检查

确认 `/healthz`、注册登录、账户权限和材料上传正常；用真实 work 会话完成“生成中文 Word → PDF 预览 → 下载 → 继续修改”。检查停止、进程重启恢复和事件流断线续接。Native 部署也应实际验证中文文档转换。

为 SQLite、RunStore、原件、案例包、配置和索引安排一致的备份与恢复。不要热复制 OpenSearch 数据目录；跨机器迁移使用输入重建或 snapshot/restore。单节点、零副本和开发证书不提供生产高可用。
