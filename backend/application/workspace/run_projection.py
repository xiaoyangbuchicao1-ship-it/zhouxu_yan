"""Persist the public task view independently of browser subscriptions."""

from backend.application.workspace.conversations import WorkspaceError
from backend.contracts.modules.loop import RunInput


class RunProjection:
    def __init__(self, workspace, events):
        self.workspace, self.events = workspace, events
        self._versions = {}

    def __call__(self, run):
        if self.workspace is None or not run.account_id:
            return
        text, sequence = self.events.visible(run.run_id)
        if sequence and (run.first_event_at is None or run.first_delta_at is None):
            items = self.events.stream_events_since(run_id=run.run_id)
            if items:
                run.first_event_at = run.first_event_at or items[0].emitted_at
                delta = next((item for item in items if item.type == "delta"), None)
                if delta:
                    run.first_delta_at = run.first_delta_at or delta.emitted_at
        run.last_sequence = max(run.last_sequence, sequence)
        if text and run.status != "completed":
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
        key = (
            run.revision,
            run.status,
            run.output,
            run.partial_output,
            run.last_sequence,
            tuple(item.get("source_id") for item in source_refs),
            tuple(item.get("artifact_id") for item in artifacts),
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
                last_sequence=run.last_sequence, first_event_at=run.first_event_at,
                first_delta_at=run.first_delta_at, last_event_at=run.completed_at,
                progress={
                    "completed_units": run.completed_units,
                    "total_units": run.total_units,
                    "current_unit": run.current_unit,
                    "source_refs": source_refs,
                    "artifacts": artifacts,
                },
                created_at=run.created_at, completed_at=run.completed_at,
                include_archived=True,
            )
        except WorkspaceError:
            return  # Conversation was explicitly removed, never recreate it.
        self._versions[run.run_id] = key
