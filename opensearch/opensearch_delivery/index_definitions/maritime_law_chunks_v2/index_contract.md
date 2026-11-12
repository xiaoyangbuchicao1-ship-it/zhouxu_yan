# maritime_law_chunks 索引合同（结构版本 v2）

本结构定义用于当前 `maritime_law_chunks_v2` 数据库索引。

## 输入、身份与来源

- `corpus/manifest/chunks.jsonl`：逐行读取，正文、分类、版本、相对路径统一来源。
- `corpus/records/articles.jsonl` / `sources.jsonl`：分块前的结构化来源记录；路径已相对 opensearch 根目录。
- `corpus/chunks/article_chunks.jsonl` / `source_chunks.jsonl`：保留分块偏移及切分原因的详细副本，共同字段与统一入库文件一致。它们含有索引 schema 之外的字段，不能将整行直接写入严格映射索引；正式导入继续读取 `corpus/manifest/chunks.jsonl`。
- `import/article_embeddings.npy` / `source_embeddings.npy`：按 kind 选择，再按 embedding_row_index 读取 1024 维向量。
- 两份 `*_embeddings_meta.jsonl`：验证 row_index、chunk_id、embed_text SHA-256 和版本字段一致，防止“正确文本配了另一条向量”。
- `_id = chunk_id`，同时在 `_source` 中保存 chunk_id。document_id 标识一个法规文献版本；article 的 parent_id 标识一条父条文，source 的 parent_id 通常标识整份来源文献。
- 所有文件路径相对于 opensearch 根。PDF、Word、HTML 统一放在 `corpus/originals/{document_id}/`；`pdf_relative_paths` 仍只列 PDF，Word/HTML 从 `corpus/records/sources.jsonl` 的 `original.relative_path` 获取。没有 PDF 的记录维持空数组。

## 字段分工

|用途|字段/行为|
|---|---|
|身份|chunk_id、kind、document_id、parent_id：keyword|
|BM25|chunk_text^5、document_name^3、section_path^2、article_no^4；显式条号再对 article_no.keyword 加精确匹配 boost|
|向量|vector：knn_vector，1024 维，Lucene HNSW，cosinesimil，m=16、ef_construction=128|
|复现|embed_text 仅保存、不建倒排；embedding_file、embedding_row_index 保存映射关系|
|版本|law_family_id、document_role、version_relation、两个目标 ID 为 keyword；is_current 为 boolean，可 null；version_date 为 yyyy-MM-dd，可 null|
|过滤|document_type、legal_status、language、chunk_type，以及 classification 内五个分类字段都是 keyword|
|追溯|canonical_text_path、pdf_relative_paths 保存当前文件的相对路径|
|顺序|chunk_index、chunk_count 为 integer；chunk_index 从 0 开始|

逐条保存现有全部字段，唯一输入转换是将空字符串 version_date 投影为 null；不会改写 JSONL。中文和英文使用 `standard + cjk_width + lowercase + cjk_bigram`；中文主要生成相邻双字词，英文忽略大小写。BM25 使用 OpenSearch 默认相似度参数（k1=1.2、b=0.75）。

`dynamic=strict` 拒绝意外新字段。1 主分片、0 副本适配本机开发；刷新常态 1s，批量导入期间暂关、结束恢复。BM25 与 HNSW 属于同一条 OpenSearch 文档的两种检索结构，不是两个独立数据库。

## 版本语义

只使用已固化的三个关联字段，不维护第二套推断关系：

- current：当前数据把此文献标为现行，目标指自己。
- superseded_by：指向已有现行候选文献；有目标 parent 时可定位候选条文组。
- amends：修改决定与基础文献的关系，不能直接把决定替换成基础法规某一条。
- unresolved：没有足够关系，两个目标字段保持 null。

is_current 按已有数据使用，不等同于系统在查询当天联网核验法律效力。同条号匹配不能证明修订前后法律含义或条文编号一直不变；历史案件应保留时间语境。数据中的 null、关系未解决项不会在导入时偷偷补成 true。

本轮实际发现 2023 年国际海运条例第三十一条（听证）与现行第三十一条内容不同，现行听证条文为第三十二条。已在 follow 查询层加入保守字符比较：去空白/首条号后相似度 < 0.8 时返回 candidate_requires_review，targets 为空，原字段指向的条文只列为 configured_candidates。这个提示也可能因分块长度差异触发，不负责重建数据关系。相似度较高同样不等同于法律意义等价。

## 查询与生命周期

默认保留全部版本及 article/source；调用方选择是否过滤现行。BM25 与向量检索各返回 50 条；HNSW 内部 k=200，以提高全量库的近邻召回，响应 size=50。RRF 分数为两路 `1/(60+rank)` 之和，按分数降序返回 10 条，同分按 chunk_id 排序。没有自动按法规族折叠，否则同一法规的不同有用条文会丢失。没有默认把历史结果替换成现行结果；通过 follow 明确获取候选并保留原命中。

Bulk 逐条检查，重复 ID 更新同一条。正式导入前预检全部本地数据；失败可重新运行，数据层是可重建来源。完全导入、读回校验后再发布 read/write aliases；在线索引拒绝全量覆盖，已有指向其他索引的别名拒绝自动迁移。更换模型、向量维度、分词等应建立后续版本索引。

本交付工具是本机维护/检索组件，没有实现多租户授权、在线增量版本治理、跨编码器精排、法律有效性自动核验或问答 API。下游应用应以 read alias 查询并负责上述业务边界。
