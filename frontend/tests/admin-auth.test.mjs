import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = { setTimeout, clearTimeout };

test("管理员登录走专用接口并保留管理员角色", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify({
      account: { id: "a1", email: "admin@example.com", display_name: "管理员", role: "admin" },
      session: { token: "token" },
    }), { status: 200, headers: { "content-type": "application/json" } });
  };
  const { authenticateAdmin } = await import("../src/lib/auth.ts");
  const session = await authenticateAdmin({ email: "Admin@Example.com", password: "secret" });
  assert.equal(session.account.role, "admin");
  assert.equal(calls[0].url, "/api/auth/admin-login");
  assert.deepEqual(JSON.parse(calls[0].options.body), { email: "admin@example.com", password: "secret" });
});
