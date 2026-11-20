# 舟序启动入口

项目运行只保留 Docker Compose 这一条路径。Windows、WSL、Linux 和 ECS 使用同一套容器；宿主机不安装项目 Python、Node、uv 或 CUDA。

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
