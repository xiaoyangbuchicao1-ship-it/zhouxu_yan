# 舟序部署合同

## 结论

当前采用一份源码、一个 Compose 项目、三个长期服务和一个一次性初始化容器：

```text
zhouxu-web  ->  zhouxu-api  ->  opensearch
```

`zhouxu-init` 负责启动时校验/导入法规、案例与信息卡独立索引，成功后正常显示 `Exited (0)`。

Windows、WSL、Linux 和 ECS 统一使用 Docker Compose。WSL2 只作为 Docker Desktop 的底层 Linux 环境，不再维护一套原生舟序依赖。

## 为什么这样拆

- `zhouxu-web` 只放构建好的前端静态文件和 Nginx 代理，浏览器只访问它的端口。
- `zhouxu-api` 包含 FastAPI、文档工具和 CPU PyTorch，镜像内部自带 Linux 依赖。
- `opensearch` 使用官方 OpenSearch 镜像，数据通过 bind mount 保存到项目外/项目持久目录。

应用镜像可以替换，OpenSearch 数据目录不随镜像重建。法规、案例与信息卡是同一集群里的三个独立索引和查询别名。原始信息卡、能力卡与论文 Skill 只读挂载在项目外，具体目录与处理流程见[科研资源接入](research-resources.md)。

## Compose 文件和端口

正式入口是 [`deploy/app/compose.yaml`](../deploy/app/compose.yaml)。默认只有 Web 端口发布到宿主机：

| 宿主机 | 容器 | 用途 |
|---:|---:|---|
| `127.0.0.1:8080` | `zhouxu-web:80` | 浏览器访问或本机 Nginx 代理 |
| 仅 Compose 网络 | `zhouxu-api:8010` | Nginx 内部代理 |
| 仅 Compose 网络 | `opensearch:9200` | API 和初始化容器内部访问 |

OpenSearch 不对公网发布。ECS 的 80/443、FRP 和 Cloudflare 隧道属于外层网络入口，不应把 9200 暴露出去。

## 配置文件

从 [`deploy/app/.env.example`](../deploy/app/.env.example) 复制出同目录 `.env`，只在部署机器填写：

```text
OPENSEARCH_INITIAL_ADMIN_PASSWORD  OpenSearch 管理员密码
DEEPSEEK_API_KEY                   后端模型凭证
ZHOUXU_API_IMAGE / ZHOUXU_WEB_IMAGE  本地构建或镜像仓库地址
ZHOUXU_WEB_PORT                    默认 8080
```

`.env` 被 Compose 作为容器环境文件，并以只读方式挂载到 `/app/runtime.env`。这是为了让初始化脚本、OpenSearch 发布脚本和后端读取同一份密码；不会把密码写入镜像。

镜像包含 CPU PyTorch，BGE-M3 权重由 `ZHOUXU_HF_CACHE_DIR` 指向宿主机并只读挂载到 API 容器。当前使用固定 revision `5617a9f61b028005a4858fdac845db406aefb181`，所以法规和案例向量检索可以离线运行。迁移时必须带上同一模型缓存，或把该变量改为目标机上的等价目录。

## Windows Docker Desktop

项目源码方式：

```powershell
Copy-Item deploy\app\.env.example deploy\app\.env
notepad deploy\app\.env
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml config --quiet
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml up -d --build
```

访问 `http://127.0.0.1:8080/`。如果 8080 已被占用，把 `.env` 的 `ZHOUXU_WEB_PORT` 改为 18080。`zhouxu-init` 是一次性初始化容器，正常完成后显示 `Exited (0)`；`zhouxu-api` 和 `zhouxu-web` 应保持 `healthy/running`。

使用镜像仓库：

```powershell
docker login <registry>
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml pull
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml up -d
```

停止但保留数据：

```powershell
docker compose --env-file deploy\app\.env -f deploy\app\compose.yaml down
```

不要使用 `down -v`，也不要删除 `opensearch/vector_db_data/opensearch`。

## Linux/ECS

安装 Docker Engine 和 Compose plugin 后，将项目放到持久磁盘，例如 `/srv/zhouxu`，复制并填写 `.env`：

```bash
cd /srv/zhouxu
chmod +x start/linux/*.sh
./start/linux/start.sh
```

这个脚本只执行 `docker compose up -d --build`，不会在主机上安装 Python、Node、CUDA 或创建 `.venv-linux`。镜像仓库部署则直接执行 `pull` 和 `up -d`。首次使用 bind mount 数据目录时，确认 OpenSearch 容器用户（通常 UID 1000）可写：

```bash
chown -R 1000:1000 opensearch/vector_db_data/opensearch
```

公网访问应由 ECS Nginx/FRP/Cloudflare 隧道转到 `8080` 或 Nginx 的 80/443；不要把 Compose 的 OpenSearch 端口公开。

## 数据与迁移

源码和运行数据分开：

```text
data/                                  账户、材料、案例快照、SQLite
opensearch/corpus/                     法条语料
opensearch/import/                     法条向量和导入清单
opensearch/opensearch_delivery/        索引定义和导入工具
opensearch/vector_db_data/opensearch/  OpenSearch 运行快照（可选）
```

有运行快照时，初始化容器会校验两个别名和文档数，匹配后跳过导入；没有运行快照时，它会从条文和案例输入重建。任何条数或别名不匹配都直接失败，不伪造“空库启动成功”。

复制 `opensearch/vector_db_data/opensearch/` 时必须沿用创建该数据目录时的 OpenSearch 管理员密码；如果密码未知，删除/省略运行快照并从输入数据重新导入，再设置新密码。

不要把 `.venv-*`、`node_modules-*`、`.env`、模型权重、SQLite 运行文件和 OpenSearch 数据目录上传 GitHub。源码构建依赖 `uv.lock`、`package-lock.json` 和 Dockerfile，可在新机器重新生成镜像。
