# OpenSearch 部署与法规索引

本目录是可版本化的部署源码：OpenSearch 2.19 启动配置、索引定义、导入/查询工具与发布校验。法规正文、向量、数据库和密钥独立管理，均不随 Git 发布。

```text
opensearch/
  corpus/                单独准备的正文、原件、结构化记录与清单
  import/                单独准备的向量及逐行元数据
  vector_db_data/        OpenSearch 持久数据
  opensearch_delivery/
    dev/                 本地 Docker Compose 与凭证模板
    native/              Linux systemd 安装与配置
    index_definitions/   字段、索引参数与版本关系合同
    scripts/             导入、校验、发布、查询和查询向量化
    queries/             查询示例、固定部署问题与配套向量
```

脚本从自身文件位置解析 opensearch 根目录；不要只移动 scripts。导入依赖 requirements.txt，在线查询编码的可选依赖在 requirements-embedding.txt。完整应用使用后端的 rag extra。

## 本地与原生服务

以下命令在 `opensearch/` 执行：

```sh
cp opensearch_delivery/dev/.env.example opensearch_delivery/dev/.env
# 在本机凭证文件中填写 OPENSEARCH_INITIAL_ADMIN_PASSWORD
docker compose --env-file opensearch_delivery/dev/.env -f opensearch_delivery/dev/compose.yaml up -d
python -m pip install -r opensearch_delivery/requirements.txt
```

服务使用 HTTPS 回环端口 19200；不要提交或打包 .env。无 Docker 云机使用 [native/README.md](native/README.md)，数据和别名合同相同。生产证书与最小权限身份按实际部署配置。

## 显式发布法规索引

先提供匹配的 corpus / import；当前包含 93,858 个块，以清单校验为准。以下命令仍在 `opensearch/` 执行，过程输出必须位于该目录的 .ai-tmp 内：

```sh
python -B opensearch_delivery/scripts/maritime_index.py validate --run-dir .ai-tmp/release-validate
python -B opensearch_delivery/scripts/maritime_index.py import --index maritime_law_chunks_v2 --run-dir .ai-tmp/release-import
python -B opensearch_delivery/scripts/maritime_index.py verify --index maritime_law_chunks_v2 --run-dir .ai-tmp/release-verify
python -B opensearch_delivery/scripts/acceptance.py --index maritime_law_chunks_v2 --run-dir .ai-tmp/release-acceptance
python -B opensearch_delivery/scripts/maritime_index.py activate --index maritime_law_chunks_v2 --run-dir .ai-tmp/release-activate
```

validate 校验清单、身份、路径与向量行；import 检查每条 Bulk 结果；verify 对照正文、元数据与向量；acceptance 对照固定查询及精确余弦近邻；activate 校验后绑定 maritime_law_chunks_read / maritime_law_chunks_write。

这些是建库与发布操作所需的运维校验，随源码保留。旧测试执行器及历史结果已从基础版本移除。应用启动只读使用现有索引，不执行这条发布链。

不覆盖已发布索引。语料、模型或分词变化时建立新版本；失败或未完成的索引不挂查询别名。运行 status 检查当前状态。别名切换和已有数据处理须由单个维护进程明确执行。

## 查询与来源

```sh
python -B opensearch_delivery/scripts/maritime_index.py search --index maritime_law_chunks_read --text "无船承运业务需要备案吗" --current
```

固定 demo_vectors.npy 的每一行只对应 demo_questions.json 中同序号问题。新问题需用相同 BGE-M3 revision 编码，不能换正文后继续用旧向量。

BM25 与 HNSW 两路同过滤，应用层 RRF 融合；分数不是法律正确概率。is_current、unresolved、amends 沿用语料，历史目标存在不等于法律内容等价。字段语义见 [index_contract.md](index_definitions/maritime_law_chunks_v2/index_contract.md)。

正式运行文件与语料放在持久盘；不要热复制 OpenSearch 数据目录。新机器可由输入重建，大库迁移使用 snapshot/restore。案例发布使用[应用案例导入器](../../backend/infrastructure/corpus/README.md)，不复用法规别名。
