"""Persist the public task view independently of browser subscriptions."""

from backend.application.workspace.conversations import WorkspaceError
from backend.contracts.modules.loop import RunInput
from backend.contracts.events.streaming import StreamEvent


class RunProjection:
    def __init__(self, workspace, events):
        self.workspace, self.events = workspace, events
        self._versions = {}
        self._statuses = {}

    def __call__(self, run):
        if self.workspace is None or not run.account_id:
            return
        previous_status = self._statuses.get(run.run_id)
        if previous_status != run.status:
            # Add observation boundaries to the existing event log. No Run or
            # checkpoint schema changes; historical missing intervals stay unknown.
            stage = run.status if previous_status is not None or run.status == "queued" else "snapshot"
            self.events.emit_stream(StreamEvent(run_id=run.run_id, type="progress", stage=f"execution:{stage}"))
            self._statuses[run.run_id] = run.status
        text, sequence = self.events.visible(run.run_id)
        if sequence and (run.first_event_at is None or run.first_delta_at is None):
            items = self.events.stream_events_since(run_id=run.run_id)
            if items:
                run.first_event_at = run.first_event_at or items[0].emitted_at
                delta = next((item for item in items if item.type == "delta"), None)
                if delta:
                    run.first_delta_at = run.first_delta_at or delta.emitted_at
        run.last_sequence = max(run.last_sequence, sequence)
        if text and run.status != "succeeded":
            run.partial_output = text
        source_refs = run.execution_state.get("source_refs", []) if isinstance(run.execution_state, dict) else []
        if not isinstance(source_refs, list):
            source_refs = []
        # The public projection only accepts the stable, human-readable fields
        # produced by the Harness. A malformed checkpoint must not block the
        # conversation from being saved.
        source_refs = [item for item in source_refs if isinstance(item, dict) and item.get("source_id")]
        artifacts = run.execution_state.get("artifacts", []) if isinstance(run.execution_state, dict) else []
        if not isinstance(artifacts, list):
            artifacts = []
        artifacts = [item for item in artifacts if isinstance(item, dict) and item.get("artifact_id")]
        has_checkpoint = bool(
            run.partial_output
            or (run.execution_state or {}).get("records")
            or (run.execution_state or {}).get("tool_results")
            or (run.execution_state or {}).get("delivered_stages")
            or (run.execution_state or {}).get("task_state")
        )
        key = (
            run.revision,
            run.status,
            run.output,
            run.partial_output,
            run.last_sequence,
            has_checkpoint,
            tuple(item.get("source_id") for item in source_refs),
            tuple(item.get("artifact_id") for item in artifacts),
            tuple(run.continuation_guidance),
            run.context_compaction_count,
        )
        if self._versions.get(run.run_id) == key:
            return
        value = RunInput.model_validate(run.payload)
        if run.answers:
            facts = dict(value.facts)
            facts["user_answers"] = {**facts.get("user_answers", {}), **run.answers}
            value = value.model_copy(update={"facts": facts})
        try:
            self.workspace.record_run(
                account_id=run.account_id,
                conversation_id=run.conversation_id or run.session_id,
                run_id=run.run_id, question=value.text, status=run.status,
                answer=run.output, partial_output=run.partial_output,
                revision=run.revision, client_request_id=run.client_request_id,
                retry_of_run_id=run.retry_of_run_id,
                payload=value.model_dump(mode="json"), material_ids=value.material_ids,
                error=run.error.message if run.error else None,
                error_code=run.error.code if run.error else None,
                model_profile_id=(value.model_snapshot.profile_id if value.model_snapshot else value.model_profile_id),
                model_profile_revision_id=(getattr(value.model_snapshot, "profile_revision_id", None) if value.model_snapshot else None),
                provider_revision_id=(getattr(value.model_snapshot, "provider_revision_id", None) if value.model_snapshot else None),
                model_snapshot=(value.model_snapshot.model_dump(mode="json") if value.model_snapshot else None),
                last_sequence=run.last_sequence, first_event_at=run.first_event_at,
                first_delta_at=run.first_delta_at, last_event_at=run.completed_at,
                progress={
                    "completed_units": run.completed_units,
                    "total_units": run.total_units,
                    "current_unit": run.current_unit,
                    "has_checkpoint": has_checkpoint,
                    "source_refs": source_refs,
                    "artifacts": artifacts,
                    "continuation_guidance": list(run.continuation_guidance),
                    "context_compaction_count": run.context_compaction_count,
                },
                created_at=run.created_at, completed_at=run.completed_at,
                include_archived=True,
            )
        except WorkspaceError:
            return  # Conversation was explicitly removed, never recreate it.
        self._versions[run.run_id] = key
