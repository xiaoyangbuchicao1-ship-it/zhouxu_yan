# 配置层

defaults.toml 保存参数、提示词和文案；settings.py 校验并读取配置与凭证；model_capabilities.toml 保存模型原始能力。配置修改后重建 Harness 或重启服务，尚无热切换。

密钥仅通过指定环境变量或本地凭证文件取得，不写入本目录、日志、公开事件或前端。

- 生产固定 DeepSeek Flash，thinking_mode 为 none / low / high；生成预留 8192 / 16384 / 32768，含思考消耗。旧 model_routing 被工厂拒绝。
- 主模型 explicit 协议配合 auto 工具选择，允许原生最终正文。review 与 compaction 关闭思考，独立设置输出额度。
- runtime 的轮数、模块时限、模型时限与执行总时限分别生效；50 轮是上限，聊天默认 180 秒，工作任务默认 1800 秒。
- context_policy 是应用预算策略；原始模型能力与 deployment_limits 分开。唯一计算在 infrastructure/model/budget.py，60/65/70 水位不代表通用质量最优区间。
- materials 约束解析、预览、读取、搜索与可选 OCR/MinerU。共享初始预览 1200 字符，默认读取上界 12000 字符；所有正文和封装仍受完整请求预算约束。
- context_policy.inventory_* 约束当前可见上下文索引的大小；索引从实际选中内容重建，不推断任务完成。
- rag.review_enabled=false 默认关闭法规工具内部模型审查，主模型仍可自主补查；open_rag 的 dynamic 参数可显式覆盖内部审查开关。
- execution.provider 本地配置默认 docker；Compose 通过 ZHOUXU_EXECUTION_PROVIDER=native 在 API 容器内执行，复用镜像依赖，无需嵌套 Docker。它不是宿主机原生部署模式。
- cases.snapshot_dir、rag 本地模型与语料路径在部署时匹配数据包；应用启动不导入或切换索引。
- research_cards 配置独立信息卡索引、方法卡目录、论文 Skill 目录与工具说明；目录通过 Compose 只读挂载。启动前 init 校验三个索引；正常信息卡检索复用 BGE-M3 与 RRF，语义通道故障明确标为 partial/BM25。源码路径见[科研资源接入](../../docs/research-resources.md)。

MinerU 使用 parsing/mineru_runtime 的独立依赖锁，默认关闭，准备环境和权重后按[解析说明](../infrastructure/parsing/README.md)配置。

完整职责见[架构](../../docs/architecture.md)，服务器要求见[部署说明](../../docs/deployment.md)。
