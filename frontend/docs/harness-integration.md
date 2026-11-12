# Zhouxu 工作台接口与状态

前端唯一的研究工作台是 `/workspace`，保留 Zhouxu Workspace 的页面结构。Harness 只负责后端 Run 调度和事件流，不作为用户页面，也不把内部模块名称展示给用户。

## 认证与运行

认证成功后，前端保存当前标签页会话，并以 bearer token 调用：

| 作用 | 接口 |
| --- | --- |
| 注册、登录、当前账户 | `/auth/register`、`/auth/login`、`/auth/me` |
| 提交 Run | `POST /workspace/conversations/:conversationId/runs` |
| 补充、调整、停止、打断 | `/workspace/runs/:runId/resume`、`/steer`、`/stop`、`/interrupt` |
| 停止后继续 | `POST /workspace/runs/:runId/continue`（同一Run） |
| 重新做 | 提交新Run，并携带 `retry_of_run_id` |
| 快照与事件流 | `GET /workspace/conversations/:conversationId/snapshot`、`/events?run_id=...` |

事件流使用 NDJSON。每条事件都带 `run_id` 和 `sequence`；`progress`、`delta` 和 `done` 分别驱动运行阶段、增量答案和可见文本结束。`done` 不代表 Run 已完成，终态以 `lifecycle` 或快照为准。前端按 `sequence` 去重，断线后从最后序号恢复，不重复提交原问题。

## 附件合同

附件先通过 multipart 上传。对话附件绑定到 Conversation，可以被后续 Run 继续使用：

```text
POST /workspace/conversations/:conversationId/materials
DELETE /workspace/conversations/:conversationId/materials/:materialId
```

上传响应只包含不透明 `material_id`、文件名、媒体类型、大小、状态和校验和，不暴露服务器路径。提交 Run 时可以显式缩小材料范围；省略 `material_ids` 时，后端自动使用该 Conversation 已绑定的材料作为候选范围：

```json
{
  "conversation_id": "conversation-id",
  "client_request_id": "client-generated-id",
  "text": "请根据附件回答",
  "facts": {},
  "constraints": [],
  "attached_material_ids": ["opaque-material-id"]
}
```

后端执行器再将材料 ID 解析为内部 `MaterialInput`，交给解析器、感知层和按需 `read_material` 工具。

`attached_material_ids` 是当前消息附上的文件，不等于候选范围。只有用户明确缩小范围才传 `material_ids`。历史用户消息的 `materials` 用于展示已发送卡片；提交成功后清空当轮卡片，Conversation仍绑定材料。`updates` 保存同一Run中的较新补充要求。

## 状态与边界

- 上传中、上传失败、可发送和已移除状态均在输入区可见。
- Run 显示 queued、running、waiting_user、completed、failed、stopped、interrupted。
- 生成期间输入框及上传保持可用；默认发送排队，可在发送菜单选择调整当前任务。等待补充时发送恢复该Run。
- 阶段成果先保存，系统自动继续；停止保留正文，继续从检查点做剩余工作，重新做另建Run。刷新恢复历史和活动快照，服务重启自动恢复活动任务。
- 多Run各有事件订阅和游标；标准正文不等待逐字动效。RunProgress显示真实阶段，处理过程折叠；普通模式使用清晰的旋转弧，减少动画设置关闭旋转和文字渐变，保留三点缓慢明暗变化的必要反馈。停止或完成的最后文字呈现完后再替换为持久历史。
- 小材料的预览足够时直接进入感知上下文；预览不足时才调用 `read_material`。
- 浏览器不接触服务器原件路径，不把材料正文写入持久化浏览器存储。

实现与限制见[当前架构](../../docs/architecture.md)；专业内容仍需依据与验收。
