import assert from "node:assert/strict";
import test from "node:test";

test("管理台导航只展示已经接通的页面", async () => {
  const { adminNavigation } = await import("../src/pages/admin/admin-navigation.ts");
  assert.deepEqual(adminNavigation.map(item => item.label), ["总览", "人员", "能力与状态"]);
  assert.deepEqual(adminNavigation.map(item => item.to), ["/admin/overview", "/admin/accounts", "/admin/operations"]);
  assert.equal(new Set(adminNavigation.map(item => item.to)).size, adminNavigation.length);
});

test("人员页能生成可刷新、可分享的会话日志链接", async () => {
  const { accountConversationPath, conversationDetailPath } = await import("../src/pages/admin/admin-navigation.ts");
  assert.equal(accountConversationPath("a/1"), "/admin/accounts/a%2F1/conversations");
  assert.equal(conversationDetailPath("a/1", "c?2"), "/admin/accounts/a%2F1/conversations/c%3F2");
});
