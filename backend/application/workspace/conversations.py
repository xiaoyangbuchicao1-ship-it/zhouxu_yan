"""Account-scoped conversation and material binding service.

The service deliberately stores only workspace metadata. Harness remains the
owner of live Run state; this layer supplies the account boundary and the
stable conversation id used as the scheduler session key.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from backend.contracts.modules.loop import ConversationMode
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import (
    ConversationMaterialModel,
    ConversationModel,
    ConversationRunModel,
    MaterialModel,
    ProjectModel,
)


class WorkspaceError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class WorkspaceService:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _title(title: str | None) -> str:
        value = (title or "").strip()
        return value[:200] if value else "新对话"

    def create(
        self, *, account_id: str, title: str | None = None,
        project_id: str | None = None,
        mode: ConversationMode = "chat",
    ) -> ConversationModel:
        now = self._now()
        conversation = ConversationModel(
            id=secrets.token_hex(16),
            account_id=account_id,
            project_id=project_id,
            title=self._title(title),
            mode=mode,
            pinned=False,
            created_at=now,
            updated_at=now,
        )
        session = self.database.session()
        try:
            if project_id is not None:
                project = session.scalar(
                    select(ProjectModel).where(
                        ProjectModel.id == project_id,
                        ProjectModel.account_id == account_id,
                    )
                )
                if project is None:
                    raise WorkspaceError("project_not_found")
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            return conversation
        finally:
            session.close()

    def get(self, *, account_id: str, conversation_id: str, include_archived: bool = False) -> ConversationModel:
        session = self.database.session()
        try:
            filters = [
                    ConversationModel.id == conversation_id,
                    ConversationModel.account_id == account_id,
                ]
            if not include_archived:
                filters.append(ConversationModel.archived_at.is_(None))
            conversation = session.scalar(select(ConversationModel).where(*filters))
            if conversation is None:
                raise WorkspaceError("conversation_not_found")
            # Detach a small immutable copy so callers cannot use a closed
            # session through lazy relationships.
            return ConversationModel(
                id=conversation.id,
                account_id=conversation.account_id,
                project_id=conversation.project_id,
                title=conversation.title,
                mode=getattr(conversation, "mode", "chat") or "chat",
                pinned=conversation.pinned,
                archived_at=conversation.archived_at,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
            )
        finally:
            session.close()

    def list(self, *, account_id: str) -> list[tuple[ConversationModel, int]]:
        session = self.database.session()
        try:
            rows = session.execute(
                select(
                    ConversationModel,
                    func.count(ConversationRunModel.run_id),
                )
                .outerjoin(
                    ConversationRunModel,
                    ConversationRunModel.conversation_id == ConversationModel.id,
                )
                .where(
                    ConversationModel.account_id == account_id,
                    ConversationModel.archived_at.is_(None),
                )
                .group_by(ConversationModel.id)
                .order_by(
                    ConversationModel.pinned.desc(),
                    ConversationModel.updated_at.desc(),
                )
            ).all()
            return [(conversation, int(run_count)) for conversation, run_count in rows]
        finally:
            session.close()

    def update(
        self,
        *,
        account_id: str,
        conversation_id: str,
        title: str | None = None,
        pinned: bool | None = None,
        mode: ConversationMode | None = None,
    ) -> ConversationModel:
        session = self.database.session()
        try:
            conversation = session.scalar(
                select(ConversationModel).where(
                    ConversationModel.id == conversation_id,
                    ConversationModel.account_id == account_id,
                    ConversationModel.archived_at.is_(None),
                )
            )
            if conversation is None:
                raise WorkspaceError("conversation_not_found")
            current_mode = getattr(conversation, "mode", "chat") or "chat"
            if mode is not None and mode != current_mode:
                raise WorkspaceError("conversation_mode_immutable")
            if title is not None:
                conversation.title = self._title(title)
            if pinned is not None:
                conversation.pinned = pinned
            conversation.updated_at = self._now()
            session.commit()
            return conversation
        finally:
            session.close()

    def list_projects(self, *, account_id: str) -> list[ProjectModel]:
        session = self.database.session()
        try:
            return list(
                session.scalars(
                    select(ProjectModel)
                    .options(selectinload(ProjectModel.conversations))
                    .where(ProjectModel.account_id == account_id)
                    .order_by(ProjectModel.updated_at.desc(), ProjectModel.created_at.asc())
                )
            )
        finally:
            session.close()

    def create_project(self, *, account_id: str, name: str) -> ProjectModel:
        value = name.strip()[:120]
        if not value:
            raise WorkspaceError("project_name_invalid")
        now = self._now()
        project = ProjectModel(
            id=secrets.token_hex(16), account_id=account_id, name=value,
            created_at=now, updated_at=now,
        )
        session = self.database.session()
        try:
            session.add(project)
            session.commit()
            session.refresh(project)
            # Materialize the relationship before returning a detached model;
            # API projection uses the count after this session is closed.
            project.conversations
            return project
        finally:
            session.close()

    def update_project(self, *, account_id: str, project_id: str, name: str) -> ProjectModel:
        value = name.strip()[:120]
        if not value:
            raise WorkspaceError("project_name_invalid")
        session = self.database.session()
        try:
            project = session.scalar(
                select(ProjectModel).where(
                    ProjectModel.id == project_id,
                    ProjectModel.account_id == account_id,
                )
            )
            if project is None:
                raise WorkspaceError("project_not_found")
            project.name = value
            project.updated_at = self._now()
            session.commit()
            project.conversations
            return project
        finally:
            session.close()

    def delete_project(self, *, account_id: str, project_id: str) -> None:
        session = self.database.session()
        try:
            project = session.scalar(
                select(ProjectModel).where(
                    ProjectModel.id == project_id,
                    ProjectModel.account_id == account_id,
                )
            )
            if project is None:
                raise WorkspaceError("project_not_found")
            now = self._now()
            for conversation in project.conversations:
                conversation.project_id = None
                conversation.updated_at = now
            session.delete(project)
            session.commit()
        finally:
            session.close()

    def move_project(
        self, *, account_id: str, conversation_id: str, project_id: str | None
    ) -> ConversationModel:
        session = self.database.session()
        try:
            conversation = session.scalar(
                select(ConversationModel).where(
                    ConversationModel.id == conversation_id,
                    ConversationModel.account_id == account_id,
                    ConversationModel.archived_at.is_(None),
                )
            )
            if conversation is None:
                raise WorkspaceError("conversation_not_found")
            if project_id is not None:
                project = session.scalar(
                    select(ProjectModel).where(
                        ProjectModel.id == project_id,
                        ProjectModel.account_id == account_id,
                    )
                )
                if project is None:
                    raise WorkspaceError("project_not_found")
            conversation.project_id = project_id
            conversation.updated_at = self._now()
            session.commit()
            return conversation
        finally:
            session.close()

    def archive(self, *, account_id: str, conversation_id: str) -> None:
        session = self.database.session()
        try:
            conversation = session.scalar(
                select(ConversationModel).where(
                    ConversationModel.id == conversation_id,
                    ConversationModel.account_id == account_id,
                    ConversationModel.archived_at.is_(None),
                )
            )
            if conversation is None:
                raise WorkspaceError("conversation_not_found")
            conversation.archived_at = self._now()
            conversation.updated_at = conversation.archived_at
            session.commit()
        finally:
            session.close()

    def delete(self, *, account_id: str, conversation_id: str) -> None:
        """Permanently delete an inactive Conversation and its durable children.

        Archiving is the reversible operation.  Permanent deletion is refused
        while a persisted Run is still active so a worker cannot lose its
        final state while it is being written back.
        """

        session = self.database.session()
        try:
            conversation = session.scalar(
                select(ConversationModel).where(
                    ConversationModel.id == conversation_id,
                    ConversationModel.account_id == account_id,
                )
            )
            if conversation is None:
                raise WorkspaceError("conversation_not_found")
            active_run = session.scalar(
                select(ConversationRunModel.run_id).where(
                    ConversationRunModel.conversation_id == conversation_id,
                    ConversationRunModel.status.in_({"queued", "running", "waiting_user"}),
                )
            )
            if active_run is not None:
                raise WorkspaceError("conversation_has_active_runs")
            session.delete(conversation)
            session.commit()
        finally:
            session.close()

    def restore(self, *, account_id: str, conversation_id: str) -> ConversationModel:
        session = self.database.session()
        try:
            conversation = session.scalar(
                select(ConversationModel).where(
                    ConversationModel.id == conversation_id,
                    ConversationModel.account_id == account_id,
                    ConversationModel.archived_at.is_not(None),
                )
            )
            if conversation is None:
                raise WorkspaceError("conversation_not_found")
            conversation.archived_at = None
            conversation.updated_at = self._now()
            session.commit()
            return conversation
        finally:
            session.close()

    def bind_material(
        self, *, account_id: str, conversation_id: str, material_id: str
    ) -> None:
        session = self.database.session()
        try:
            conversation = session.scalar(
                select(ConversationModel).where(
                    ConversationModel.id == conversation_id,
                    ConversationModel.account_id == account_id,
                    ConversationModel.archived_at.is_(None),
                )
            )
            material = session.scalar(
                select(MaterialModel).where(
                    MaterialModel.id == material_id,
                    MaterialModel.account_id == account_id,
                )
            )
            if conversation is None:
                raise WorkspaceError("conversation_not_found")
            if material is None:
                raise WorkspaceError("material_not_found")
            existing = session.scalar(
                select(ConversationMaterialModel).where(
                    ConversationMaterialModel.conversation_id == conversation_id,
                    ConversationMaterialModel.material_id == material_id,
                )
            )
            if existing is None:
                session.add(
                    ConversationMaterialModel(
                        conversation_id=conversation_id, material_id=material_id
                    )
                )
                conversation.updated_at = self._now()
                session.commit()
        finally:
            session.close()

    def unbind_material(
        self, *, account_id: str, conversation_id: str, material_id: str
    ) -> None:
        session = self.database.session()
        try:
            link = session.scalar(
                select(ConversationMaterialModel)
                .join(ConversationModel)
                .where(
                    ConversationMaterialModel.conversation_id == conversation_id,
                    ConversationMaterialModel.material_id == material_id,
                    ConversationModel.account_id == account_id,
                )
            )
            if link is None:
                raise WorkspaceError("material_not_found")
            session.delete(link)
            session.commit()
        finally:
            session.close()

    def list_materials(self, *, account_id: str) -> list[MaterialModel]:
        session = self.database.session()
        try:
            return list(
                session.scalars(
                    select(MaterialModel)
                    .where(MaterialModel.account_id == account_id)
                    .order_by(MaterialModel.created_at.desc())
                )
            )
        finally:
            session.close()

    def list_conversation_materials(
        self, *, account_id: str, conversation_id: str, include_archived: bool = False
    ) -> list[MaterialModel]:
        session = self.database.session()
        try:
            self._require_active(session, account_id, conversation_id, include_archived=include_archived)
            return list(
                session.scalars(
                    select(MaterialModel)
                    .join(
                        ConversationMaterialModel,
                        ConversationMaterialModel.material_id == MaterialModel.id,
                    )
                    .where(
                        ConversationMaterialModel.conversation_id == conversation_id,
                        MaterialModel.account_id == account_id,
                    )
                    .order_by(MaterialModel.created_at.asc())
                )
            )
        finally:
            session.close()

    def list_runs(
        self, *, account_id: str, conversation_id: str, include_archived: bool = False
    ) -> list[ConversationRunModel]:
        session = self.database.session()
        try:
            self._require_active(session, account_id, conversation_id, include_archived=include_archived)
            return list(
                session.scalars(
                    select(ConversationRunModel)
                    .where(ConversationRunModel.conversation_id == conversation_id)
                    .order_by(ConversationRunModel.created_at.asc())
                )
            )
        finally:
            session.close()

    def list_conversation_material_ids(
        self, *, account_id: str, conversation_id: str
    ) -> list[str]:
        session = self.database.session()
        try:
            self._require_active(session, account_id, conversation_id)
            return list(
                session.scalars(
                    select(ConversationMaterialModel.material_id)
                    .where(ConversationMaterialModel.conversation_id == conversation_id)
                    .order_by(ConversationMaterialModel.material_id.asc())
                )
            )
        finally:
            session.close()

    def context_records(self, *, account_id: str, conversation_id: str, exclude_run_id: str):
        """Authorized durable dialogue projection; never rehydrate tools as evidence."""
        from backend.contracts.modules.memory import MemoryRecord

        records = []
        for run in self.list_runs(account_id=account_id, conversation_id=conversation_id):
            if run.run_id == exclude_run_id or run.status in {"queued", "running", "waiting_user"}:
                continue
            try:
                payload = json.loads(run.payload_json or "{}")
            except json.JSONDecodeError:
                payload = {}
            extras = {key: payload[key] for key in ("facts", "constraints") if isinstance(payload, dict) and payload.get(key)}
            records.append(MemoryRecord(
                record_id=f"history:{run.run_id}:user", session_id=conversation_id,
                run_id=run.run_id, source="user", retention="session",
                body={"text": run.question, **extras}, created_at=run.created_at.replace(tzinfo=timezone.utc),
            ))
            if run.answer or run.partial_output:
                records.append(MemoryRecord(
                    record_id=f"history:{run.run_id}:assistant", session_id=conversation_id,
                    run_id=run.run_id, source="history", retention="session",
                    body={"text": run.answer or run.partial_output, "status": run.status,
                          "notice": "历史回答，不是本轮已核验的证据。"},
                    created_at=(run.completed_at or run.created_at).replace(tzinfo=timezone.utc),
                ))
        return records

    def record_run(
        self, *, account_id: str, conversation_id: str, run_id: str,
        question: str, status: str, answer: str | None = None,
        partial_output: str | None = None, error: str | None = None,
        error_code: str | None = None,
        revision: int = 0, client_request_id: str | None = None,
        retry_of_run_id: str | None = None, payload: dict | None = None,
        material_ids: list[str] | None = None,
        last_sequence: int = 0,
        first_event_at: datetime | None = None,
        first_delta_at: datetime | None = None,
        last_event_at: datetime | None = None,
        progress: dict | None = None,
        created_at: datetime | None = None,
        completed_at: datetime | None = None,
        include_archived: bool = False,
    ) -> None:
        session = self.database.session()
        try:
            conversation = self._require_active(
                session, account_id, conversation_id, include_archived=include_archived
            )
            record = session.scalar(
                select(ConversationRunModel).where(
                    ConversationRunModel.run_id == run_id,
                    ConversationRunModel.conversation_id == conversation_id,
                )
            )
            if record is None and client_request_id:
                record = session.scalar(
                    select(ConversationRunModel).where(
                        ConversationRunModel.conversation_id == conversation_id,
                        ConversationRunModel.client_request_id == client_request_id,
                    )
                )
            now = self._now()
            if record is None:
                record = ConversationRunModel(
                    run_id=run_id,
                    conversation_id=conversation.id,
                    question=question,
                    answer=answer,
                    partial_output=partial_output,
                    status=status,
                    revision=revision,
                    client_request_id=client_request_id,
                    retry_of_run_id=retry_of_run_id,
                    payload_json=json.dumps(payload or {}, ensure_ascii=False),
                    material_ids_json=json.dumps(material_ids or [], ensure_ascii=False),
                    last_sequence=last_sequence,
                    first_event_at=first_event_at,
                    first_delta_at=first_delta_at,
                    last_event_at=last_event_at,
                    progress_json=json.dumps(progress or {}, ensure_ascii=False),
                    error=error,
                    error_code=error_code,
                    created_at=created_at or now,
                    completed_at=completed_at or (now if status in {"completed", "failed", "stopped", "interrupted"} else None),
                )
                session.add(record)
            else:
                record.status = status
                if answer is not None:
                    record.answer = answer
                if partial_output is not None:
                    record.partial_output = partial_output
                elif status == "completed":
                    # A successful retry supersedes an earlier partial result.
                    # Do not leave stale text visible after the final answer.
                    record.partial_output = None
                record.error = error
                # A continuation enters the active queue with no error.  Do
                # not leave the previous terminal diagnostic attached to the
                # next successful or running projection.
                record.error_code = error_code
                record.revision = max(record.revision or 0, revision)
                if payload is not None:
                    record.payload_json = json.dumps(payload, ensure_ascii=False)
                if record.material_ids_json is None and material_ids is not None:
                    record.material_ids_json = json.dumps(material_ids, ensure_ascii=False)
                record.last_sequence = max(record.last_sequence or 0, last_sequence)
                record.first_event_at = record.first_event_at or first_event_at
                record.first_delta_at = record.first_delta_at or first_delta_at
                record.last_event_at = last_event_at or record.last_event_at
                if progress is not None:
                    record.progress_json = json.dumps(progress, ensure_ascii=False)
                if record.client_request_id is None:
                    record.client_request_id = client_request_id
                if record.retry_of_run_id is None:
                    record.retry_of_run_id = retry_of_run_id
                if status in {"completed", "failed", "stopped", "interrupted"}:
                    record.completed_at = record.completed_at or completed_at or now
            conversation.updated_at = now
            try:
                session.commit()
            except IntegrityError:
                # A concurrent writer may have won the unique
                # (conversation_id, client_request_id) race after the lookup
                # above. Treat that commit as the same durable Run.
                session.rollback()
                if client_request_id:
                    existing = session.scalar(
                        select(ConversationRunModel).where(
                            ConversationRunModel.conversation_id == conversation_id,
                            ConversationRunModel.client_request_id == client_request_id,
                        )
                    )
                    if existing is not None:
                        return
                raise
        finally:
            session.close()

    @staticmethod
    def _require_active(session, account_id, conversation_id, *, include_archived: bool = False) -> ConversationModel:
        filters = [
                ConversationModel.id == conversation_id,
                ConversationModel.account_id == account_id,
            ]
        if not include_archived:
            filters.append(ConversationModel.archived_at.is_(None))
        conversation = session.scalar(select(ConversationModel).where(*filters))
        if conversation is None:
            raise WorkspaceError("conversation_not_found")
        return conversation


__all__ = ["WorkspaceError", "WorkspaceService"]
