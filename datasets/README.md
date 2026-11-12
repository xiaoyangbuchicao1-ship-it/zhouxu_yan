# 测试集与参考答案

这里保存可复核的验收输入，与生产源码、执行程序和历史结果分开。题目、原材料、期望答案及评估定义保持原内容；旧执行器、评分脚本和生成报告不随基础版本保留。

| 目录 | 内容 |
| --- | --- |
| `scenarios/core_v1.json` | 32 个固定场景、37 个用户轮次，含合成材料、期望和 rubric |
| `scenarios/professional_v1.json` | 原始海事 Word 的独立专业场景及前十题给定参考答案 |
| `scenarios/context_delivery_v1.json` | 引用核心场景的上下文、范围与压力验收定义 |
| `scenarios/fixtures/maritime_questions.docx` | 用户题库原件的逐字节副本 |
| `scenarios/fixtures/maritime_answers.json` | 从原用户答案抽取的完整 97 项参考答案 |
| `benchmarks/*_cases.json` | 法规、查询计划、材料、模型和决定协议题集 |
| `benchmarks/*.toml` | 原评估候选定义与性能阈值，不用于生产配置 |
| `skills/maritime-legal-research.json` | 海事法规 Skill 的预期行为及困难状态样本 |

97 项答案保留 ordinal、section、number、answer。原答案先为第一组 1—87，再为第二组重新编号 1—10；不能只按 number 合并。前十题答案与 professional_v1 的 judgment_key 对应。题目和答案包含历史法律语境，不冒充当前法律权威。

使用规则：

- 生产代码不导入本目录；参考答案、expected、rubric 与风险标签不进入模型输入。
- 不为当前模型通过而改题、改 gold 或放宽期望。
- 区分选项匹配、解释质量、材料链路和专业结论；固定样本的通过率不代表线上准确率。
- 模型候选和历史协议定义只供理解原验收意图，不恢复为生产路由配置。
- `context_delivery_v1` 内的相对题集引用、professional_v1 的相对材料引用均以同目录为根。

法规部署的固定查询及向量保留在 `opensearch/opensearch_delivery/queries/`，供索引发布验收使用。业务语料、案例快照和模型权重独立于测试集管理，不随 Git 发布。
