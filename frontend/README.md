# 舟序前端

舟序前端使用 React、Vite、TypeScript、Tailwind CSS、Radix UI 和 Framer Motion，包含落地页、登录注册和 Zhouxu 研究工作台。

## 运行

前端由 `deploy/app` 的 web 容器构建并由 Nginx 提供服务。所有平台统一使用项目根目录的 Compose 入口，不在宿主机安装 Node 或保留 `node_modules`：

```powershell
.\start\windows\start-compose.cmd
```

Linux、WSL 或 ECS 使用 `./start/linux/start.sh`。页面地址默认为 `http://127.0.0.1:8080/`。

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

- [本地字体及许可说明](public/fonts/FONT-NOTICE.md)

不提交 `.env`、运行数据、用户材料、密钥、依赖目录、构建产物或 `.ai-tmp/` 过程文件。
