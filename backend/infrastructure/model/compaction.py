"""Minimal Python port of CC Switch's Codex compaction bridge.

Source: farion1231/cc-switch, fc665e818e842bf6542e5432ca0fe37d33ffabe1,
src-tauri/src/proxy/providers/codex_compaction.rs.
Copyright (c) 2025 Jason Young. MIT; see third_party/cc-switch/LICENSE.

Reuse the handoff prompt and completion gate. ZhouXu already stores plain-text
summaries and coverage, so Codex's encrypted-content envelope is unnecessary.
"""

COMPACT_PROMPT = """You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task.

Include:
- Current progress and key decisions made
- Important context, constraints, or user preferences
- What remains to be done (clear next steps)
- Any critical data, examples, or references needed to continue

Be concise, structured, and focused on helping the next LLM seamlessly continue the work.
"""


def compaction_incomplete_reason(finish_reason: str | None) -> str | None:
    """Port CC Switch's completion gate, with native Gemini/Responses success."""
    if finish_reason is None or not finish_reason.strip():
        return None
    reason = finish_reason.strip().lower()
    if reason in {"stop", "end_turn", "stop_sequence", "completed"}:
        return None
    if reason in {"length", "max_tokens", "model_context_window_exceeded"}:
        return "max_output_tokens"
    if reason in {"content_filter", "sensitive", "refusal"}:
        return "content_filter"
    return reason
