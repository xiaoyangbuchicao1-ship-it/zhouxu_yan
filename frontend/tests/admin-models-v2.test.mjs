import assert from "node:assert/strict";
import test from "node:test";

test("模型测试、模型列表拉取和设为默认调用真实后台接口", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    if (String(url).endsWith("/admin/model-providers/siliconflow/discover")) {
      return Response.json({
        supported: true,
        models: [{ id: "Qwen/Qwen2.5-72B-Instruct", family: "qwen", label: "Qwen 2.5 72B", already_added: false }],
        notice: null,
      });
    }
    if (String(url).endsWith("/admin/models/siliconflow%3Aqwen/test")) {
      return Response.json({
        ok: true,
        connectivity_status: "verified",
        latency_ms: 142,
        error_code: null,
      });
    }
    return Response.json({
      profile_id: "siliconflow:qwen",
      provider_id: "siliconflow",
      upstream_model_id: "Qwen/Qwen2.5-72B-Instruct",
      label: "Qwen 2.5 72B",
      family: "qwen",
      protocol_override: null,
      protocol: "openai_chat_completions",
      context_tokens: 131072,
      max_output_tokens: 16384,
      thinking_modes: ["none", "low"],
      default_thinking: "none",
      capabilities: {},
      enabled: true,
      access_scope: "all",
      is_default: true,
      sort_order: 100,
      source: "admin",
      checked_on: "2026-11-22",
      connectivity_status: "verified",
      connectivity_latency_ms: 142,
      connectivity_checked_at: "2026-11-22T12:00:00Z",
      connectivity_error: null,
      revision: 2,
      updated_at: "2026-11-22T12:00:00Z",
    });
  };

  const { createAdminApi } = await import("../src/lib/admin-api.ts");
  const api = createAdminApi("token");
  const discovered = await api.discoverProviderModels("siliconflow");
  assert.equal(discovered.supported, true);
  assert.equal(discovered.models[0].family, "qwen");

  const probed = await api.testModel("siliconflow:qwen");
  assert.equal(probed.ok, true);
  assert.equal(probed.latency_ms, 142);

  const updated = await api.updateModel("siliconflow:qwen", { enabled: true, is_default: true });
  assert.equal(updated.is_default, true);
  assert.equal(calls[2].options.method, "PATCH");
});

test("管理端读取来源原文遵循敏感字段拦截规则", async () => {
  const { createAdminApi, AdminApiError } = await import("../src/lib/admin-api.ts");
  globalThis.fetch = async () =>
    Response.json({
      source_id: "law-22",
      kind: "law",
      title: "中华人民共和国海商法",
      locator: "第二十二条",
      text: "下列各项海事请求具有船舶优先权……",
    });
  const doc = await createAdminApi("token").sourceDocument("law-22");
  assert.equal(doc.title, "中华人民共和国海商法");

  globalThis.fetch = async () =>
    Response.json({ source_id: "law-22", title: "测试", text: "正文", disk_path: "/secret" });
  await assert.rejects(
    createAdminApi("token").sourceDocument("law-22"),
    err => err instanceof AdminApiError && err.code === "invalid_response",
  );
});

test("会话列表支持传游标翻页和归档筛选", async () => {
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return Response.json({
      account: { id: "a1", email: "user@example.com", display_name: "研究员" },
      conversations: [],
      next_cursor: "cursor-2",
    });
  };
  const { createAdminApi } = await import("../src/lib/admin-api.ts");
  const page = await createAdminApi("token").accountConversations("a1", {
    archived: "archived",
    cursor: "cursor-1",
    limit: 20,
  });
  assert.equal(page.next_cursor, "cursor-2");
  assert.equal(page.account?.email, "user@example.com");
  assert.match(calls[0], /archived=true/);
  assert.match(calls[0], /cursor=cursor-1/);
});
