# OpenSearch 数据与索引工具

应用只使用 deploy/app/compose.yaml 启动 OpenSearch。这个目录保存同一集群使用的法规语料、案例快照、索引定义和维护工具，不再提供第二套 native/dev 启动方式。

目录：

    opensearch/
    ├─ corpus/                     法规正文与清单
    ├─ import/                     法规向量与元数据
    ├─ vector_db_data/opensearch/  OpenSearch 持久数据
    └─ opensearch_delivery/
       ├─ index_definitions/       索引结构与版本合同
       ├─ scripts/                 校验、导入、发布和查询
       └─ queries/                 固定查询与验收输入

## 启动

在项目根目录准备 deploy/app/.env，然后运行：

    .\start\windows\start-compose.cmd

Linux、WSL 或 ECS 运行：

    ./start/linux/start.sh

OpenSearch 只在 Compose 网络监听 9200，不对公网发布。法规、案例和论文信息卡使用同一集群中的独立索引/别名。信息卡使用 BM25 + 同版本 BGE-M3、现有 RRF；处理脚本为 `scripts/information_cards_index.py`，派生向量放在 `opensearch/import/information_cards/`。原始卡片不复制进项目，详见[科研资源接入](../../docs/research-resources.md)。

## 索引发布

维护脚本从 `ZHOUXU_RUNTIME_ENV_FILE` 或 `deploy/app/.env` 读取管理员密码。语料变化时，在 opensearch/ 目录执行：

    python -B opensearch_delivery/scripts/maritime_index.py validate --run-dir .ai-tmp/release-validate
    python -B opensearch_delivery/scripts/maritime_index.py import --index maritime_law_chunks_v2 --run-dir .ai-tmp/release-import
    python -B opensearch_delivery/scripts/maritime_index.py verify --index maritime_law_chunks_v2 --run-dir .ai-tmp/release-verify
    python -B opensearch_delivery/scripts/acceptance.py --index maritime_law_chunks_v2 --run-dir .ai-tmp/release-acceptance
    python -B opensearch_delivery/scripts/maritime_index.py activate --index maritime_law_chunks_v2 --run-dir .ai-tmp/release-activate

过程文件只放在 opensearch/.ai-tmp/，发布失败不会自动切换查询别名。

不要删除 vector_db_data/opensearch/，不要使用 docker compose down -v；这会删除检索数据。完整应用部署见 deploy/app/README.md。
