# 舟序启动入口

项目运行只保留 Docker Compose 这一条路径。Windows、WSL、Linux 和 ECS 使用同一套容器；宿主机不安装项目 Python、Node、uv 或 CUDA。

启动器会先检查 Docker 服务是否可访问、是否为 Linux 引擎以及 Compose 插件是否可用，再读取部署配置并启动服务。Docker 命令已安装不代表引擎正在运行；Windows 请先打开 Docker Desktop，等待 Linux 引擎就绪。

如果提示访问被拒绝，先在 Docker Desktop 所属用户的普通 PowerShell 中运行 `docker info --format '{{.OSType}}'`。普通终端成功、编程助手失败时，应为助手的该次 Docker 操作申请执行权限；不要修改管道权限或开放 Docker TCP 端口。Linux 同样需要当前账户有权访问 Docker socket，不要用 `chmod 666` 放开权限。启动器不会自动提权、切换 Docker context 或修改服务配置。

## Windows + Docker Desktop

首次部署：

```powershell
Copy-Item deploy\app\.env.example deploy\app\.env
notepad deploy\app\.env
.\start\windows\start-compose.cmd
```

访问 `http://127.0.0.1:8080/`。

停止但保留数据：

```powershell
.\start\windows\stop-compose.cmd
```

## Linux、WSL、ECS

```bash
cp deploy/app/.env.example deploy/app/.env
# 编辑 deploy/app/.env，填写密码和凭证
chmod +x start/linux/*.sh
./start/linux/start.sh
```

停止但保留数据：

```bash
./start/linux/stop.sh
```

页面默认地址为 `http://127.0.0.1:8080/`。公网访问由 ECS 的 Nginx/FRP 转发到这个端口；OpenSearch 只在 Compose 网络内提供 `9200`，不对公网开放。

## 模型和数据

API 容器只读挂载宿主机的 BGE-M3 缓存（`ZHOUXU_HF_CACHE_DIR`）。法规语料、案例快照、SQLite 和 OpenSearch 运行数据也必须保留；不要使用 `docker compose down -v`，不要删除 `opensearch/vector_db_data/opensearch`。

旧的 Windows 原生启动脚本和本机虚拟环境不再属于交付路径。

完整迁移说明见 [`deploy/app/README.md`](../deploy/app/README.md) 和 [部署合同](../docs/deployment.md)。
