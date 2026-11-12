from backend.contracts.events.runtime import RunEvent
from backend.contracts.events.streaming import StreamEvent


class MemoryEventSink:
    """One bounded run's metadata; no prompts, key, headers or tool result bodies."""

    def __init__(self):
        self.events: list[RunEvent] = []
        self.stream_events: list[StreamEvent] = []
        self._stream_sequences: dict[str, int] = {}

    def emit(self, event: RunEvent) -> None:
        self.events.append(event.model_copy(deep=True))

    def emit_stream(self, event: StreamEvent) -> None:
        sequence = self._stream_sequences.get(event.run_id, 0) + 1
        self._stream_sequences[event.run_id] = sequence
        self.stream_events.append(event.model_copy(update={"sequence": sequence}, deep=True))

    def next_stream_sequence(self, run_id: str) -> int:
        return self._stream_sequences.get(run_id, 0) + 1

    def stream_events_since(
        self, *, run_id: str | None = None, after_sequence: int = 0
    ) -> list[StreamEvent]:
        """Return a stable copy of visible events after a per-run cursor."""

        return [
            event.model_copy(deep=True)
            for event in self.stream_events
            if (run_id is None or event.run_id == run_id)
            and event.sequence > after_sequence
        ]
