# 接口与扩展合同

## 统一模块调用

`contracts/schemas/core.py` 定义最小公共接口：

```text
ModuleRequest<Input>:
  run_id, call_id, input
  turn_id?, context?

ModuleResponse<Output>:
  status: ok | needs_input | failed | cancelled
  output?, error?

Error:
  code, message, retryable?
```

Harness 生成调用标识，业务载荷由各模块定义。必要输入、结果、归属和权限必须校验；可选观测缺失不阻断业务。模块成功不等于整个用户任务完成；取消不表示外部副作用回滚。

## 稳定对象与端口

| 位置 | 合同 |
| --- | --- |
| `backend/contracts/modules/` | 感知、记忆、决定、工具、材料、法规和案例载荷 |
| `backend/contracts/harness/` | Run 生命周期、私有执行检查点、文件产物 |
| `backend/contracts/events/` | 内部运行事件、公开流式事件与正文折叠 |
| `backend/contracts/model_context.py` | 模型能力、完整请求预算和上下文策略 |
| `backend/contracts/ports.py` | 模块、模型、工具、记忆、检索、材料等可替换端口 |
| `backend/interfaces/api/contracts.py` | HTTP 请求和公开响应 |
| `frontend/src/lib/workbench-types.ts` | 前端工作台类型 |
| `frontend/src/lib/workbench-api.ts` | 前端认证请求与流式接口 |

公开 HTTP 使用 account / conversation / run / material 身份。内部调度键、私有 execution_state、存储路径和供应商隐藏推理不跨产品接口。创建 Run 按账户、Conversation、client_request_id 幂等；重做使用新的请求标识并记录 retry_of_run_id。

材料候选范围与当轮附件身份分开验证。来源引用、历史已读、当前正文可见与任务完成分开处理。公开流以 Run 和 sequence 续接，done 只提交可见正文，终态以生命周期为准。

账户与项目、Conversation 生命周期、材料绑定和永久删除边界见[工作台合同](architecture/workbench-contract.md)。

## 扩展位置

| 变化 | 最小接入方式 |
| --- | --- |
| 新业务工具 | 实现现有工具端口，由 bootstrap 显式注册，声明只读性与副作用 |
| 新模型 | 实现模型与完整请求预算端口，再修改模型工厂和配置；不静默回退 |
| 新任务运行时 | 实现 workspace.py 的 CommandRuntime，继承 WorkspaceExecutionProvider |
| 新材料格式 | 接入 parsing 注册表，保留材料归属、版本、顺序和缺失状态 |
| 新资料源 | 复用 Action、来源与预算合同，显式准备和发布数据 |
| 新业务方法 | 增加 Skill 文档及显式注册项，验收输入放 datasets/skills/ |
| 新存储 | 同时处理业务仓储、RunStore、迁移、持久化与恢复 |

具体实现由 `backend/bootstrap.py` 连接，模块不直接依赖供应商或其他模块内部实现。当前能力与限制见[架构](architecture.md)。
