# 舟序

海事研究与文档工作台。支持账户、连续会话、材料阅读、法规与案例检索、可恢复任务和文件交付。

## 技术栈与结构

采用前后端分离的模块化单体，由 `backend/bootstrap.py` 统一组装具体实现。

| 部分 | 当前技术 |
| --- | --- |
| 前端 | React 18、TypeScript、Vite、Tailwind CSS、Radix UI、Framer Motion |
| 后端 | Python **3.11**、FastAPI、Pydantic v2、httpx、SQLAlchemy |
| 智能体 | 自研 Harness、DeepSeek Flash，none / low / high 三档 |
| 检索 | OpenSearch 2.19、BGE-M3、BM25 与向量混合检索 |
| 存储 | SQLite 业务库与 RunStore；本地持久文件目录 |
| 任务执行 | 共享文件工具，Docker / Linux Native 两种运行环境 |

```text
backend/
  application/       账户、会话、材料与资源归属
  harness/           主循环、排队、检查点与恢复
  modules/           感知、记忆、推理、行动、反思、法规检索
  infrastructure/    模型、存储、解析、检索与执行适配
  contracts/         端口、数据与事件合同
  interfaces/        HTTP 产品接口与内部控制面
  config/            参数、能力表和提示词
  skills/            已注册的专业工作方法
frontend/            页面、组件与工作台接口
datasets/            题目、原始材料、参考答案与评估定义
deploy/              任务运行时镜像源码
opensearch/
  opensearch_delivery/  索引定义、导入工具与部署配置
docs/                当前架构、接口与部署说明
```

`data/`、语料、向量、模型权重、运行目录和密钥留在本机或持久磁盘，独立于 Git 源码准备。测试执行代码、评分脚本和历史测试报告已从源码基线移除，测试集保存在 `datasets/`。

## 本地开发

推荐 Ubuntu 24.04 LTS；Windows 可以使用 WSL2，也支持直接开发。Windows 与 Linux 的 `.venv`、`node_modules` 需要分别安装。Ubuntu 24.04 默认 Python 版本不满足本项目约束，需要单独准备 Python 3.11。

需要 uv、Python 3.11、Node.js 20.19+ 或 22.12+、npm。在项目根目录运行：

```sh
uv sync --project backend --extra rag --extra materials --locked
npm --prefix frontend ci
npm --prefix frontend run dev:all
```

前端 `http://127.0.0.1:4173`，后端 `http://127.0.0.1:8010`。启动脚本等待后端健康检查，退出时回收本次启动的进程。

完整功能还需准备：

1. DeepSeek 凭证：按 `backend/config/defaults.toml` 的配置提供环境变量或本地凭证文件。
2. 匹配的 BGE-M3 模型、法规语料、案例快照与 OpenSearch 索引，见[部署说明](docs/deployment.md)。
3. 任务运行时：默认 Docker，构建方式如下；Linux 专用部署可设置 `ZHOUXU_EXECUTION_PROVIDER=native` 并安装文档工具。

```sh
docker build -t zhouxu-task-runtime:0.2 deploy/task-runtime
```

## 检查与构建

```sh
uv lock --project backend --check --offline
uv run --project backend --locked ruff check --no-cache backend
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

`datasets/` 保存离线验收输入，不被生产代码加载；参考答案不得作为模型输入。新基线不附带测试执行器或历史成绩。

## 持久化与服务边界

| 内容 | 默认位置 |
| --- | --- |
| 账户、会话、材料元数据与授权历史 | `data/relational/zhouxu.db` |
| 私有任务检查点、公开事件和可见正文 | `backend/.runtime/runs.sqlite3` |
| 原始材料与解析结果 | `data/files/` |
| 会话生成文件 | `data/files/generated/run-workspaces/<账户>/<会话>/` |

当前 RunStore 用文件锁限定一个服务进程；部署先使用单个 Uvicorn worker。Native 命令以服务账号运行，面向互不信任用户的任意代码执行还需要独立沙箱。云端上线需要 HTTPS、流式反向代理、持久盘和数据备份，详见[部署说明](docs/deployment.md)。

## 维护入口

- [后端](backend/README.md)与[前端](frontend/README.md)：启动和维护。
- [架构](docs/architecture.md)：当前链路、数据、上下文和恢复。
- [接口合同](docs/module-contracts.md)：公共对象、模块与扩展位置。
- [测试集](datasets/README.md)：原题、参考答案、历史评估定义与使用边界。

新增能力优先复用现有端口、注册表和配置；出现真实的隔离或独立扩容需要后再拆服务。
