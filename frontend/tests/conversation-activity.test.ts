import test from "node:test";
import assert from "node:assert/strict";
import {
  computeConversationActivity,
  isCardStaleAgainstServer,
  reconcileConversationTransitions,
} from "../src/lib/conversation-activity.ts";

test("background chat transitions from running to done without visiting it", () => {
  const now = 100_000;
  const chatId = "conv-criminal-law-1";
  const runId = "run-1";

  const cards = {
    [runId]: {
      conversationId: chatId,
      runId,
      status: "running",
      observedAt: now - 5_000,
    },
  };

  // While user was inside conv-criminal-law-1 with an active stream, it counts as running.
  const initial = reconcileConversationTransitions(
    [{ id: chatId, active_run_count: 1, latest_run_id: runId, latest_run_status: "running" }],
    cards,
    {},
    chatId,
    new Set([runId]),
    true,
    now - 4_000,
  );
  assert.equal(initial.nextPrevMap[chatId].active, true);
  assert.deepEqual(initial.staleCardRunIds, []);

  // User clicks "New Chat" (activeConversationId = undefined, stream aborted), then backend finishes run-1.
  const settled = reconcileConversationTransitions(
    [{ id: chatId, active_run_count: 0, latest_run_id: runId, latest_run_status: "succeeded" }],
    cards,
    initial.nextPrevMap,
    undefined,
    new Set(),
    true,
    now,
  );

  assert.deepEqual(settled.staleCardRunIds, [runId]);
  assert.deepEqual(settled.newlyCompleted, [{ convId: chatId, kind: "done" }]);
  assert.deepEqual(settled.invalidatedChatIds, [chatId]);
  assert.equal(settled.nextPrevMap[chatId].active, false);
  assert.equal(settled.nextPrevMap[chatId].status, "succeeded");

  // Even before setCards commits stale removal, activity reflects "done" instead of "running".
  const activity = computeConversationActivity(
    [{ id: chatId, active_run_count: 0, latest_run_id: runId, latest_run_status: "succeeded" }],
    cards,
    { [chatId]: "done" },
    undefined,
    new Set(),
    now,
  );
  assert.equal(activity[chatId], "done");
});

test("background chat emits error or attention badge when failing or waiting_user", () => {
  const now = 200_000;
  const prevMap = {
    "conv-fail": { runId: "run-f", status: "running", active: true },
    "conv-wait": { runId: "run-w", status: "running", active: true },
  };
  const cards = {
    "run-f": { conversationId: "conv-fail", runId: "run-f", status: "running", observedAt: now - 3000 },
    "run-w": { conversationId: "conv-wait", runId: "run-w", status: "running", observedAt: now - 3000 },
  };
  const conversations = [
    { id: "conv-fail", active_run_count: 0, latest_run_id: "run-f", latest_run_status: "failed" },
    { id: "conv-wait", active_run_count: 1, latest_run_id: "run-w", latest_run_status: "waiting_user" },
  ];

  const settled = reconcileConversationTransitions(
    conversations,
    cards,
    prevMap,
    "conv-other",
    new Set(),
    true,
    now,
  );

  assert.deepEqual(settled.newlyCompleted, [
    { convId: "conv-fail", kind: "error" },
    { convId: "conv-wait", kind: "attention" },
  ]);
  const activity = computeConversationActivity(
    conversations,
    {},
    { "conv-fail": "error", "conv-wait": "attention" },
    "conv-other",
    new Set(),
    now,
  );
  assert.equal(activity["conv-fail"], "error");
  assert.equal(activity["conv-wait"], "attention");
});

test("newly submitted run keeps running during pre-poll window then settles when server catches up", () => {
  const now = 300_000;
  const chatId = "conv-multi-turn";
  const cards = {
    "run-2": { conversationId: chatId, runId: "run-2", status: "running", observedAt: now - 1_000 },
  };

  // Server history still shows old run-1 succeeded; run-2 is only 1s old so must stay active.
  assert.equal(
    isCardStaleAgainstServer(
      cards["run-2"],
      { id: chatId, active_run_count: 0, latest_run_id: "run-1", latest_run_status: "succeeded" },
      "conv-another",
      new Set(),
      now,
    ),
    false,
  );

  const duringRace = reconcileConversationTransitions(
    [{ id: chatId, active_run_count: 0, latest_run_id: "run-1", latest_run_status: "succeeded" }],
    cards,
    { [chatId]: { runId: "run-1", status: "succeeded", active: false } },
    "conv-another",
    new Set(),
    true,
    now,
  );
  assert.equal(duringRace.nextPrevMap[chatId].active, true);
  assert.equal(duringRace.nextPrevMap[chatId].runId, "run-2");

  // Server catches up with run-2 succeeded.
  const settled = reconcileConversationTransitions(
    [{ id: chatId, active_run_count: 0, latest_run_id: "run-2", latest_run_status: "succeeded" }],
    cards,
    duringRace.nextPrevMap,
    "conv-another",
    new Set(),
    true,
    now + 3_000,
  );
  assert.deepEqual(settled.staleCardRunIds, ["run-2"]);
  assert.deepEqual(settled.newlyCompleted, [{ convId: chatId, kind: "done" }]);
});

test("visible active chat completes cleanly without creating an unread badge", () => {
  const now = 400_000;
  const chatId = "conv-visible";
  const settled = reconcileConversationTransitions(
    [{ id: chatId, active_run_count: 0, latest_run_id: "run-v", latest_run_status: "succeeded" }],
    { "run-v": { conversationId: chatId, runId: "run-v", status: "succeeded", observedAt: now } },
    { [chatId]: { runId: "run-v", status: "running", active: true } },
    chatId,
    new Set(),
    true,
    now,
  );
  assert.deepEqual(settled.newlyCompleted, []);
  assert.deepEqual(settled.invalidatedChatIds, [chatId]);
  assert.equal(settled.nextPrevMap[chatId].active, false);
});
