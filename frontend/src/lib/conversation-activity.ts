export type ConversationActivityState = "running" | "attention" | "done" | "error";

export const ACTIVE_RUN_STATUSES = new Set<string>([
  "queued",
  "preparing",
  "running",
  "pausing",
  "resuming",
  "retrying",
]);

const TERMINAL_OR_ATTENTION_STATUSES = new Set<string>([
  "succeeded",
  "failed",
  "waiting_user",
  "paused",
]);

const STALE_NON_ACTIVE_CARD_GRACE_MS = 8000;

export interface ConversationActivitySnapshot {
  id: string;
  active_run_count?: number;
  latest_run_id?: string | null;
  latest_run_status?: string | null;
}

export interface TrackedCardSummary {
  conversationId: string;
  runId?: string;
  status: string;
  observedAt?: number;
}

export interface PrevConversationRunState {
  runId: string | null;
  status: string | null;
  active: boolean;
}

export interface ConversationTransitionResult {
  nextPrevMap: Record<string, PrevConversationRunState>;
  newlyCompleted: Array<{ convId: string; kind: "done" | "error" | "attention" }>;
  invalidatedChatIds: string[];
  staleCardRunIds: string[];
}

function isServerRunning(chat: ConversationActivitySnapshot): boolean {
  if (chat.latest_run_status === "waiting_user" || chat.latest_run_status === "paused") {
    return false;
  }
  return (
    (chat.active_run_count ?? 0) > 0 ||
    (chat.latest_run_status ? ACTIVE_RUN_STATUSES.has(chat.latest_run_status) : false)
  );
}

/**
 * Determine whether a locally cached card has already been settled by the server.
 * When the user switches away from a running chat, its SSE stream is aborted while
 * the card remains in React memory; once the server poll reflects that the run is
 * no longer running, that local card must stop pinning the sidebar as running.
 */
export function isCardStaleAgainstServer(
  card: TrackedCardSummary,
  chat: ConversationActivitySnapshot | undefined,
  activeConversationId: string | undefined,
  activeStreamRunIds: ReadonlySet<string>,
  nowMs = Date.now(),
): boolean {
  if (
    card.runId &&
    card.conversationId === activeConversationId &&
    activeStreamRunIds.has(card.runId)
  ) {
    return false;
  }
  if (!chat) return false;
  const serverRunning = isServerRunning(chat);
  if (card.runId && chat.latest_run_id === card.runId) {
    return !serverRunning;
  }
  if (
    card.conversationId !== activeConversationId &&
    !serverRunning &&
    typeof card.observedAt === "number" &&
    nowMs - card.observedAt > STALE_NON_ACTIVE_CARD_GRACE_MS
  ) {
    return true;
  }
  return false;
}

function resolveConversationRunState(
  chat: ConversationActivitySnapshot,
  cardsForChat: TrackedCardSummary[],
  activeConversationId: string | undefined,
  activeStreamRunIds: ReadonlySet<string>,
  nowMs: number,
): PrevConversationRunState {
  const freshCards = cardsForChat.filter(
    card => !isCardStaleAgainstServer(card, chat, activeConversationId, activeStreamRunIds, nowMs),
  );
  const matchingCard = freshCards.find(card => !card.runId || card.runId !== chat.latest_run_id) ?? freshCards[0];
  const isActivelyStreaming =
    chat.id === activeConversationId &&
    Boolean(matchingCard?.runId && activeStreamRunIds.has(matchingCard.runId));

  const effectiveStatus = isActivelyStreaming
    ? matchingCard!.status
    : (matchingCard?.runId && matchingCard.runId !== chat.latest_run_id && ACTIVE_RUN_STATUSES.has(matchingCard.status))
      ? matchingCard.status
      : (chat.latest_run_status ?? matchingCard?.status ?? null);

  const effectiveRunId = isActivelyStreaming
    ? (matchingCard?.runId ?? chat.latest_run_id ?? null)
    : (matchingCard?.runId && matchingCard.runId !== chat.latest_run_id && ACTIVE_RUN_STATUSES.has(matchingCard.status))
      ? matchingCard.runId
      : (chat.latest_run_id ?? matchingCard?.runId ?? null);

  const serverRunning = isServerRunning(chat);
  const localRunning = freshCards.some(card => ACTIVE_RUN_STATUSES.has(card.status));
  const active = serverRunning || localRunning || (effectiveStatus ? ACTIVE_RUN_STATUSES.has(effectiveStatus) : false);

  return { runId: effectiveRunId, status: effectiveStatus, active };
}

