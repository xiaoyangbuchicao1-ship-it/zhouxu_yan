# 舟序后端

Python 3.11、FastAPI、Pydantic v2、httpx、SQLAlchemy 与自研 Harness。提供账户、Workspace、材料、可恢复任务、法规/案例检索和文档工作。

## 启动

从项目根目录运行：

```sh
uv sync --project backend --extra rag --extra materials --locked
uv run --project backend --extra rag --extra materials --locked uvicorn backend.interfaces.api.app:create_app --factory --host 127.0.0.1 --port 8010 --workers 1
```

统一前后端启动见[项目说明](../README.md)。CLI 为 `python -m backend "问题"`，会调用真实模型；可用 `--config <完整 TOML 路径>` 指定配置。默认参数、提示词和文案在 `config/defaults.toml`，修改后重建 Harness 或重启服务。

模型固定 DeepSeek Flash，none / low / high 三档；review 与 compaction 关闭思考。凭证由配置读取器在运行时获取，不进入 TOML、Trace、公开事件或前端。

`/healthz` 是健康检查，`/auth/*` 是账户接口，认证后的 `/workspace/*` 是产品接口；`/harness/*` 保留内部控制边界。

## 目录与扩展

| 路径 | 职责 |
| --- | --- |
| `bootstrap.py` | 具体实现的组装与资源生命周期 |
| `contracts/` | 请求、响应、事件与端口 |
| `application/` | 账户、项目、会话、材料归属与历史 |
| `harness/` | 主循环、预算、排队、补充、停止与恢复 |
| `modules/` | 感知、记忆、推理、行动、结构反思与法规检索 |
| `infrastructure/` | 模型、数据库、检索、材料解析和执行适配 |
| `skills/` | 显式注册的只读工作方法 |
| `../datasets/` | 原题、原始材料、参考答案和验收定义 |

新增能力复用[公共端口](contracts/ports.py)，在 bootstrap 显式连接。协作、自进化和长期经验尚未实现。

## 共享执行环境

`infrastructure/execution/workspace.py` 统一 read / write / edit / bash、账户/会话工作区、版本冲突、产物、预览和 Run 回收。正文与编辑补丁经标准输入传输。

`CommandRuntime` 要求 exec / python / bash / close；Docker 与 Native 只实现各自进程边界。新增运行时继承 `WorkspaceExecutionProvider` 并实现 `_create_runtime`，再接入配置及组装入口。

默认 Docker 镜像为 `zhouxu-task-runtime:0.2`，源码在 `deploy/task-runtime/`。Linux 无 Docker 可设置 `ZHOUXU_EXECUTION_PROVIDER=native`；显式 TOML 需直接选择 provider。会话文件持续保存，Run 收尾释放进程。文档库、LibreOffice、Poppler、中文字体和 Bash 要按[部署说明](../docs/deployment.md)准备。

## 数据、上下文与恢复

业务 SQLite 保存账户、授权历史和材料信息；SQLiteRunStore 保存私有检查点、公开事件及正文，并限定单服务进程。启动恢复 queued/running，停止、打断和等待补充不自动恢复；未知副作用不自动重放。

唯一完整请求预算在 `infrastructure/model/budget.py`。材料先给有界预览，再按需 search_material / read_material；解析、当前正文可见和任务完成分别表达。供应商隐藏推理只留在按 Run 隔离的私有协议续接与检查点。

法规与案例经 Action 工具进入主循环，共享常驻 BGE-M3 和认证检索客户端。`rag.review_enabled=false` 为默认，主模型按证据缺口决定补查。应用启动不导入或切换索引。

维护资料：[当前架构](../docs/architecture.md)、[接口合同](../docs/module-contracts.md)、[解析](infrastructure/parsing/README.md)、[法规检索](modules/dynamic_rag/README.md)、[案例库](infrastructure/corpus/README.md)。

## 检查

```sh
uv lock --project backend --check --offline
uv run --project backend --locked ruff check --no-cache backend
```

基础版本按用户要求移除旧测试执行器与结果；验收输入保存在[测试集](../datasets/README.md)。检查与真实业务验收分开。继续维护前读取 [AGENTS.md](AGENTS.md)。
