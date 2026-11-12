# 舟序前端

舟序前端使用 React、Vite、TypeScript、Tailwind CSS、Radix UI 和 Framer Motion，包含落地页、登录注册和 Zhouxu 研究工作台。

## 本地运行

需要 Node.js 20.19+ 或 22.12+、npm，以及当前系统安装的 `backend/.venv`。Windows 与 Ubuntu/WSL 使用同一启动命令；脚本自动选择 `Scripts/python.exe` 或 `bin/python`。项目根目录的安装与统一启动见 [README](../README.md)。

```powershell
npm ci
Copy-Item .env.example .env
npm run dev:all
```

`dev:all` 会先启动后端并等待 `http://127.0.0.1:8010/healthz` 返回成功，再启动前端。打开 `http://127.0.0.1:4173`。退出前端进程时会一并停止本次启动的后端进程。可用 `ZHOUXU_API_PORT`、`ZHOUXU_WEB_PORT` 和 `ZHOUXU_API_TARGET` 覆盖本地端口；不要在任何 `VITE_` 配置中存放密钥。

如果只需要单独启动前端，仍可运行 `npm run dev -- --port 4173`，但这不会启动后端。

## 验证与构建

```powershell
npm run typecheck
npm run build
npm run preview -- --port 4173
```

生产构建输出为 `dist/`。正式托管需要支持 SPA 路由回退，将 `/api` 转发到后端，并保留流式响应；Vite 的开发和预览代理不能替代生产反向代理。

## 工作台边界

`/workspace` 是唯一的用户工作台入口，使用旧版 Zhouxu Workspace 的视觉和交互，并通过 `src/lib/workbench-api.ts` 连接新版 FastAPI 合同。

后端 Harness 只作为运行控制平面存在，不作为用户页面。前端使用 `conversation_id` 关联 Conversation、材料、Run 和 NDJSON 事件流，覆盖提交、处理中、等待补充、恢复、停止和完成状态。

## 维护资料

- [设计规范](DESIGN.md)
- [产品边界](PRODUCT.md)
- [登录注册对接](docs/auth-integration.md)
- [Harness 接口、状态与验证](docs/harness-integration.md)
- [本地字体及许可说明](public/fonts/FONT-NOTICE.md)

不提交 `.env`、运行数据、用户材料、密钥、依赖目录、构建产物或 `.ai-tmp/` 过程文件。旧测试执行代码与依赖已移除；固定验收输入见 [datasets](../datasets/README.md)。