export function reconcileConversationTransitions(
  conversations: ConversationActivitySnapshot[],
  cards: Record<string, TrackedCardSummary>,
  prevMap: Record<string, PrevConversationRunState>,
  activeConversationId: string | undefined,
  activeStreamRunIds: ReadonlySet<string>,
  isDocumentVisible: boolean,
  nowMs = Date.now(),
): ConversationTransitionResult {
  const byChat = new Map<string, ConversationActivitySnapshot>();
  for (const chat of conversations) byChat.set(chat.id, chat);

  const cardsByChat = new Map<string, TrackedCardSummary[]>();
  const staleCardRunIds: string[] = [];
  for (const [runId, card] of Object.entries(cards)) {
    const normalized: TrackedCardSummary = card.runId ? card : { ...card, runId };
    const chat = byChat.get(normalized.conversationId);
    if (isCardStaleAgainstServer(normalized, chat, activeConversationId, activeStreamRunIds, nowMs)) {
      staleCardRunIds.push(runId);
      continue;
    }
    const list = cardsByChat.get(normalized.conversationId) ?? [];
    list.push(normalized);
    cardsByChat.set(normalized.conversationId, list);
  }

  const nextPrevMap: Record<string, PrevConversationRunState> = {};
  const newlyCompleted: Array<{ convId: string; kind: "done" | "error" | "attention" }> = [];
  const invalidatedChatIds: string[] = [];

  for (const chat of conversations) {
    const resolved = resolveConversationRunState(
      chat,
      cardsByChat.get(chat.id) ?? [],
      activeConversationId,
      activeStreamRunIds,
      nowMs,
    );
    const previous = prevMap[chat.id];
    if (
      previous?.active &&
      !resolved.active &&
      resolved.status &&
      TERMINAL_OR_ATTENTION_STATUSES.has(resolved.status)
    ) {
      const isVisibleActive = chat.id === activeConversationId && isDocumentVisible;
      if (!isVisibleActive) {
        newlyCompleted.push({
          convId: chat.id,
          kind:
            resolved.status === "succeeded"
              ? "done"
              : resolved.status === "failed"
                ? "error"
                : "attention",
        });
      }
      invalidatedChatIds.push(chat.id);
    }
    nextPrevMap[chat.id] = resolved;
  }

  return { nextPrevMap, newlyCompleted, invalidatedChatIds, staleCardRunIds };
}

export function computeConversationActivity(
  conversations: ConversationActivitySnapshot[],
  cards: Record<string, TrackedCardSummary>,
  unreadChats: Record<string, "done" | "error" | "attention">,
  activeConversationId: string | undefined,
  activeStreamRunIds: ReadonlySet<string>,
  nowMs = Date.now(),
): Record<string, ConversationActivityState> {
  const result: Record<string, ConversationActivityState> = {};
  for (const chat of conversations) {
    const freshCards = Object.values(cards).filter(
      card =>
        card.conversationId === chat.id &&
        !isCardStaleAgainstServer(card, chat, activeConversationId, activeStreamRunIds, nowMs),
    );
    const localRunning = freshCards.some(card => ACTIVE_RUN_STATUSES.has(card.status));
    const localAttention = freshCards.some(
      card => card.status === "waiting_user" || card.status === "paused",
    );
    const serverRunning = (chat.active_run_count ?? 0) > 0 && chat.latest_run_status !== "waiting_user";
    const serverAttention =
      chat.latest_run_status === "waiting_user" || chat.latest_run_status === "paused";
    if (localRunning || serverRunning) {
      result[chat.id] = "running";
    } else if (localAttention || serverAttention) {
      result[chat.id] = "attention";
    } else if (unreadChats[chat.id]) {
      result[chat.id] = unreadChats[chat.id];
    }
  }
  return result;
}
