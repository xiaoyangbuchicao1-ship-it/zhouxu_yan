# 通用可恢复运行合同

Milestone 1 固化舟序的运行对象。它不预设论文、海事或某个 Skill 的业务步骤。

```text
Run
├─ RunGraph        一张版本化节点图
├─ NodeState[]     每个节点的状态、耗时和输入/输出引用
├─ Attempt[]       每次真实尝试，保留失败历史
├─ Checkpoint      当前安全恢复点
└─ Artifact[]      产物元数据，不把大文件塞进事件
```

节点类型由 `backend/contracts/runtime/graph.py` 的 `NodeKind` 统一约束：`skill`、`tool`、`model`、`file`、`human_gate`、`compile`、`subgraph`、`transform`。本阶段执行器已接通 `skill/tool/model/file` 的最小声明式节点；模型协议、真实工具路由和人工确认在后续里程碑接入相同合同，不增加第二套任务引擎。

服务端保存状态和事件，前端只投影它们。节点输出通过引用登记；正文、提示词、密钥和大段工具结果不进入公开生命周期事件。聊天请求与图请求直接使用同一个调度器和标准生命周期合同，不再在运行时维护另一套旧状态映射。

本阶段验收输入与脚本在 `.ai-tmp/milestone1-20261120/`，仅用于本轮验证，不是运行时依赖。
