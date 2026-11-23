# 舟序跨机部署与镜像发布指南

本文档说明如何通过 **GitHub 源码仓库 + 阿里云 ACR 镜像仓库 + 本地资产包 (`zhouxu-assets.zip`)**，在另一台只安装 Docker Desktop 的电脑上快速拉起舟序系统。

---

## 1. 仓库资产分布

代码与轻量配置进入 Git，大模型与重型语料资产不入库：

- **进入 Git**：
  - `backend/`、`frontend/`、`start/`
  - `deploy/`（`Dockerfile`、`compose.yaml`、`nginx.conf`、`.env.example`、`seccomp-execution.json`）
  - `opensearch/opensearch_delivery/`（索引 Schema 与校验/发布脚本）
  - `tests/`、`docs/`、`README.md`、`plan.md`、`.github/workflows/deploy.yml`、`.gitignore`、`.dockerignore`
- **不进 Git（打包进 `zhouxu-assets.zip`）**：
  - `data/`（案例、用户文档，以及 `data/research/{information_cards,capability_cards,papers}`）
  - `models/`（离线 `huggingface/` BGE-M3 向量权重与 `fonts/` 论文编译字体）
  - `opensearch/` 下的 `corpus/`、`import/`、`vector_db_data/`
  - `deploy/app/.env`（真实密钥与 OpenSearch 密码，**绝不入库，也不要发给他人**）

---

## 2. GitHub Actions 自动构建并推送到阿里云 ACR

仓库已包含 [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml)。每当代码推送到 `main` 分支（或手动触发 `workflow_dispatch`）时，会自动构建并推送两个镜像：

- `zhouxu-api`（`target: api`）
- `zhouxu-web`（`target: web`）

在 GitHub 仓库 `Settings -> Secrets and variables -> Actions` 中配置：

| 类型 | 名称 | 说明 |
| --- | --- | --- |
| Secret | `ACR_USERNAME` | 阿里云容器镜像服务用户名 |
| Secret | `ACR_PASSWORD` | 阿里云 ACR 访问凭证/密码 |
| Variable（可选） | `ACR_REGISTRY` | 默认 `registry.cn-hangzhou.aliyuncs.com` |
| Variable（可选） | `ACR_NAMESPACE` | 默认 `zhouxu`（建议改为你自己的 ACR 命名空间） |

---

## 3. 在源电脑打包迁移资产

### 3.1 导出命名卷中的活跃 SQLite 数据（如账号、项目、会话与案件）

运行栈使用的 SQLite 位于 Docker Linux 命名卷 `zhouxu_runtime-state`。迁移前先导出到临时目录（或目标 `data/runtime`）：

```powershell
# 查看或导出当前运行卷（请先确保栈已停止或处于维护窗口）
python start/common/runtime_volume.py export --target data/runtime
```

> 若无需迁移历史对话与账号，可保持 `data/runtime` 为空目录，新电脑会自动初始化空库。

### 3.2 打包 `zhouxu-assets.zip`

将以下目录打包进 `zhouxu-assets.zip`（保留根目录相对层级）：

```text
zhouxu-assets.zip
├── data/
├── models/
└── opensearch/
    ├── corpus/
    ├── import/
    └── vector_db_data/
```

---

## 4. 在新电脑一键拉起

新电脑只需安装 **Docker Desktop**（无需 Python / Node）：

1. 克隆 GitHub 仓库并进入仓库根目录，将 `zhouxu-assets.zip` 解压覆盖到仓库根目录（保留其中的 `opensearch/opensearch_delivery/`）。
2. 复制配置模板并填入密钥与镜像地址：

```powershell
Copy-Item deploy/app/.env.example deploy/app/.env
```

编辑 `deploy/app/.env`，填写真实 `DEEPSEEK_API_KEY` 等密钥、OpenSearch 密码，并将镜像指向阿里云 ACR：

```dotenv
ZHOUXU_API_IMAGE=registry.cn-hangzhou.aliyuncs.com/<你的命名空间>/zhouxu-api:latest
ZHOUXU_WEB_IMAGE=registry.cn-hangzhou.aliyuncs.com/<你的命名空间>/zhouxu-web:latest
ZHOUXU_WEB_PORT=18080
```

3. 确保存在 `data/runtime` 目录（或已放置导出的 `runs.sqlite3` / `maritime-office.sqlite3`），执行两步拉起：

```powershell
New-Item -ItemType Directory -Force data/runtime | Out-Null
docker compose --env-file deploy/app/.env -f deploy/app/compose.yaml pull
docker compose --env-file deploy/app/.env -f deploy/app/compose.yaml up -d --wait
```

启动后：
- 若带入了 `opensearch/vector_db_data/`，`zhouxu-init` 巡检通过后立即放行，无需重建向量索引；
- 若带入了导出的 `data/runtime/*.sqlite3`，`runtime_volume.py` 会在新卷尚未就绪时自动迁移导入；
- 浏览器访问 `http://127.0.0.1:18080` 即可使用。
