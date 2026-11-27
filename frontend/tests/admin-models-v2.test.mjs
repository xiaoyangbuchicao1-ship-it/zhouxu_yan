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

test("默认中国主流策略、分组增删改及用户分组绑定调用真实后台接口", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), method: options?.method ?? "GET", body: options?.body ?? null });
    if (String(url).endsWith("/admin/models/apply-domestic-default")) {
      return Response.json({
        dry_run: true,
        changes: [
          { profile_id: "relay:claude-3.7", label: "Claude 3.7", is_domestic: false, previous_scope: "all", target_scope: "allowlist" },
        ],
        default_profile_id: "deepseek-flash",
        default_profile_label: "DeepSeek Flash",
        domestic_open_count: 2,
      });
    }
    if (String(url).endsWith("/admin/model-groups") && (!options?.method || options.method === "GET")) {
      return Response.json({
        groups: [
          { id: "grp-1", name: "海外研究组", description: "开放海外模型", allowed_models: ["relay:claude-3.7"], member_count: 2, created_at: "2026-11-27T00:00:00Z", updated_at: "2026-11-27T00:00:00Z" },
        ],
      });
    }
    if (String(url).endsWith("/admin/model-groups") && options?.method === "POST") {
      return Response.json({
        id: "grp-2",
        name: "课题组",
        description: "",
        allowed_models: ["relay:gpt-4o"],
        member_count: 0,
        created_at: "2026-11-27T00:00:00Z",
        updated_at: "2026-11-27T00:00:00Z",
      });
    }
    if (String(url).includes("/admin/capabilities/acc-1")) {
      return Response.json({
        account_id: "acc-1",
        email: "user@example.com",
        display_name: "研究员",
        model_policy: "default",
        allowed_models: [],
        group_id: "grp-2",
        group_name: "课题组",
        group_allowed_models: ["relay:gpt-4o"],
        skill_policy: "default",
        can_upload_skill: true,
        material_quota_mb: null,
        material_used_bytes: 0,
        updated_at: "2026-11-27T00:00:00Z",
      });
    }
    return Response.json({ deleted: true, group_id: "grp-2" });
  };

  const { createAdminApi } = await import("../src/lib/admin-api.ts");
  const api = createAdminApi("token");
  const preview = await api.applyDomesticDefault(true);
  assert.equal(preview.dry_run, true);
  assert.equal(preview.changes[0].target_scope, "allowlist");

  const list = await api.modelGroups();
  assert.equal(list.groups[0].name, "海外研究组");

  const created = await api.createModelGroup({ name: "课题组", allowed_models: ["relay:gpt-4o"] });
  assert.equal(created.id, "grp-2");

  const cap = await api.updateCapability("acc-1", { group_id: "grp-2" });
  assert.equal(cap.group_name, "课题组");

  const removed = await api.deleteModelGroup("grp-2");
  assert.equal(removed.deleted, true);
});

test("管理员账户备注别名与内联图片附件二进制接口调用正确路径", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), method: options?.method ?? "GET", body: options?.body ?? null });
    if (String(url).includes("/alias")) {
      return Response.json({
        id: "u1",
        email: "user@example.com",
        display_name: "研究员",
        alias: "张三课题组",
        alias_note: "重点跟进",
        status: "active",
        role: "user",
        created_at: "2026-11-27T00:00:00Z",
        updated_at: "2026-11-27T00:00:00Z",
      });
    }
    return new Response(new Uint8Array([137, 80, 78, 71]), {
      status: 200,
      headers: { "Content-Type": "image/png" },
    });
  };
  const { createAdminApi } = await import("../src/lib/admin-api.ts");
  const api = createAdminApi("token");
  const updated = await api.setAccountAlias("u1", { alias: "张三课题组", note: "重点跟进" });
  assert.equal(updated.alias, "张三课题组");
  assert.equal(calls[0].method, "PUT");
  assert.match(calls[0].url, /\/admin\/accounts\/u1\/alias$/);

  const blob = await api.materialBlob("mat-99");
  assert.equal(blob.size, 4);
  assert.match(calls[1].url, /\/admin\/materials\/mat-99\/download\?inline=1$/);
});
