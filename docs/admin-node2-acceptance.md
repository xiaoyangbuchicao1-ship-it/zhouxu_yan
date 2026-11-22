# 管理员 Node 2 验收记录

日期：2026-11-22

## 本节点做了什么

- 增加管理员总览、人员列表、启用/停用账户、角色调整和业务审计接口。
- 管理员接口只返回业务信息：人员、角色、账户状态和业务操作记录。
- 密码哈希、登录 Token、服务器堆栈和模型原始请求不进入管理员页面。
- 普通用户访问管理员接口返回 403。
- 禁止停用自己，也禁止把最后一个可用管理员停掉。
- 为正式库清理 8 条账户孤儿记录，并按数据库级联规则一并清理 3 条回答记录和 2 条资料关联。

## 真实结果

### 数据清理

- 清理前预检：8 条孤儿记录（对话 3、资料 2、会话 3）。
- 清理后：`PRAGMA foreign_key_check` 为空，`integrity_check` 为 `ok`。
- 正常账户数：225，未减少。
- 清理前完整备份：`data/backups/orphan-account-cleanup-20261122-rerun/zhouxu.db`。
- 备份中的完整性检查为 `ok`，保留原始 8 条记录，便于审计或恢复。

### 后端测试

```text
python -m unittest backend.application.test_admin backend.application.accounts.test_admin_roles backend.infrastructure.database.test_admin_role_migration -v
11 项通过

python -m unittest backend.infrastructure.persistence.test_orphan_account_records -v
2 项通过
```

这 2 个迁移测试专门覆盖了本次发现的根因：删除孤儿对话时，回答和资料关联必须一起级联删除，不能留下半套数据。

### 真实 HTTP 测试

在 Docker Linux 容器内通过真实 HTTP 入口完成：

1. 管理员登录得到 200，并能打开总览。
2. 人员列表得到 200，返回中没有密码或 Token 字段。
3. 普通用户访问管理员总览得到 403。
4. 管理员停用普通用户得到 200。
5. 被停用用户再次登录得到 401。
6. 业务审计得到 200，并记录 `account.status_changed`。
7. 正式 API 容器恢复后，管理员登录再次得到 200。

Windows 本机没有用来替代这条验收：它的 asyncio 事件循环在启动 HTTP 测试前会卡住；项目约束要求真实 HTTP 走 Docker Linux，所以本节点的 HTTP 结果以容器内实测为准。

## 本节点的边界

管理员页面只处理业务管理。服务器日志、异常堆栈、密钥、Token 和模型原始请求仍留给后续运维入口，不会混入管理员工作台。

下一节点是前端管理员入口和工作台：登录分流、人员表、业务记录、状态反馈、空态/失败态和桌面/手机浏览器验收。
