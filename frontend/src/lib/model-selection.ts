import type { Message, ModelRunSnapshot } from "./workbench-api";

export interface ModelSwitchEvent {
  id: string;
  conversationId: string;
  createdAt: string;
  afterRunId: string | null;
  fromProfileId: string;
  toProfileId: string;
  fromLabel: string;
  toLabel: string;
}

export function modelLabel(profileId: string): string {
  const modelId = profileId.startsWith("relay:") ? profileId.slice(6) : profileId;
  return ({ "deepseek-flash": "DeepSeek Flash", "deepseek-v4-pro": "DeepSeek Pro" } as Record<string, string>)[modelId] || modelId;
}

export function modelSwitchText(event: Pick<ModelSwitchEvent, "fromLabel" | "toLabel">): string {
  return `模型已从 ${event.fromLabel} 切换至 ${event.toLabel}`;
}

export function effectiveModelSwitches(messages: Message[], conversationId: string): Map<string, ModelSwitchEvent> {
  const events = new Map<string, ModelSwitchEvent>();
  let previous: ModelRunSnapshot | null = null;
  let previousRunId: string | null = null;
  for (const message of messages) {
    if (message.role !== "user" || !message.run_id) continue;
    const current = message.model_snapshot;
    if (current && previous && current.profile_id !== previous.profile_id) {
      events.set(message.id, {
        id: `model-switch:${message.run_id}`,
        conversationId,
        createdAt: message.created_at,
        afterRunId: previousRunId,
        fromProfileId: previous.profile_id,
        toProfileId: current.profile_id,
        fromLabel: modelLabel(previous.profile_id),
        toLabel: modelLabel(current.profile_id),
      });
    }
    // An older run without a model snapshot cannot establish a model change.
    previous = current ?? null;
    previousRunId = message.run_id;
  }
  return events;
}
