import type { Message, RunStatus } from "./workbench-api";

export function visibleAnswerActions(message: Pick<Message, "content" | "status" | "recovery">, liveStatus?: RunStatus) {
  const status = liveStatus ?? message.status;
  const settled = status !== undefined && ["succeeded", "paused", "failed", "cancelled"].includes(status);
  return {
    recovery: settled && Boolean(message.recovery?.can_continue || message.recovery?.can_retry),
    answer: settled && Boolean(message.content.trim()),
  };
}
