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
        created_at: "2026-11-22T08:00:00Z", updated_at: "2026-11-22T09:00:00Z",
      }],
      next_cursor: null,
    });
  };
  const { createAdminApi } = await import("../src/lib/admin-api.ts");
  const result = await createAdminApi("token").accountConversations("a/1", {
    query: "海 商", mode: "work", archived: "active", limit: 20,
  });
  assert.equal(result.conversations.length, 1);
  assert.equal(result.conversations[0].title, "海事法规研究");
  assert.match(calls[0].url, /\/admin\/accounts\/a%2F1\/conversations\?/);
  assert.match(calls[0].url, /query=%E6%B5%B7\+%E5%95%86/);
  assert.match(calls[0].url, /mode=work/);
  assert.equal(calls[0].options.cache, "no-store");
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
    }],
    next_cursor: null,
  });
  const { createAdminApi } = await import("../src/lib/admin-api.ts");
  const result = await createAdminApi("token").conversationDetail("a1", "c1", { limit: 50 });
  assert.equal(result.runs[0].user_question, "请查找条文");
  assert.equal(result.runs[0].public_answer, "已找到。");
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
      runs: [{ run_id: "r1", user_question: "你好", public_answer: "你好", status: "succeeded", ...leaked }],
      next_cursor: null,
    });
    await assert.rejects(
      createAdminApi("token").conversationDetail("a1", "c1", { limit: 50 }),
      error => error instanceof AdminApiError && error.code === "invalid_response",
    );
  }
});

