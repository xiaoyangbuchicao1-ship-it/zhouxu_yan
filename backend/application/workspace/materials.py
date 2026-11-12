"""Account-scoped uploaded material registration and local storage."""

from __future__ import annotations

import hashlib
import mimetypes
import secrets
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_, select

from backend.config.settings import Settings
from backend.contracts.modules.materials import MaterialInput, StoredMaterialRef
from backend.infrastructure.database.connection import WORKSPACE_ROOT, Database
from backend.infrastructure.database.models import (
    ConversationMaterialModel,
    MaterialModel,
)


class MaterialServiceError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class MaterialService:
    """Small storage boundary used by the Harness HTTP adapter and executor."""

    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings

    def _storage_root(self) -> Path:
        root = Path(self.settings.materials.storage_root)
        if root.is_absolute():
            return root
        return (WORKSPACE_ROOT / root).resolve()

    def upload(
        self,
        *,
        account_id: str,
        session_id: str,
        filename: str,
        media_type: str | None,
        content: bytes,
    ) -> StoredMaterialRef:
        safe_name = Path(filename or "").name.strip()
        if not safe_name or safe_name in {".", ".."}:
            raise MaterialServiceError("material_filename_invalid")
        if len(content) > self.settings.materials.max_file_bytes:
            raise MaterialServiceError("material_too_large")
        material_id = secrets.token_hex(16)
        checksum = hashlib.sha256(content).hexdigest()
        guessed = mimetypes.guess_type(safe_name)[0]
        normalized_type = (media_type or guessed or "application/octet-stream").split(";", 1)[0].lower()
        storage_key = f"{account_id}/{material_id}/{safe_name}"
        path = self._storage_root() / account_id / material_id / safe_name
        path.parent.mkdir(parents=True, exist_ok=False)
        try:
            path.write_bytes(content)
            now = datetime.now(timezone.utc)
            session = self.database.session()
            try:
                session.add(
                    MaterialModel(
                        id=material_id,
                        account_id=account_id,
                        session_id=session_id,
                        filename=safe_name,
                        media_type=normalized_type,
                        size_bytes=len(content),
                        checksum=checksum,
                        storage_key=storage_key,
                        status="uploaded",
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.commit()
            finally:
                session.close()
        except Exception:
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
                path.parent.parent.rmdir()
            except OSError:
                pass
            raise
        return StoredMaterialRef(
            material_id=material_id,
            name=safe_name,
            media_type=normalized_type,
            size_bytes=len(content),
            status="uploaded",
            checksum=checksum,
        )

    def resolve(
        self, *, account_id: str, session_id: str, material_ids: list[str],
        conversation_id: str | None = None,
    ) -> list[MaterialInput]:
        if not material_ids:
            return []
        unique_ids = list(dict.fromkeys(material_ids))
        session = self.database.session()
        try:
            query = select(MaterialModel).where(
                MaterialModel.account_id == account_id,
                MaterialModel.id.in_(unique_ids),
            )
            if conversation_id is not None:
                query = query.join(
                    ConversationMaterialModel,
                    ConversationMaterialModel.material_id == MaterialModel.id,
                ).where(
                    ConversationMaterialModel.conversation_id == conversation_id
                )
            else:
                query = query.outerjoin(
                    ConversationMaterialModel,
                    ConversationMaterialModel.material_id == MaterialModel.id,
                ).where(
                    or_(
                        MaterialModel.session_id == session_id,
                        ConversationMaterialModel.conversation_id == session_id,
                    )
                )
            records = list(session.scalars(query))
        finally:
            session.close()
        by_id = {record.id: record for record in records}
        if len(by_id) != len(unique_ids):
            raise MaterialServiceError("material_unavailable")
        root = self._storage_root().resolve()
        result: list[MaterialInput] = []
        for material_id in unique_ids:
            record = by_id[material_id]
            path = (root / record.storage_key).resolve()
            if root not in path.parents or not path.is_file():
                raise MaterialServiceError("material_unavailable")
            result.append(
                MaterialInput(
                    uri=str(path),
                    material_id=record.id,
                    name=record.filename,
                    media_type=record.media_type,
                )
            )
        return result

    def set_status(self, *, account_id: str, material_id: str, status: str) -> None:
        if status not in {"uploaded", "parsing", "ready", "failed", "unsupported"}:
            raise MaterialServiceError("material_status_invalid")
        session = self.database.session()
        try:
            record = session.scalar(
                select(MaterialModel).where(
                    MaterialModel.id == material_id,
                    MaterialModel.account_id == account_id,
                )
            )
            if record is None:
                raise MaterialServiceError("material_unavailable")
            allowed_transitions = {
                "uploaded": {"parsing"},
                "parsing": {"ready", "failed", "unsupported"},
                "ready": {"parsing"},
                "failed": {"parsing"},
                "unsupported": {"parsing"},
            }
            if status != record.status and status not in allowed_transitions.get(record.status, set()):
                raise MaterialServiceError("material_status_transition_invalid")
            record.status = status
            record.updated_at = datetime.now(timezone.utc)
            session.commit()
        finally:
            session.close()

    def delete(self, *, account_id: str, session_id: str, material_id: str) -> None:
        session = self.database.session()
        try:
            record = session.scalar(
                select(MaterialModel).where(
                    MaterialModel.id == material_id,
                    MaterialModel.account_id == account_id,
                    MaterialModel.session_id == session_id,
                )
            )
            if record is None:
                raise MaterialServiceError("material_unavailable")
            path = (self._storage_root() / record.storage_key).resolve()
            root = self._storage_root().resolve()
            if root not in path.parents:
                raise MaterialServiceError("material_unavailable")
            session.delete(record)
            session.commit()
        finally:
            session.close()
        path.unlink(missing_ok=True)
        for parent in (path.parent, path.parent.parent):
            try:
                parent.rmdir()
            except OSError:
                break

    def delete_account_material(self, *, account_id: str, material_id: str) -> None:
        """Delete an account-owned material regardless of its conversation scope."""
        session = self.database.session()
        try:
            record = session.scalar(
                select(MaterialModel).where(
                    MaterialModel.id == material_id,
                    MaterialModel.account_id == account_id,
                )
            )
            if record is None:
                raise MaterialServiceError("material_unavailable")
            path = (self._storage_root() / record.storage_key).resolve()
            if self._storage_root().resolve() not in path.parents:
                raise MaterialServiceError("material_unavailable")
            session.delete(record)
            session.commit()
        finally:
            session.close()
        path.unlink(missing_ok=True)
        for parent in (path.parent, path.parent.parent):
            try:
                parent.rmdir()
            except OSError:
                break
