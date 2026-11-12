# 配置层

defaults.toml 保存参数、提示词和文案；settings.py 校验并读取配置与凭证；model_capabilities.toml 保存模型原始能力。配置修改后重建 Harness 或重启服务，尚无热切换。

密钥仅通过指定环境变量或本地凭证文件取得，不写入本目录、日志、公开事件或前端。

- 生产固定 DeepSeek Flash，thinking_mode 为 none / low / high；生成预留 8192 / 16384 / 32768，含思考消耗。旧 model_routing 被工厂拒绝。
- 主模型 explicit 协议配合 auto 工具选择，允许原生最终正文。review 与 compaction 关闭思考，独立设置输出额度。
- runtime 的轮数、模块时限、模型时限与执行总时限分别生效；50 轮是上限，单次执行总时限默认 180 秒。
- context_policy 是应用预算策略；原始模型能力与 deployment_limits 分开。唯一计算在 infrastructure/model/budget.py，60/65/70 水位不代表通用质量最优区间。
- materials 约束解析、预览、读取、搜索与可选 OCR/MinerU。共享初始预览 1200 字符，默认读取上界 12000 字符；所有正文和封装仍受完整请求预算约束。
- context_policy.inventory_* 约束当前可见上下文索引的大小；索引从实际选中内容重建，不推断任务完成。
- rag.review_enabled=false 默认关闭法规工具内部模型审查，主模型仍可自主补查；open_rag 的 dynamic 参数可显式覆盖内部审查开关。
- execution.provider 选择 docker / native；ZHOUXU_EXECUTION_PROVIDER 覆盖默认配置，显式 TOML 保持其设置。Native 的 Python 默认使用服务解释器。
- cases.snapshot_dir、rag 本地模型与语料路径在部署时匹配数据包；应用启动不导入或切换索引。

MinerU 使用 parsing/mineru_runtime 的独立依赖锁，默认关闭，准备环境和权重后按[解析说明](../infrastructure/parsing/README.md)配置。

完整职责见[架构](../../docs/architecture.md)，服务器要求见[部署说明](../../docs/deployment.md)。
