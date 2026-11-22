# 第一步真实验收

管理员会话日志：`node tests/browser/admin-conversation-log.mjs`。登录、账户选择、会话列表、公开回答和运行详情均走真实 HTTP；桌面和手机会检查刷新、横向溢出、敏感字段和页面错误。长回答展开/收起：`node tests/browser/admin-long-answer-layout.mjs`。登录错误位置：`node tests/browser/admin-login-error-layout.mjs`。三个脚本都通过本次进程环境 `ZHOUXU_TEST_ADMIN_EMAIL`、`ZHOUXU_TEST_ADMIN_PASSWORD` 提供已获授权的管理员凭证；不保存密码或 Token。

技能 ZIP 页面验收：`node tests/browser/skill-packages.mjs`。使用 `tests/fixtures/skills/expense-summary` 现成包，验证内置/我的分组、损坏与超大包提示、1.2 MB 包通过网站上传、读取参考资料和模板、刷新保留、工作入口预选、历史聊天模式不变及移除。该脚本不调用模型；实际执行技能与下载成果由技能运行验收另行检查。可用 `ZHOUXU_CHROME_PATH` 指向本机 Chrome。

模型清单与模式分离回归：`node tests/browser/model-mode-parity.mjs`。检查聊天/工作清单一致、选择刷新保留、未验证型号能绑定真实工具图、聊天权限与不可用型号仍受限制，最后只向官方 DeepSeek 发送一次“你好”。工具图不调用模型；脚本不做逐型号能力探测。

应用在 Docker Linux Compose 中运行；浏览器驱动可在 Windows 执行。先确认实际端口与 `/api/healthz`，本机为 `http://127.0.0.1:18080`。使用部署已配置的真实 DeepSeek，会产生模型调用费用；脚本不读取中转凭证、不保存或打印账号密码和令牌。

从项目根目录执行：

```powershell
npm ci --prefix tests/browser
npx --prefix tests/browser playwright install chromium
node tests/browser/step1.mjs
```

脚本自动注册专用验收账号，经实际登录、页面操作、HTTP 和下载完成两条短旅程：

1. 默认聊天要求创建 TXT；普通文本、空值、旧提交入口不能代替授权；草稿保留；明确拒绝后只回答文本，重复确认返回 409。
2. 真实模型读 Skill、写文件后暂停；刷新，上传附件（人为延迟真实上传以核验按钮锁定），带要求继续同一 Run；核对原目标、附件、耗时和下载哈希，两个章节与附件标记各出现一次。

测试账号和对话留在应用中便于排查，不自动删除服务端用户数据。每次新建独立账号；失败输出 Run ID。测试自己的超时或页面定位错误不能算产品故障，也不能写成验收通过。可用 `ZHOUXU_TEST_URL` 指定其他已授权的测试部署。

完整海事论文旅程较慢，另行运行（输出目录属于本次过程文件）：

```powershell
node tests/browser/step1.mjs --paper --output-dir .ai-tmp/step1-paper
docker run --rm -v "${PWD}/tests/browser:/checks:ro" -v "${PWD}/.ai-tmp/step1-paper:/papers:ro" --entrypoint python zhouxu-api:local /checks/verify_paper.py /papers
```

`verify_paper.py` 验证 PDF 可解析、有正文，DOCX ZIP 完整、原生脚注引用匹配、必需主题存在、没有重复长段落；输出实际页数、脚注数与哈希。它不是学术质量或引用准确性审核，后两项须对照真实来源人工检查。

后台根因回归（独立临时数据库，不修改在线运行库）：

```powershell
docker compose -f deploy/app/compose.yaml run --rm --no-deps -v "${PWD}/backend:/app/backend:ro" --entrypoint python zhouxu-api -m unittest backend.harness.lifecycle.test_step1_recovery backend.harness.runtime.test_context_recovery backend.infrastructure.execution.test_write_recovery backend.infrastructure.persistence.test_execution_migration backend.interfaces.api.test_health -q
```

重启与历史迁移验收另见 `docs/step1-acceptance.md`。不要为重跑测试清库、删除数据卷或重放用户历史任务。

长论文实际走过摘要失败后的完整原文引用恢复。对同一份已保存输入，以下命令使用真实 DeepSeek 单独复测正常摘要分支，不修改原 Run，也不冒充新一次浏览器长任务：

```powershell
docker compose -f deploy/app/compose.yaml run --rm --no-deps -v "${PWD}/tests/browser:/checks:ro" --entrypoint python zhouxu-api /checks/replay_compaction.py --conversation 6f7b2e6eef92717015c276bb4eb377c2 --run 3698df6d5e984d71902d44d05a50dae0 --require 迟延 --require PDF
```

回答完成闪动与耗时格式回归：`node tests/browser/completion.mjs`。它会用真实登录和 DeepSeek 连续生成两次回答，确认正文节点不被替换、上翻时不抢滚动、步骤详情不收起、刷新后回答仍在，并检查所有耗时保留一位小数。

第三步 Node 1 的最小任务图与权限验收：

```powershell
node tests/browser/node1.mjs .ai-tmp/step3-node1-20261121
```

它只向真实 DeepSeek 发送一次“你好”，确认服务端生成一个模型节点、刷新后仍保留回答，并通过同一 HTTP 入口提交一张聊天 Skill 图，确认后端返回 `409 work_mode_required`。这个脚本不使用中转站，也不保存平台凭证。
