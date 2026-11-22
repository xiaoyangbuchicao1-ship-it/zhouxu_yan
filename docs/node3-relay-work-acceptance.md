# Node 3：中转模型工作模式最小闭环

日期：2026-11-21  
入口：`http://127.0.0.1:18080`  
模型：`gpt-6-astra`  
来源：CC Switch `fc665e818e842bf6542e5432ca0fe37d33ffabe1`

这次只验证一条最小路径，避免用长论文请求掩盖协议问题：

1. 在网页模型选择器中选择 `gpt-6-astra`；
2. 发送“请用工具 write 创建 relay-cc-switch-proof.txt，内容只写：CC Switch 工具闭环成功。完成后交付这个文件。”；
3. 中转站通过 Responses 协议返回工具调用；
4. 舟序执行写文件工具并登记产物；
5. 从网页下载文件，刷新页面后再次查看结果。

## 人话验收

| 测试 | 测什么 | 防什么问题 | 实际结果 |
|---|---|---|---|
| 模型能进入工作页 | 在网页选择中转模型并提交工作任务 | 页面显示能选，服务端却换回 DeepSeek | 通过；Run 快照记录 `relay:gpt-6-astra` |
| 工具真的被调用 | 让模型只做一次 `write` | 只能聊天，不能执行工作动作 | 通过；留下 `relay-cc-switch-proof.txt` |
| 文件确实可交付 | 下载服务端登记的文件 | 页面显示完成，但文件不存在或内容不对 | 通过；内容为“CC Switch 工具闭环成功。”，哈希与记录一致 |
| 刷新后不丢结果 | 完成后刷新网页 | 只在实时画面里看得到，历史打不开 | 通过；刷新后仍显示结果 |
| 页面没有前端错误 | 记录浏览器脚本错误 | 用户看到空白、闪退或按钮失效 | 通过；错误数为 0 |

真实运行记录：

- Run ID：`9ccd830b14a74729a3abe3203efadeb6`
- 会话 ID：`510f549523cf036ed4fe48d4b73772d4`
- 产物：`relay-cc-switch-proof.txt`
- 产物大小：31 字节
- 产物 SHA-256：`2115da844c5ab1c00fe4072c63697803dcc4df7c9293fd2e1dd9270e10593820`

另外，直接对中转站做的低成本协议探测也通过：

- Chat 请求能返回工具调用；
- Responses 请求能返回 `function_call`；
- Responses 流式请求能收到 `response.function_call_arguments.delta` 和 `response.completed`。

因此当前只把这个模型标为可进入工作模式。其他中转模型仍需分别探测，不能因为同属一个家族就自动视为可工作。

## 本次改动

- `backend/config/relay_verification.json` 增加了真实探测通过的 `gpt-6-astra` 工具和流式能力；
- `backend/infrastructure/model/test_registry.py` 增加能力合同回归；
- `backend/application/workspace/task_graph.py` 修复工作会话发送普通问候时误显示多步骤的问题；
- `third_party/cc-switch/README.md` 固定复用来源、版本和取舍；
- `tests/browser/node3-relay-work.mjs` 固化真实网页黄金测试。

## 运行命令

```powershell
node tests/browser/node3-relay-work.mjs .ai-tmp/node3-relay-work-20261121-deployed
```

本次只用一条短文本和一次写文件调用验证中转站，论文、法规检索和 PDF 生成仍留给下一步的完整工作流验收。
