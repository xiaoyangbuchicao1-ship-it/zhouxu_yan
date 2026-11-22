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

不要使用 `down -v`，它会删除任务和个人 Skill 所在的运行数据卷；也不要删除 `opensearch/vector_db_data/opensearch`。

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
data/                                  账户、材料、案例快照、数据库导出与备份
Docker volume: zhouxu_runtime-state     当前任务、事件、个人 Skill 与案件元数据
opensearch/corpus/                     法条语料
opensearch/import/                     法条向量和导入清单
opensearch/opensearch_delivery/        索引定义和导入工具
opensearch/vector_db_data/opensearch/  OpenSearch 运行快照（可选）
```

有运行快照时，初始化容器会校验两个别名和文档数，匹配后跳过导入；没有运行快照时，它会从条文和案例输入重建。任何条数或别名不匹配都直接失败，不伪造“空库启动成功”。

复制 `opensearch/vector_db_data/opensearch/` 时必须沿用创建该数据目录时的 OpenSearch 管理员密码；如果密码未知，删除/省略运行快照并从输入数据重新导入，再设置新密码。

不要把 `.venv-*`、`node_modules-*`、`.env`、模型权重、SQLite 运行文件和 OpenSearch 数据目录上传 GitHub。源码构建依赖 `uv.lock`、`package-lock.json` 和 Dockerfile，可在新机器重新生成镜像。

### 运行库的 Linux 数据卷迁移与导出

SQLite WAL 的各进程需要共享同一主机的共享内存与锁，参见 [SQLite WAL 文档](https://sqlite.org/wal.html)。API 的两个运行数据库 `runs.sqlite3` 与 `maritime-office.sqlite3` 现存于 Linux 数据卷，挂载到 `/app/backend/.runtime`。默认 Compose 项目名为 `zhouxu`，实际卷名为 `zhouxu_runtime-state`；改变项目名也会改变卷名，不能把新卷当成旧数据已丢失。

`ZHOUXU_RUN_STATE_DIR` 默认 `../../data/runtime`，现在仅是只读旧库导入来源（容器内 `/app/legacy-runtime`），迁移后不会随新任务更新。`data/relational/zhouxu.db` 和上传/生成文件仍保留原位置。禁止从 Windows Python 或数据库查看器打开正在由 Linux API 使用的 SQLite 文件。

**现有安装的一次性迁移：**先保留 API 停止时的原库及其 WAL/日志文件备份，构建新镜像，然后执行以下命令。迁移仅接受空目标卷，源库原样保留；逐库检查完整性、通过 SQLite Backup API 备份，并核对逻辑内容哈希。发现旧数据而目标尚未迁入时，正常启动会要求执行该命令。

```powershell
docker compose -f deploy/app/compose.yaml stop -t 30 zhouxu-api
docker compose -f deploy/app/compose.yaml build zhouxu-api
docker compose -f deploy/app/compose.yaml run --rm --no-deps --entrypoint python zhouxu-api /app/start/common/runtime_volume.py migrate
docker compose -f deploy/app/compose.yaml up -d --no-deps --wait zhouxu-api
```

本机已完成此次迁移，勿重复执行；迁移前原件保存在 `data/backups/runtime-storage-20261120/`。全新部署没有旧库时直接创建空库，不需迁移。更早版本含旧 Run 状态的，先导入数据卷，再按下一节显式升级状态，最后启动 API。

**跨电脑备份/搬迁：**停止 API 后，将数据卷导出到尚不存在或为空的专用目录。下面目录名每次导出需换新，已有导出不会被覆盖。

```powershell
docker compose -f deploy/app/compose.yaml stop -t 30 zhouxu-api
docker compose -f deploy/app/compose.yaml run --rm --no-deps --entrypoint python zhouxu-api /app/start/common/runtime_volume.py export --destination /app/data/runtime-export-20261120
```

保持 API 停止，连同 `data/relational/zhouxu.db`、对应上传/生成文件、资源配置一起复制，保证属于同一个停止时点。目标机配置 `ZHOUXU_RUN_STATE_DIR=../../data/runtime-export-20261120`，对空数据卷执行上面的 `migrate`，再走原启动入口。已存在数据的目标卷不会被覆盖，不能删除旧卷来绕过错误。原机完成复制后执行 `docker compose -f deploy/app/compose.yaml up -d --no-deps --wait zhouxu-api` 恢复服务。

容器升级或普通 `down` 保留数据卷；`down -v` 会删除数据卷。不要仅打包项目目录后声称带走了最新运行库，也不要把本机旧 `data/runtime/` 当成当前备份。

### 执行检查点 v2 的显式升级

v2 保存原始工作区基线的来源，并通过持久文件动作凭据恢复写入。旧 Run 不在执行时猜测基线；先迁移再启动新代码。只有缺失基线的历史记录标为 `migration_cutover`，表示迁移时点的文件清单，不冒充任务启动时的清单。原产物记录独立保留并校验实际文件。

本机 2026-11-20 已执行：97 个 Run、73 个会话、96 条业务投影；事件无需更改，16 个已有文件记录的哈希全部匹配。成对备份保存在 `data/backups/step1-checkpoints-20261120-v2/`，必须保留。再次只读预检应全部为 0；不要重复覆盖该备份目录。

```powershell
docker compose -f deploy/app/compose.yaml exec -T zhouxu-api python -m backend.infrastructure.persistence.migrate_execution_checkpoints --run-store /app/backend/.runtime/runs.sqlite3 --workspace-db /app/data/relational/zhouxu.db
```

其他部署升级时先构建新 API 镜像。停写后再做预检和应用，以下 `--backup-dir` 必须换成该次专用的新目录：

```powershell
docker compose -f deploy/app/compose.yaml stop -t 30 zhouxu-api zhouxu-web
docker compose -f deploy/app/compose.yaml run --rm --no-deps --entrypoint python zhouxu-api -m backend.infrastructure.persistence.migrate_execution_checkpoints --run-store /app/backend/.runtime/runs.sqlite3 --workspace-db /app/data/relational/zhouxu.db
docker compose -f deploy/app/compose.yaml run --rm --no-deps --entrypoint python zhouxu-api -m backend.infrastructure.persistence.migrate_execution_checkpoints --run-store /app/backend/.runtime/runs.sqlite3 --workspace-db /app/data/relational/zhouxu.db --apply --backup-dir /app/data/backups/checkpoints-v2-NEW-DATE
docker compose -f deploy/app/compose.yaml up -d --no-deps --wait zhouxu-api zhouxu-web
```

迁移前后核对原目标、正文、执行记录、事件序号、产物哈希和业务投影。回滚沿用下一节的 SQLite Backup API 成对恢复流程，将备份目录改为本次检查点备份，并恢复匹配的镜像；恢复前先保存当前两库及文件，避免丢失升级后的新任务。运行库仍只在 Linux 数据卷内操作，不跨 Windows 打开在线 WAL，不清库。

### 旧 Run 状态的显式升级与回滚

升级到标准 Run 状态时，旧值只做一次映射：`completed → succeeded`、`interrupted → paused`、`stopped → cancelled`。迁移覆盖 RunStore 会话快照、事件内嵌 Run 和业务库 `conversation_runs`；正文与业务步骤的 `completed` 不变。应用启动不会自动兼容或改写旧值，必须先完成迁移。

以下命令在项目根目录执行，使用已包含 `migrate_run_statuses.py` 的新 API 镜像。先停止所有会写这两库的 API/Web 实例，OpenSearch 可以保持运行。不要在迁移结束前重新启动应用。

```powershell
docker compose --env-file deploy/app/.env -f deploy/app/compose.yaml stop zhouxu-web zhouxu-api
docker compose --env-file deploy/app/.env -f deploy/app/compose.yaml run --rm --no-deps --entrypoint python zhouxu-api -m backend.infrastructure.persistence.migrate_run_statuses --run-store /app/backend/.runtime/runs.sqlite3 --workspace-db /app/data/relational/zhouxu.db
```

第二条只读预检会验证全部记录并报告待改数量，不写数据。确认后执行；`--apply` 和 `--backup-dir` 必须一起提供。备份目录必须是本次升级专用、尚不存在的目录，已有备份不可覆盖；下例目录是本机 2026-11-20 已完成迁移保留的真实备份，新一次升级请改用新目录。

```powershell
docker compose --env-file deploy/app/.env -f deploy/app/compose.yaml run --rm --no-deps --entrypoint python zhouxu-api -m backend.infrastructure.persistence.migrate_run_statuses --run-store /app/backend/.runtime/runs.sqlite3 --workspace-db /app/data/relational/zhouxu.db --apply --backup-dir /app/data/backups/milestone1-lifecycle-20261120
docker compose --env-file deploy/app/.env -f deploy/app/compose.yaml run --rm --no-deps --entrypoint python zhouxu-api -m backend.infrastructure.persistence.migrate_run_statuses --run-store /app/backend/.runtime/runs.sqlite3 --workspace-db /app/data/relational/zhouxu.db
```

复核应返回 `runtime_sessions: 0`、`runtime_events: 0`、`workspace_runs: 0`。备份由 SQLite backup API 生成，同一目录下的 `runs.sqlite3` 与 `zhouxu.db` 必须成对保留；默认宿主机位置为 `data/backups/milestone1-lifecycle-20261120/`。备份应纳入私有数据备份，不进入 Git。

如需回滚，保持 API/Web 停止，并使用同一批备份同时恢复两库。下面是 PowerShell 命令；Linux 使用相同 Python 内容，通过 heredoc 传给容器的 `python -`。恢复仍使用 SQLite backup API，不直接 `Copy-Item`/`cp` 覆盖运行库，也不手工删除 `-wal`、`-shm` 或 `-journal` 文件，避免遗留事务日志与数据库内容不匹配。

```powershell
@'
import sqlite3
from contextlib import closing
from pathlib import Path

backup = Path('/app/data/backups/milestone1-lifecycle-20261120')
pairs = [(backup / 'runs.sqlite3', Path('/app/backend/.runtime/runs.sqlite3')),
         (backup / 'zhouxu.db', Path('/app/data/relational/zhouxu.db'))]
for source, target in pairs:
    if not source.is_file():
        raise FileNotFoundError(source)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError(f'invalid backup: {source}')
for source, target in pairs:
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            dst.execute('PRAGMA wal_checkpoint(TRUNCATE)')
print('both databases restored')
'@ | docker compose --env-file deploy/app/.env -f deploy/app/compose.yaml run --rm -T --no-deps --entrypoint python zhouxu-api -
```

迁移成功后使用新代码启动；若恢复的是升级前状态，还必须恢复与备份匹配的旧代码/镜像，或再次迁移后才能启动新代码。确认两库均已处理后执行 `docker compose --env-file deploy/app/.env -f deploy/app/compose.yaml up -d`，用 `ps -a` 检查健康状态。不要用删除数据库的方式解决恢复失败。
