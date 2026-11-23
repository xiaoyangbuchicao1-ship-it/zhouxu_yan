import assert from "node:assert/strict";
import test from "node:test";
import { createAdminApi } from "../src/lib/admin-api.ts";
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

test("技能区严格区分 5 个已开放能力与 6 个持续推进中占位项", () => {
  const grouped = splitSkillsByReadiness(
    [
      { id: "law-research", name: "法律检索", summary: "s", instructions: "", version: 2, owner_id: null, visibility: "builtin", status: "ready" },
      { id: "case-assessment", name: "案件研判", summary: "s", instructions: "", version: 2, owner_id: null, visibility: "builtin", status: "coming_soon" },
      { id: "custom-zip", name: "我的包", summary: "s", instructions: "ok", version: 1, owner_id: "u1", visibility: "private" },
    ],
    "u1",
  );
  assert.equal(grouped.ready.length, 1);
  assert.equal(grouped.ready[0].id, "law-research");
  assert.equal(grouped.comingSoon.length, 1);
  assert.equal(grouped.comingSoon[0].id, "case-assessment");
  assert.equal(grouped.mine.length, 1);
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
