import assert from "node:assert/strict";
import test from "node:test";

test("会话列表只请求当前账户，并把筛选条件放进网址", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return Response.json({
      conversations: [{
        id: "c1", account_id: "a/1", title: "海事法规研究", mode: "work",
        archived: false, run_count: 2, latest_status: "succeeded",
        latest_run_at: "2026-11-22T08:45:00Z",
        created_at: "2026-11-22T08:00:00Z", updated_at: "2026-11-22T09:00:00Z",
      }],
      status_counts: { all: 4, succeeded: 1, active: 1, failed: 1, no_runs: 1 },
      next_cursor: null,
    });
  };
  const { createAdminApi } = await import("../src/lib/admin-api.ts");
  const result = await createAdminApi("token").accountConversations("a/1", {
    query: "海 商", mode: "work", status: "failed", archived: "active", limit: 20,
  });
  assert.equal(result.conversations.length, 1);
  assert.equal(result.conversations[0].title, "海事法规研究");
  assert.equal(result.status_counts?.failed, 1);
  assert.match(calls[0].url, /\/admin\/accounts\/a%2F1\/conversations\?/);
  assert.match(calls[0].url, /query=%E6%B5%B7\+%E5%95%86/);
  assert.match(calls[0].url, /mode=work/);
  assert.match(calls[0].url, /status=failed/);
  assert.equal(calls[0].options.cache, "no-store");
});

test("全局最近任务支持按状态筛选参数传给后端", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return Response.json({ tasks: [], next_cursor: null });
  };
  const { createAdminApi } = await import("../src/lib/admin-api.ts");
  await createAdminApi("token").tasks(30, undefined, "failed");
  assert.match(calls[0].url, /\/admin\/tasks\?/);
  assert.match(calls[0].url, /limit=30/);
  assert.match(calls[0].url, /status=failed/);
});

test("会话详情只接受公开正文和安全运行信息", async () => {
  globalThis.fetch = async () => Response.json({
    conversation: {
      id: "c1", account_id: "a1", title: "海事法规研究", mode: "work",
      archived: false, created_at: "2026-11-22T08:00:00Z", updated_at: "2026-11-22T09:00:00Z",
    },
    runs: [{
      run_id: "r1", user_question: "请查找条文", public_answer: "已找到。",
      status: "succeeded", model: "gpt-6.2-sol", provider: "relay",
      protocol: "openai_responses", thinking_mode: "low", error_code: null,
      created_at: "2026-11-22T08:00:00Z", completed_at: "2026-11-22T08:00:02Z",
      elapsed_ms: 2000, nodes: [{ id: "retrieve", label: "查找资料", status: "succeeded" }],
      sources: [{ source_id: "law-22", kind: "law", title: "海商法" }],
      artifacts: [{ artifact_id: "artifact-1", name: "论文.pdf", media_type: "application/pdf", size_bytes: 12, sha256: "a".repeat(64), kind: "document" }],
    }],
    next_cursor: null,
  });
  const { createAdminApi } = await import("../src/lib/admin-api.ts");
  const result = await createAdminApi("token").conversationDetail("a1", "c1", { limit: 50 });
  assert.equal(result.runs[0].user_question, "请查找条文");
  assert.equal(result.runs[0].public_answer, "已找到。");
  assert.equal(result.runs[0].sources[0].title, "海商法");
  assert.equal(result.runs[0].artifacts[0].name, "论文.pdf");
});

test("API若夹带路径、完整payload或提示词，管理台拒绝渲染", async () => {
  const { createAdminApi, AdminApiError } = await import("../src/lib/admin-api.ts");
  for (const leaked of [
    { payload_json: "{}" },
    { disk_path: "C:/private/file.pdf" },
    { system_prompt: "hidden" },
    { api_key: "secret" },
    { hidden_reasoning: "private" },
    { skill_source: "private source" },
  ]) {
    globalThis.fetch = async () => Response.json({
      conversation: {
        id: "c1", account_id: "a1", title: "测试", mode: "chat", archived: false,
        created_at: "2026-11-22T08:00:00Z", updated_at: "2026-11-22T08:00:00Z",
      },
      runs: [{ run_id: "r1", user_question: "你好", public_answer: "你好", status: "succeeded", nodes: [], sources: [], artifacts: [], ...leaked }],
      next_cursor: null,
    });
    await assert.rejects(
      createAdminApi("token").conversationDetail("a1", "c1", { limit: 50 }),
      error => error instanceof AdminApiError && error.code === "invalid_response",
    );
  }
});

test("能力页请求真实接口，并把权限修改发回服务端", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    if (String(url).includes("/admin/capabilities/") && options?.method === "PATCH") {
      return Response.json({
        account_id: "a1", email: "user@example.com", display_name: "用户",
        model_policy: "allowlist", allowed_models: ["deepseek-flash"],
        skill_policy: "builtin_only", can_upload_skill: false, updated_at: null,
      });
    }
    return Response.json({
      accounts: [{ account_id: "a1", email: "user@example.com", display_name: "用户", model_policy: "default", allowed_models: [], skill_policy: "default", can_upload_skill: true, updated_at: null }],
    });
  };
  const { createAdminApi } = await import("../src/lib/admin-api.ts");
  const api = createAdminApi("token");
  const capabilities = await api.capabilities();
  assert.equal(capabilities.accounts[0].account_id, "a1");
  const updated = await api.updateCapability("a1", { model_policy: "allowlist", allowed_models: ["deepseek-flash"] });
  assert.equal(updated.model_policy, "allowlist");
  assert.equal(calls[1].options.method, "PATCH");
  assert.deepEqual(JSON.parse(calls[1].options.body), { model_policy: "allowlist", allowed_models: ["deepseek-flash"] });
});

