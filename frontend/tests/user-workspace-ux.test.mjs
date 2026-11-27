import assert from "node:assert/strict";
import test from "node:test";
import { createAdminApi } from "../src/lib/admin-api.ts";
import { workbenchApi } from "../src/lib/workbench-api.ts";
import { splitSkillsByReadiness, matchPromptCommands } from "../src/lib/workspace-commands.ts";

test("审计日志接口传递分类、结果、搜索与游标参数给后端", async () => {
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return new Response(
      JSON.stringify({
        events: [],
        category_counts: { all: 3, account: 1, capability: 1, model: 0, conversation: 1 },
        next_cursor: null,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  };
  const api = createAdminApi("admin-token");
  const result = await api.audit({
    category: "account",
    outcome: "failed",
    query: "sailor@example.com",
    cursor: "cur-1",
    limit: 25,
  });
  assert.equal(result.category_counts?.account, 1);
  assert.match(calls[0], /\/api\/admin\/audit\?/);
  assert.match(calls[0], /category=account/);
  assert.match(calls[0], /outcome=failed/);
  assert.match(calls[0], /q=sailor%40example\.com/);
  assert.match(calls[0], /cursor=cur-1/);
  assert.match(calls[0], /limit=25/);
});

test("技能区把内置技能和用户上传的技能分开，没有占位项", () => {
  const grouped = splitSkillsByReadiness(
    [
      { id: "maritime-legal-research", name: "条文检索", summary: "s", instructions: "", version: 2, owner_id: null, visibility: "builtin", status: "ready" },
      { id: "custom-zip", name: "我的包", summary: "s", instructions: "ok", version: 1, owner_id: "u1", visibility: "private" },
    ],
    "u1",
  );
  assert.equal(grouped.ready.length, 1);
  assert.equal(grouped.ready[0].id, "maritime-legal-research");
  assert.equal(grouped.mine.length, 1);
  assert.equal(grouped.comingSoon, undefined);
});

test("斜杠快捷指令按输入前缀实时过滤且不暴露内置隐藏指令", () => {
  const commands = [
    { id: "1", trigger: "review", title: "合同审查", content: "核对风险", mode_hint: "work", is_builtin: true, created_at: "", updated_at: "" },
    { id: "2", trigger: "paper", title: "海事论文", content: "起草论文", mode_hint: "work", is_builtin: true, created_at: "", updated_at: "" },
  ];
  assert.equal(matchPromptCommands("/rev", commands).length, 1);
  assert.equal(matchPromptCommands("/rev", commands)[0].trigger, "review");
  assert.equal(matchPromptCommands("普通文本 /rev", commands).length, 0);
  assert.equal(matchPromptCommands("/", commands).length, 2);
});

test("workbenchApi.detail 保留网络来源链接、已读正文与回答引用标记", async () => {
  const prevFetch = globalThis.fetch;
  const prevWindow = globalThis.window;
  globalThis.window = { setTimeout, clearTimeout };
  globalThis.fetch = async (url) => {
    const target = String(url);
    if (target.endsWith("/snapshot")) {
      return new Response(
        JSON.stringify({
          conversation_id: "conv-1",
          status: "open",
          queued_run_ids: [],
          waiting_run_ids: [],
          runs: [
            {
              run_id: "run-1",
              conversation_id: "conv-1",
              status: "succeeded",
              revision: 2,
              payload: { mode: "chat", text: "测试", facts: {}, constraints: [], material_ids: [] },
              pending_request_ids: [],
              answers: {},
              recovery: { mode: "none", can_continue: false, can_retry: false, message: null },
              sources: [
                {
                  source_id: "web-1",
                  kind: "web",
                  title: "中华人民共和国海商法解读",
                  url: "https://example.com/maritime",
                  domain: "example.com",
                  snippet: "摘要内容",
                  verified_read: true,
                  used_in_answer: true,
                },
                {
                  source_id: "law-1",
                  kind: "law",
                  title: "海商法",
                  locator: "第五十一条",
                  used_in_answer: false,
                },
              ],
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    return new Response(
      JSON.stringify({
        conversation: { conversation_id: "conv-1", title: "测试", mode: "chat", pinned: false, created_at: "2026-11-27T00:00:00Z", updated_at: "2026-11-27T00:00:00Z" },
        messages: [
          {
            message_id: "m-1",
            role: "assistant",
            content: "回答",
            created_at: "2026-11-27T00:00:00Z",
            run_id: "run-1",
            sources: [],
          },
        ],
        materials: [],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  };
  try {
    const api = workbenchApi("user-token");
    const result = await api.detail("conv-1");
    const sources = result.messages[0].sources || [];
    assert.equal(sources.length, 2);
    assert.equal(sources[0].kind, "web");
    assert.equal(sources[0].url, "https://example.com/maritime");
    assert.equal(sources[0].domain, "example.com");
    assert.equal(sources[0].snippet, "摘要内容");
    assert.equal(sources[0].verified_read, true);
    assert.equal(sources[0].used_in_answer, true);
    assert.equal(sources[1].kind, "law");
    assert.equal(sources[1].used_in_answer, false);
  } finally {
    globalThis.fetch = prevFetch;
    globalThis.window = prevWindow;
  }
});
