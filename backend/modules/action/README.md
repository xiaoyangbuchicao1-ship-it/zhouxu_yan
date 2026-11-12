# 行动模块

负责受控工具调用，返回执行结果、错误和副作用记录。

工具权限、超时、重试和终止语义由 Harness 与工具端口共同约束。

当前只执行组装入口注册的工具。原工具继续实现 `execute(ActionRequest)`；需要可信运行身份的工具可实现 `execute_context(ModuleRequest[ActionRequest])`。Action 只分派，不导入其他模块内部实现。

法规工具由 `infrastructure/tools/rag.py` 桥接至独立 RAG Module 端口。Harness 传入 `tool_call_index`、原始 `user_request`、本轮 `user_facts/user_constraints`，模型生成的参数不能覆盖调用身份。用户提供的内容仍是数据，不获得系统权限。搜索失败返回包含失败状态的真实观察，让模型解释限制；这不等于搜索成功。取消仍向上传播。

可选 `ActionResult.session_summary` 只供 Harness 保存引用投影，不重复发给模型；有投影时完整结果仅在当前 run 保存。没有该字段的旧工具行为保持兼容。当前只验证只读工具，正式写入工具仍需其自身权限、幂等和恢复合同。

## 回执与恢复（2026-11-03）

ActionResult 新增兼容字段 outcome（ok / partial / empty / error）和可选 error。
模块返回 ok 表示成功取得回执；回执中的 outcome 才表达操作结果。缺省字段的旧记录
仍可读取。原始 result 保持各工具自己的结构；非 ok 结果经模型适配器按 outcome、
error、data 显式表达，已有正文不会因失败标记被清空。

参数错误、未注册工具等明确可纠正的问题按配置形成错误回执，不执行错误动作。
只读工具的操作异常可回到模型；未知副作用工具异常、归属冲突、取消与回执不匹配
仍终止。错误文案使用配置中的公开说明，不把适配器异常细节带进上下文。不自动重放工具。

ToolSpec.read_only 和 reuse_identical_results 均默认 false，由可信注册端声明，不由模型
参数决定。当前材料搜索、读取与法规检索显式声明；实时轮询工具应保持不可复用。
相同参数与相同完整结果达到既有阈值后先反馈重复事实；后续相同请求提供未执行回执，
引用已有动作，不伪装为新执行。其他参数或工具仍可使用；持续无进展才收尾。
从已有检查点的真实请求/回执恢复重复记录，不新增存储表。

法规检索重排淘汰候选不等于 partial；桥接返回包确因容量裁减，才标记部分交付。
outcome 不代表法律充分性或任务完成度，仍需阅读原有 sufficiency 等字段。
