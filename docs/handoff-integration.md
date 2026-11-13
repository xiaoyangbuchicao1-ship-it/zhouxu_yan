# 交接能力融合说明

本项目以 `v1` 对应的当前舟序主干为基础。`交接文件/` 是功能补充快照，不是第二套应用；它提供海事办公区的页面、业务规则和部分后端实现，必须接入当前的 Workspace、Harness、账户和运行时合同。

## 主干与补充能力映射

| 交接能力 | 当前主干接入点 | 融合规则 |
| --- | --- | --- |
| 聊天／工作切换 | `frontend/src/pages/Workspace.tsx`、`use-workspace.ts`、`ConversationModeSwitch.tsx` | 保留当前会话创建、模式锁定和服务端校验；只吸收交接快照中的视觉和交互细节 |
| 技能工坊 | 当前 Workspace 的 `/workspace/skills` 与 `backend/skills/` | 交接 Skill 进入当前 `SkillRegistry`；不复制旧 Harness |
| 海事检索 | `backend/infrastructure/search/`、当前 ToolExecutor、Workspace API | 真实资料通过当前工具合同返回；来源投影接入当前消息和依据面板 |
| 案件协作 | 当前账户服务、应用服务和 Workspace 路由 | 保留成员、角色、版本冲突和审计语义；不建立第二套账户系统 |
| 工作包 | 当前 Run、Artifact 和 ExecutionProvider | 只迁移缺失的交付投影，运行环境仍由当前 Provider 管理 |
| Docker／Native | `backend/infrastructure/execution/` | 交接 Dockerfile 和 worker 只作补充参考，不在模式切换时创建容器 |

## Skill 与工具边界

`Skill` 是版本化的专业方法资源，包含 `SKILL.md`、方法元数据和可选参考资源。它不直接访问数据库、文件系统、模型或 Runtime。

`read_skill` 是模型可调用的只读工具。它只接受注册的 Skill ID，返回当前版本的方法正文和资源元数据。它属于工具注册表中的 `skill_activation` 类别，但不属于案件、检索或执行等业务副作用工具。

```text
SkillRegistry -> SkillDescriptor / SKILL.md / resources
                         ^
                         |
ToolRegistry  -> read_skill (read-only, idempotent)
              -> search / read / case / execution tools
```

读取 Skill 不改变会话模式，不扩大账户权限，也不代表已经取得事实证据。模型仍需通过受控业务工具取得资料。

## 当前工作流

```text
用户请求
  -> 模型看到 available_skills 和工具清单
  -> 需要专业方法时调用 read_skill(skill_id)
  -> 方法正文进入当前 Harness 上下文
  -> 模型调用检索、材料或案件工具
  -> Harness 保存检查点并按预算恢复或压缩上下文
  -> 当前 Workspace 展示真实来源、状态和交付物
```

## 接入边界

- 前端不把 `skill_context` 或 `case_context` 当作可信事实；服务端按账户和会话重新解析。
- 无语料、来源失败、版本冲突和 Runtime 不可用都保留真实状态。
- 普通聊天、读取 Skill、查看来源和案件列表不要求切换到工作模式。
- 工作模式只在模型真正调用执行工具时创建 Runtime。
- 交接文件中的旧测试仅作为行为合同参考；基础版本只保留当前合同和关键用户路径所需的最小验证。
