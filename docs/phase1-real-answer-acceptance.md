# 第一步：真实完整回答闭环

本节点把用户公开回答的持久化投影贯通到管理员会话详情：

- 用户会话接口读取 conversation_runs.answer 或暂停时的 partial_output；
- 管理员接口返回同一份正文，并返回经过白名单过滤的资料卡和交付文件元数据；
- 管理员页面默认显示完整正文，长回答可以主动收起，再展开；
- 回答使用与工作台相同的 Markdown 渲染规则，引用链接可以点击；
- 管理员接口不会返回系统提示词、隐藏思考、工具原始回执、磁盘路径或凭证。

## 测试驱动证据

先加断言再改实现时，HTTP 合同测试因 sources 字段不存在失败（KeyError: sources）。浏览器验收脚本原先用 page.route 临时拼接长文本，本节点已删除该夹具，改为要求预览库中存在真实持久化的超长回答；当前隔离库尚未写入长回答，因此脚本明确失败并提示“请先通过真实用户入口生成测试回答”，没有把伪造内容当成通过。

实现后：

- python -m unittest backend.interfaces.api.test_admin_conversations：4 项通过；
- 测试同时读取普通用户 GET /workspace/conversations/{id} 与管理员 GET /admin/accounts/{account}/conversations/{id}，长回答和暂停片段逐字一致；
- 前端 npm test：15 项通过；
- npm run typecheck：通过；
- npm run lint：0 错误，保留项目已有的 13 条 Fast Refresh 警告；
- npm run build：通过。

剩余的浏览器长回答脚本需要在隔离预览库通过真实用户入口产生一条超过 1200 字的回答后运行；未产生前保持失败是预期的红测门槛。
