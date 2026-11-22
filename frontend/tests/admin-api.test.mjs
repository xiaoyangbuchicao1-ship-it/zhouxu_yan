import assert from "node:assert/strict";
import test from "node:test";

test("首屏任一接口失败就明确失败，不返回虚假的0和空列表", async () => {
  const { loadAdminDashboard, AdminApiError } = await import("../src/lib/admin-api.ts");
  globalThis.fetch = async url => url.endsWith("/accounts")
    ? Response.json({ detail: "unavailable" }, { status: 503 })
    : Response.json(url.endsWith("/overview") ? { overview: { total_accounts: 225 } } : { events: [] });
  await assert.rejects(loadAdminDashboard("t"), error => error instanceof AdminApiError && error.status === 503);
});

test("权限拒绝保留真实状态和清楚原因，PATCH成功不依赖后续刷新", async () => {
  const { createAdminApi, loadAdminDashboard, AdminApiError } = await import("../src/lib/admin-api.ts");
  const updated = { id: "a1", email: "one@example.com", status: "disabled", role: "user" };
  globalThis.fetch = async (_url, options) => options.method === "PATCH"
    ? Response.json(updated)
    : Response.json({ detail: "admin_required" }, { status: 403 });
  const api = createAdminApi("t");
  assert.deepEqual(await api.updateAccount("a1", { status: "disabled" }), updated);
  await assert.rejects(loadAdminDashboard("t"), error => error instanceof AdminApiError && error.status === 403 && error.message.includes("管理员"));
});
