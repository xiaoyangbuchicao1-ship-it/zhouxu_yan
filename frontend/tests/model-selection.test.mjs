import assert from "node:assert/strict";
import test from "node:test";
import { effectiveModelSwitches, modelSwitchText } from "../src/lib/model-selection.ts";
import { visibleAnswerActions } from "../src/lib/answer-actions.ts";

const question = (runId, profileId) => ({
  id: `${runId}:user`, role: "user", run_id: runId, content: "你好", created_at: "2026-11-21T08:00:00Z",
  model_snapshot: profileId ? { profile_id: profileId } : null,
});

test("切换记录跟随实际使用新模型的问题，刷新后位置不变", () => {
  const questions = [question("one", "deepseek-flash"), question("two", "relay:gpt-6-astra"), question("three", "relay:gpt-6-astra")];
  const events = effectiveModelSwitches(questions, "chat-one");
  assert.equal(events.size, 1);
  const event = events.get("two:user");
  assert.equal(event.afterRunId, "one");
  assert.equal(event.conversationId, "chat-one");
  assert.equal(modelSwitchText(event), "模型已从 DeepSeek Flash 切换至 gpt-6-astra");
  assert.deepEqual(effectiveModelSwitches(JSON.parse(JSON.stringify(questions)), "chat-one"), events);
  assert.equal(effectiveModelSwitches([question("other", "relay:gpt-6-astra")], "chat-two").size, 0);
  assert.equal(effectiveModelSwitches([question("old", null), question("new", "relay:gpt-6-astra")], "chat-one").size, 0);
});

test("还没有正文的暂停任务能继续，有正文后才出现复制和保存", () => {
  const message = { content: "", status: "paused", recovery: { can_continue: true, can_retry: true } };
  assert.deepEqual(visibleAnswerActions(message), { recovery: true, answer: false });
  assert.deepEqual(visibleAnswerActions({ ...message, status: "failed" }), { recovery: true, answer: false });
  assert.deepEqual(visibleAnswerActions({ ...message, content: "已生成正文" }), { recovery: true, answer: true });
  assert.deepEqual(visibleAnswerActions({ ...message, content: "正在生成" }, "running"), { recovery: false, answer: false });
});
