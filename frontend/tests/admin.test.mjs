import assert from "node:assert/strict";
import test from "node:test";
import { accountRoleLabel, accountStatusLabel, auditActionLabel, canEnterAdmin, selectAccountPage, auditChangeText, accountChangeText, applyUpdatedAccount } from "../src/lib/admin-ui.ts";

const session = (role) => ({ account: { id: "a1", email: "a@example.com", display_name: "甲", role }, session: { token: "t" } });

test("只有管理员会进入管理员工作台", () => {
  assert.equal(canEnterAdmin(session("admin")), true);
  assert.equal(canEnterAdmin(session("user")), false);
  assert.equal(canEnterAdmin(null), false);
});

test("管理员列表使用简短的业务文案", () => {
  assert.equal(accountRoleLabel("admin"), "管理员");
  assert.equal(accountRoleLabel("user"), "普通用户");
  assert.equal(accountStatusLabel("active"), "正常");
  assert.equal(accountStatusLabel("disabled"), "已停用");
  assert.equal(auditActionLabel("account.status_changed"), "调整账户状态");
});

test("225个账户按20条分页，按邮箱称呼、状态和角色能找到目标", () => {
  const accounts = Array.from({ length: 225 }, (_, index) => ({
    id: `a${index}`, email: `person${index}@example.com`, display_name: index === 224 ? "张船长" : `成员${index}`,
    status: index === 224 ? "disabled" : "active", role: index === 224 ? "admin" : "user",
  }));
  const all = { query: "", status: "all", role: "all" };
  assert.equal(selectAccountPage(accounts, all, 1).items.length, 20);
  assert.equal(selectAccountPage(accounts, all, 12).items.length, 5);
  assert.equal(selectAccountPage(accounts, all, 12).totalPages, 12);
  assert.equal(selectAccountPage(accounts, { ...all, query: " PERSON224@EXAMPLE.COM " }, 1).items[0].id, "a224");
  assert.equal(selectAccountPage(accounts, { query: "张船长", status: "disabled", role: "admin" }, 1).total, 1);
  assert.equal(selectAccountPage(accounts, { query: "张船长", status: "active", role: "admin" }, 1).total, 0);
  assert.equal(selectAccountPage(accounts, { ...all, query: "张船长" }, 12).page, 1);
});

test("业务记录明确展示角色和状态从什么改到什么", () => {
  assert.equal(auditChangeText({ action: "account.status_changed", details: { from: "active", to: "disabled" } }), "正常 → 已停用");
  assert.equal(auditChangeText({ action: "account.role_changed", details: { from: "user", to: "admin" } }), "普通用户 → 管理员");
  assert.equal(accountChangeText({ role: "admin" }), "角色改为管理员");
  assert.equal(accountChangeText({ status: "disabled" }), "停用账户");
});

test("修改成功立刻保存服务端回执和人数，不靠刷新才能看到真实结果", () => {
  const previous = { id: "a1", email: "one@example.com", status: "active", role: "user" };
  const updated = { ...previous, status: "disabled", role: "admin" };
  const data = { accounts: [previous], audit: [], overview: { total_accounts: 1, active_accounts: 1, disabled_accounts: 0, admin_accounts: 0, audit_events: 0 } };
  const result = applyUpdatedAccount(data, updated);
  assert.deepEqual(result.accounts, [updated]);
  assert.equal(result.overview.total_accounts, 1);
  assert.equal(result.overview.active_accounts, 0);
  assert.equal(result.overview.disabled_accounts, 1);
  assert.equal(result.overview.admin_accounts, 1);
  assert.equal(data.accounts[0].status, "active");
});
