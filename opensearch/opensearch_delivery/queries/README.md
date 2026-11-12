# 查询文件

- `bm25.json`：直接 POST 到 `/<read-alias>/_search` 的关键词查询示例。
- `current_version.json`：相同问题加 `is_current=true`。
- `vector.json`：包含“无船承运业务需要备案吗”的真实 1024 维查询向量，可直接 POST；向量内部过滤现行。
- `version_followup.json`：按已知现行目标 document_id/parent_id 取回第七条。通用工具还会分页取齐全部 chunk。
- `hybrid.json`：应用层执行配置，**不能作为 OpenSearch 查询 DSL 直接发送**；由 `scripts/maritime_index.py` 的 hybrid 函数执行两次搜索并融合。
- `demo_questions.json` / `demo_vectors.npy`：固定 6 个问题及对应 BGE-M3 查询向量，可无模型下载重跑验收。row 0–5 的顺序见 JSON。

查询结果中的 BM25 分数、向量分数不能直接相加，脚本保留它们供观察，排序使用两路名次的 RRF 分数。本交付没有神经精排分数；不能把 RRF 分数误标为 BGE reranker 分数。
