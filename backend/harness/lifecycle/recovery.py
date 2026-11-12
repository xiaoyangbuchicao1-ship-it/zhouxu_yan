"""One recovery policy shared by the scheduler and the Workbench projection."""

from copy import deepcopy
from typing import Any

from backend.contracts.harness.lifecycle import RunEnvelope, RunRecovery

# These failures do not mean that the user's request is wrong.  They can be
# retried from the saved checkpoint after the transient provider state has
# been discarded.  Keeping the policy here prevents each interface from
# growing its own list of provider and budget codes.
CHECKPOINT_CODES = frozenset(
    {
        "cancelled",
        "context_budget_exceeded",
        "context_recovery_exhausted",
        "model_invalid_response",
        "model_output_incomplete",
        "model_generation_stopped",
        "invalid_decision",
        "model_unavailable",
        "module_failed",
        "missing_output",
        "timeout",
        "tool_failed",
        "rag_call_limit",
        "rag_tool_budget",
        "execution_unavailable",
        "execution_timeout",
        "artifact_validation_failed",
        "round_limit",
        "retry_limit",
        "tool_no_progress",
        "reflection_failed",
        "task_blocked",
        "mixed_input_incomplete",
        "required_context_budget",
    }
)

# These failures are safe to attempt again after the external condition is
# repaired, but a same-Run continuation has no useful checkpoint to resume.
# The code list makes the decision stable after a browser reload, when the
# persisted workspace row does not carry the internal retryable flag.
RETRYABLE_CODES = frozenset(
    {
        "case_service_unavailable",
        "case_source_mismatch",
        "executor_failed",
        "material_processing_failed",
        "model_quota_exhausted",
        "rag_embedding_failed",
        "rag_invalid_evidence",
        "rag_review_failed",
        "rag_search_failed",
        "rag_timeout",
        "invalid_completion",
    }
)

# Configuration, authorization, and malformed persisted state require a new
# request or an operator change.  Presenting a Continue button for these
# states only sends the user back through the same failure.
NON_RECOVERABLE_CODES = frozenset(
    {
        "credential_missing",
        "model_profile_invalid",
        "context_configuration",
        "invalid_endpoint",
        "invalid_context",
        "memory_scope",
        "required_context_unavailable",
        "missing_input",
        "execution_invalid_path",
        "execution_file_not_found",
        "execution_file_too_large",
        "execution_conflict",
        "execution_exact_match",
    }
)

PROTOCOL_RESET_CODES = frozenset(
    {
        "context_budget_exceeded",
        "context_recovery_exhausted",
        "required_context_budget",
        "model_invalid_response",
        "model_output_incomplete",
        "model_generation_stopped",
        "model_unavailable",
        "timeout",
    }
)

CONTEXT_RECOVERY_CODES = frozenset(
    {
        "context_budget_exceeded",
        "context_recovery_exhausted",
        "required_context_budget",
    }
)


def _checkpoint_exists(run: RunEnvelope) -> bool:
    state = run.execution_state if isinstance(run.execution_state, dict) else {}
    return bool(
        run.partial_output
        or state.get("records")
        or state.get("tool_results")
        or state.get("delivered_stages")
        or state.get("task_state")
    )


def recovery_for_state(
    *,
    status: str,
    error_code: str | None = None,
    retryable: bool = False,
    partial_output: str | None = None,
    execution_state: dict[str, Any] | None = None,
) -> RunRecovery:
    """Project either a live Run or a persisted workspace row."""

    if status in {"queued", "running", "waiting_user", "completed"}:
        return RunRecovery()

    if status in {"interrupted", "stopped"}:
        return RunRecovery(
            mode="checkpoint",
            can_continue=True,
            can_retry=True,
            message=(
                "已保留当前进度，可以继续处理。"
                if status == "interrupted"
                else "已停止，当前进度已保留。"
            ),
        )

    code = error_code
    checkpoint = bool(
        partial_output
        or (execution_state or {}).get("records")
        or (execution_state or {}).get("tool_results")
        or (execution_state or {}).get("delivered_stages")
        or (execution_state or {}).get("task_state")
    )
    if code in NON_RECOVERABLE_CODES and code not in RETRYABLE_CODES:
        return RunRecovery(
            mode="none",
            can_retry=False,
            message="这次处理没有完成。",
        )
    if code in CHECKPOINT_CODES or (checkpoint and code is not None):
        return RunRecovery(
            mode="checkpoint",
            can_continue=checkpoint or code in CHECKPOINT_CODES,
            can_retry=True,
            message="已保留当前进度，可以继续处理。",
        )
    if code in RETRYABLE_CODES or retryable:
        return RunRecovery(
            mode="fresh_attempt",
            can_retry=True,
            message="这次处理没有完成，可以重新处理。",
        )
    return RunRecovery(message="这次处理没有完成。")


def recovery_for_run(run: RunEnvelope) -> RunRecovery:
    """Return only actions that have a real, supported recovery path."""

    return recovery_for_state(
        status=run.status,
        error_code=run.error.code if run.error else None,
        retryable=run.error.retryable if run.error else False,
        partial_output=run.partial_output,
        execution_state=run.execution_state,
    )


def prepare_checkpoint_for_continue(
    state: dict[str, Any] | None, *, error_code: str | None
) -> dict[str, Any]:
    """Prepare a saved checkpoint for one explicit user continuation.

    Provider continuation frames are protocol state, not task data.  A failed
    provider exchange must not be replayed as if it were valid.  Context
    failures request another bounded compaction pass; the Harness still owns
    the actual selection and validation.
    """

    prepared = deepcopy(state) if isinstance(state, dict) else {}
    attempts = prepared.get("recovery_attempts", 0)
    prepared["recovery_attempts"] = attempts + 1 if isinstance(attempts, int) else 1
    prepared["recovery_mode"] = (
        "context" if error_code in CONTEXT_RECOVERY_CODES else "checkpoint"
    )
    if error_code in PROTOCOL_RESET_CODES:
        prepared["model_continuation"] = {}
    if error_code in CONTEXT_RECOVERY_CODES:
        prepared["compaction_requested"] = True
    # This is private diagnostic state and is never included in the public
    # Workbench recovery contract.
    prepared["last_failure_code"] = error_code
    return prepared


__all__ = [
    "CONTEXT_RECOVERY_CODES",
    "CHECKPOINT_CODES",
    "NON_RECOVERABLE_CODES",
    "RETRYABLE_CODES",
    "prepare_checkpoint_for_continue",
    "recovery_for_state",
    "recovery_for_run",
]
