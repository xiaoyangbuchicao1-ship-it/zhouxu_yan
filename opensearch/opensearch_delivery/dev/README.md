# 海商法法规库 OpenSearch 开发环境

本目录只负责启动 OpenSearch。法规数据、向量和索引定义位于同级的 `corpus`、`import` 和 `opensearch_delivery` 目录。

## 目录关系

- 启动配置：`opensearch_delivery/dev`
- 本机数据库数据卷：`vector_db_data/opensearch`
- 规范化语料：`corpus`
- 向量输入：`import`
- 过程文件：`.ai-tmp`

所有部署路径都相对于 `opensearch` 根目录，不要求固定盘符。

## 前置条件

1. 安装并启动 Docker Desktop。
2. 在 `opensearch_delivery/dev` 复制 `.env.example` 为 `.env`。
3. 只在部署机器的 `.env` 中设置 `OPENSEARCH_INITIAL_ADMIN_PASSWORD`，不要提交 `.env`。

管理员账号固定为 `admin`。密码由部署者自行设置，不在项目文件、日志或聊天中记录。

## 启动

在 `opensearch_delivery/dev` 目录运行：

```powershell
docker compose --env-file .env up -d
```

本机端口映射为 `19200 -> 9200`、`19600 -> 9600`，容器内部仍使用 OpenSearch 标准端口 `9200`。

## 检查

```powershell
docker compose ps
curl.exe -k -u admin:$env:OPENSEARCH_INITIAL_ADMIN_PASSWORD https://localhost:19200/
curl.exe -k -u admin:$env:OPENSEARCH_INITIAL_ADMIN_PASSWORD https://localhost:19200/_cluster/health
```

## 停止和重启

```powershell
docker compose down
docker compose up -d
```

不要使用 `docker compose down -v`，因为这会删除数据卷。

正式索引创建和全量导入使用 `corpus/manifest/chunks.jsonl`、`import/*.npy` 和对应的向量元数据；所有版本跳转字段已经写入规范化数据，不依赖下游重新推断。

正式索引、导入与查询工具在上一级，参见[部署与索引说明](../README.md)。已有版本字段不代表逐条语义关系都已人工确认。
