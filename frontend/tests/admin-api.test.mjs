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

test("模型目录只接收公开配置，不接受任何密钥字段", async () => {
  const { createAdminApi, AdminApiError } = await import("../src/lib/admin-api.ts");
  globalThis.fetch = async () => Response.json({
    providers: [{
      id: "relay", name: "模型服务", preset_type: "custom",
      default_protocol: "openai_responses", auth_style: "bearer",
      base_url: "https://models.example.test/v1", credential_mode: "env_ref",
      credential_ref: "MODEL_API_KEY", credential_configured: true, enabled: true,
      revision: 2, updated_at: "2026-11-22T00:00:00Z", api_key: "leak",
    }],
    models: [],
  });
  await assert.rejects(createAdminApi("t").modelDirectory(), error => error instanceof AdminApiError && error.code === "invalid_response");
});

test("未配置服务商可以保留空接口地址，模型管理页仍能加载", async () => {
  const { createAdminApi } = await import("../src/lib/admin-api.ts");
  globalThis.fetch = async () => Response.json({
    providers: [{
      id: "relay", name: "未配置服务商", preset_type: "custom",
      default_protocol: "openai_responses", auth_style: "bearer",
      base_url: "", credential_mode: "env_ref",
      credential_ref: "MODEL_API_KEY", credential_configured: false, enabled: false,
      revision: 1, updated_at: "2026-11-22T00:00:00Z",
    }],
    models: [],
  });
  const result = await createAdminApi("t").modelDirectory();
  assert.equal(result.providers[0].base_url, "");
});
