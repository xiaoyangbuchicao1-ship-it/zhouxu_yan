from __future__ import annotations

from collections import defaultdict

from backend.contracts.modules.memory import (
    MemoryRecord,
    MemoryWriteResult,
    TaskState,
    TaskWriteResult,
)


class InMemoryMemoryRepository:
    """Replaceable repository used until the business database is selected."""

    def __init__(self):
        self._records: dict[str, MemoryRecord] = {}
        self._session_index: dict[str, list[str]] = defaultdict(list)
        self._run_index: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._task_index: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._tasks: dict[tuple[str, str], TaskState] = {}
        self._kind_index: dict[tuple[str, str], list[str]] = defaultdict(list)
        self._exchange_index: dict[tuple[str, str | None, str], set[str]] = defaultdict(
            set
        )

    async def append(self, records: list[MemoryRecord]) -> MemoryWriteResult:
        accepted: list[str] = []
        duplicates: list[str] = []
        conflicts: list[str] = []
        staged: dict[str, MemoryRecord] = {}
        for incoming in records:
            if not incoming.session_id:
                raise ValueError("missing_session_id")
            current = staged.get(incoming.record_id) or self._records.get(
                incoming.record_id
            )
            if current is not None:
                if current == incoming:
                    duplicates.append(incoming.record_id)
                else:
                    conflicts.append(incoming.record_id)
                continue
            staged[incoming.record_id] = incoming
        if conflicts:
            return MemoryWriteResult(
                conflict_record_ids=list(dict.fromkeys(conflicts)),
                notices=["batch_not_written"],
            )
        for incoming in staged.values():
            stored = incoming.model_copy(deep=True)
            self._records[stored.record_id] = stored
            self._session_index[stored.session_id].append(stored.record_id)
            self._kind_index[(stored.session_id, stored.kind)].append(stored.record_id)
            for action_id in stored.exchange_ids:
                self._exchange_index[(stored.session_id, stored.run_id, action_id)].add(
                    stored.record_id
                )
            if stored.run_id:
                self._run_index[(stored.session_id, stored.run_id)].append(
                    stored.record_id
                )
            if stored.task_id:
                self._task_index[(stored.session_id, stored.task_id)].append(
                    stored.record_id
                )
            accepted.append(stored.record_id)
        return MemoryWriteResult(
            accepted_record_ids=accepted,
            duplicate_record_ids=duplicates,
            conflict_record_ids=conflicts,
        )

    async def list_records(
        self,
        *,
        session_id: str,
        run_id: str | None = None,
        task_id: str | None = None,
        limit: int | None = None,
        record_ids: list[str] | None = None,
        kinds: tuple[str, ...] | None = None,
        related_to: list[str] | None = None,
    ) -> list[MemoryRecord]:
        if related_to is not None:
            ids = set()
            pending = list(related_to)
            visited = set()
            while pending:
                record_id = pending.pop()
                if record_id in visited:
                    continue
                visited.add(record_id)
                record = self._records.get(record_id)
                if record is None or record.session_id != session_id:
                    continue
                for action_id in record.exchange_ids:
                    partners = self._exchange_index.get(
                        (session_id, record.run_id, action_id), ()
                    )
                    ids.update(partners)
                    pending.extend(i for i in partners if i not in visited)
        elif kinds is not None:
            ids = [
                item_id
                for kind in kinds
                for item_id in self._kind_index.get((session_id, kind), ())
            ]
        elif record_ids is not None:
            ids = {
                item_id
                for item_id in record_ids
                if item_id in self._records
                and self._records[item_id].session_id == session_id
            }
        elif run_id:
            ids = self._run_index.get((session_id, run_id), set())
        elif task_id:
            ids = self._task_index.get((session_id, task_id), set())
        else:
            ids = self._session_index.get(session_id, [])
        if limit is not None:
            ids = list(ids)[-limit:]
        records = sorted(
            (self._records[item_id] for item_id in ids),
            key=lambda item: (item.created_at, item.record_id),
        )
        return [record.model_copy(deep=True) for record in records]

    async def get_task(self, *, session_id: str, task_id: str) -> TaskState | None:
        state = self._tasks.get((session_id, task_id))
        if state is None:
            return None
        return state.model_copy(deep=True)

    async def save_task(
        self,
        state: TaskState,
        *,
        expected_revision: int | None,
    ) -> TaskWriteResult:
        if not state.session_id:
            raise ValueError("missing_session_id")
        key = (state.session_id, state.task_id)
        current = self._tasks.get(key)
        current_revision = current.revision if current else 0
        expected = state.revision if expected_revision is None else expected_revision
        if expected != current_revision:
            return TaskWriteResult(
                accepted=False,
                conflict=True,
                task_state=current.model_copy(deep=True) if current else None,
                notices=["task_revision_conflict"],
            )
        stored = state.model_copy(deep=True)
        stored.revision = current_revision + 1
        self._tasks[key] = stored
        return TaskWriteResult(accepted=True, task_state=stored.model_copy(deep=True))

    async def close_run(
        self, *, session_id: str, run_id: str, purge_session: bool = False
    ) -> int:
        run_ids = list(self._run_index.pop((session_id, run_id), []))
        if purge_session:
            ids = list(self._session_index.get(session_id, []))
        else:
            ids = [
                record_id
                for record_id in run_ids
                if self._records[record_id].retention == "run"
            ]
        for record_id in ids:
            record = self._records.pop(record_id, None)
            if record is None:
                continue
            for action_id in record.exchange_ids:
                key = (session_id, record.run_id, action_id)
                self._exchange_index[key].discard(record_id)
                if not self._exchange_index[key]:
                    del self._exchange_index[key]
        removed = set(ids)
        # Filter each affected index once, including other runs during a purge.
        self._session_index[session_id] = [
            i for i in self._session_index.get(session_id, []) if i not in removed
        ]
        if not self._session_index[session_id]:
            del self._session_index[session_id]
        for index in (self._run_index, self._task_index, self._kind_index):
            for key in [key for key in index if key[0] == session_id]:
                index[key] = [i for i in index[key] if i not in removed]
                if not index[key]:
                    del index[key]
        if purge_session:
            for key in [key for key in self._tasks if key[0] == session_id]:
                del self._tasks[key]
        return len(ids)

    async def count_session_records(self, *, session_id: str) -> int:
        return self.count_records(session_id=session_id)

    def count_records(
        self, *, session_id: str | None = None, run_id: str | None = None
    ) -> int:
        if session_id is None:
            return len(self._records)
        if run_id is None:
            return len(self._session_index.get(session_id, []))
        return len(self._run_index.get((session_id, run_id), []))
