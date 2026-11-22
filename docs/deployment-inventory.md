# 舟序部署清单

更新时间：2026-11-18

## 一句话结论

源码只有一份；运行时只启动一套 Docker Compose 应用。Windows、WSL、Linux 和 ECS 都运行同样的三个容器。

```text
Docker Compose：zhouxu-web + zhouxu-api + opensearch
```

WSL2 是 Docker Desktop 的底层 Linux 环境，不是第三套舟序。当前 Linux 启动脚本只调用 Compose，不再创建 `backend/.venv-linux`。

## 当前正式目录

```text
真正的舟序/
├─ backend/                       FastAPI 源码、uv.lock
├─ frontend/                      React/Vite 源码、package-lock.json
├─ data/                          账户、材料、案例快照、SQLite（不进 Git）
├─ opensearch/
│  ├─ corpus/                     法条语料（数据包）
│  ├─ import/                     法条及信息卡向量和清单（数据包）
│  ├─ vector_db_data/opensearch/  OpenSearch 运行快照（可选数据包）
│  └─ opensearch_delivery/        索引定义、导入脚本和查询
├─ deploy/app/
│  ├─ compose.yaml                三容器编排
│  ├─ Dockerfile                  前端构建 + 后端运行镜像
│  ├─ nginx.conf                  前端静态文件和 /api 代理
│  ├─ .env.example                部署配置模板
│  └─ README.md                   迁移与启动说明
├─ start/
│  ├─ windows/                    Docker Desktop 入口
│  ├─ linux/                      Docker Compose 入口
│  └─ common/                     Compose 共用的索引就绪检查
└─ docs/                          架构和部署说明
```

宿主机 Python、Node、uv、`.venv-*` 和 `node_modules-*` 都不是交付物；依赖在 Linux 容器镜像内构建。

## 三个 Compose 服务

| 服务 | 内容 | 数据/网络边界 |
|---|---|---|
| `zhouxu-web` | Nginx + 前端静态文件 | 发布宿主机 `8080` |
| `zhouxu-api` | FastAPI + 文档工具 + CPU PyTorch | 只在 Compose 网络监听 `8010` |
| `opensearch` | OpenSearch 2.19 | 只在 Compose 网络监听 `9200`，数据 bind mount |

`zhouxu-init` 是一次性初始化任务，不是第四个长期服务：它等待 OpenSearch 健康，校验/导入法规、案例和信息卡，成功后退出码为 0。`models/` 保存只读模型缓存与论文模板字体；Docker Linux 数据卷 `zhouxu_runtime-state` 保存任务记录和个人 Skill 元数据，避免容器重建丢失任务。`data/runtime/` 仅是旧库导入来源，跨电脑迁移先按 [部署合同](deployment.md#运行库的-linux-数据卷迁移与导出)导出当前卷。

## 三个逻辑库，一个 OpenSearch

| 逻辑库 | 索引/别名 | 输入 |
|---|---|---|
| 法条库 | `maritime_law_chunks_v2` / `maritime_law_chunks_read` | `opensearch/corpus`、`opensearch/import` |
| 案例库 | 当前案例快照 ID / `maritime_cases_read` | `data/cases/<snapshot>/` |
| 信息卡库 | `maritime_information_cards_v4` / `maritime_information_cards_read` | 项目外只读 MinerU 统一信息卡，`opensearch/import/information_cards/` 保存已校验向量与发布清单 |

一个 OpenSearch 集群承载三个独立来源索引，数据目录独立于应用镜像。外部能力卡与完整论文 Skill 只读挂载给 API，不混入上述索引。完整新增目录及验收场景见[科研资源接入](research-resources.md)。

## 另一台 Windows 的交付包

源码构建至少包含：

```text
deploy/app/
backend/（源码、pyproject.toml、uv.lock）
frontend/（源码、package.json、package-lock.json）
start/common/
data/
opensearch/corpus/
opensearch/import/
opensearch/opensearch_delivery/
```

已有索引快照可额外携带 `opensearch/vector_db_data/opensearch/`。不复制：

```text
backend/.venv-*
frontend/node_modules-*
deploy/app/.env（在目标机单独填写）
模型权重、日志和运行缓存
```

镜像仓库方案可以省略 backend/frontend/start 的源码，只保留 Compose、.env、数据包和索引发布资产。

## 真实启动命令

源码构建：

```powershell
Copy-Item deploy\app\.env.example deploy\app\.env
notepad deploy\app\.env
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml config --quiet
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml up -d --build
```

另一台 Windows 电脑也可以直接运行项目内的一键入口（只要求 Docker Desktop）：

```powershell
.\start\windows\start-compose.cmd
```

镜像仓库：

```powershell
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml pull
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml up -d
```

访问 `http://127.0.0.1:8080/`，查看：

```powershell
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml ps -a
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml logs zhouxu-init
```

停止时使用 `down`，不要使用 `down -v`。

## 端口

| 端口 | 来源 | 作用 |
|---:|---|---|
| `127.0.0.1:8080` | Compose web 容器 | Docker 页面/本机反向代理 |
| `9200` | Compose 网络内部 | OpenSearch，不公网开放 |
| ECS 80/443 | 外部 Nginx/FRP/Cloudflare | 公网入口 |
