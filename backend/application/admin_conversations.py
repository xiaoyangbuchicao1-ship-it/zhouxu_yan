"""Account-scoped, public-only conversation logs for administrators."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select

from backend.application.admin import AdminService, AdminServiceError
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import (
    AccountModel,
    ConversationModel,
    ConversationRunModel,
)


class AdminConversationService:
    """Read only the text and metadata already visible to the account owner."""

    def __init__(self, database: Database):
        self.database = database

    def list_conversations(
        self,
        *,
        account_id: str,
        query: str = "",
        mode: str | None = None,
        archived: bool | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict:
        if mode not in {None, "chat", "work"}:
            raise AdminServiceError("invalid_conversation_mode")
        if limit < 1 or limit > 100:
            raise AdminServiceError("invalid_conversation_limit")
        cursor_time, cursor_id = self._decode_cursor(cursor)
        term = query.strip()[:200]
        with self.database.session() as session:
            if session.get(AccountModel, account_id) is None:
                raise AdminServiceError("account_not_found")
            run_count = (
                select(
                    ConversationRunModel.conversation_id.label("conversation_id"),
                    func.count(ConversationRunModel.run_id).label("run_count"),
                )
                .group_by(ConversationRunModel.conversation_id)
                .subquery()
            )
            latest_status = (
                select(ConversationRunModel.status)
                .where(ConversationRunModel.conversation_id == ConversationModel.id)
                .order_by(
                    ConversationRunModel.created_at.desc(),
                    ConversationRunModel.run_id.desc(),
                )
                .limit(1)
                .scalar_subquery()
            )
            statement = (
                select(
                    ConversationModel,
                    func.coalesce(run_count.c.run_count, 0),
                    latest_status,
                )
                .outerjoin(
                    run_count,
                    run_count.c.conversation_id == ConversationModel.id,
                )
                .where(ConversationModel.account_id == account_id)
                .order_by(
                    ConversationModel.updated_at.desc(),
                    ConversationModel.id.desc(),
                )
                .limit(limit + 1)
            )
            if term:
                statement = statement.where(ConversationModel.title.ilike(f"%{term}%"))
            if mode:
                statement = statement.where(ConversationModel.mode == mode)
            if archived is not None:
                statement = statement.where(
                    ConversationModel.archived_at.is_not(None)
                    if archived
                    else ConversationModel.archived_at.is_(None)
                )
            if cursor_time is not None:
                statement = statement.where(
                    or_(
                        ConversationModel.updated_at < cursor_time,
                        and_(
                            ConversationModel.updated_at == cursor_time,
                            ConversationModel.id < cursor_id,
                        ),
                    )
                )
            rows = session.execute(statement).all()
            has_more = len(rows) > limit
            rows = rows[:limit]
            conversations = [
                self._summary(conversation, int(count), status)
                for conversation, count, status in rows
            ]
            next_cursor = None
            if has_more and rows:
                next_cursor = self._encode_cursor(rows[-1][0].updated_at, rows[-1][0].id)
            return {"conversations": conversations, "next_cursor": next_cursor}

    def conversation_detail(
        self,
        *,
        actor_id: str,
        account_id: str,
        conversation_id: str,
        cursor: str | None = None,
        limit: int = 50,
        request_id: str | None = None,
    ) -> dict:
        if limit < 1 or limit > 100:
            raise AdminServiceError("invalid_conversation_limit")
        cursor_time, cursor_id = self._decode_cursor(cursor)
        with self.database.session() as session:
            conversation = session.scalar(
                select(ConversationModel).where(
                    ConversationModel.id == conversation_id,
                    ConversationModel.account_id == account_id,
                )
            )
            if conversation is None:
                raise AdminServiceError("conversation_not_found")
            count = session.scalar(
                select(func.count(ConversationRunModel.run_id)).where(
                    ConversationRunModel.conversation_id == conversation_id
                )
            ) or 0
            # The page may be a later slice.  The summary must still describe
            # the conversation's actual last run, rather than the last row in
            # the current page.
            latest_status = session.scalar(
                select(ConversationRunModel.status)
                .where(ConversationRunModel.conversation_id == conversation_id)
                .order_by(
                    ConversationRunModel.created_at.desc(),
                    ConversationRunModel.run_id.desc(),
                )
                .limit(1)
            )
            statement = (
                select(ConversationRunModel)
                .where(ConversationRunModel.conversation_id == conversation_id)
                .order_by(
                    ConversationRunModel.created_at.asc(),
                    ConversationRunModel.run_id.asc(),
                )
                .limit(limit + 1)
            )
            if cursor_time is not None:
                statement = statement.where(
                    or_(
                        ConversationRunModel.created_at > cursor_time,
                        and_(
                            ConversationRunModel.created_at == cursor_time,
                            ConversationRunModel.run_id > cursor_id,
                        ),
                    )
                )
            rows = list(session.scalars(statement).all())
            has_more = len(rows) > limit
            rows = rows[:limit]
            runs = [self._run_log(row) for row in rows]
            next_cursor = None
            if has_more and rows:
                next_cursor = self._encode_cursor(rows[-1].created_at, rows[-1].run_id)
            AdminService._add_audit(
                session,
                actor_id=actor_id,
                target_account_id=account_id,
                action="conversation.log_viewed",
                resource_type="conversation",
                resource_id=conversation_id,
                details={
                    "cursor": cursor,
                    "limit": limit,
                    "result_count": len(runs),
                    "request_id": request_id,
                },
            )
            session.commit()
            return {
                "conversation": self._summary(conversation, int(count), latest_status),
                "runs": runs,
                "next_cursor": next_cursor,
            }

    @staticmethod
    def _summary(conversation, count: int, latest_status: str | None) -> dict:
        return {
            "id": conversation.id,
            "account_id": conversation.account_id,
            "title": conversation.title,
            "mode": conversation.mode or "chat",
            "archived": conversation.archived_at is not None,
            "run_count": count,
            "latest_status": latest_status,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
        }

    @classmethod
    def _run_log(cls, run: ConversationRunModel) -> dict:
        snapshot = cls._snapshot(run)
        nodes = cls._public_nodes(run.progress_json)
        finished = run.completed_at or run.last_event_at
        elapsed_ms = None
        if finished is not None:
            elapsed_ms = max(0, int((cls._utc(finished) - cls._utc(run.created_at)).total_seconds() * 1000))
        return {
            "run_id": run.run_id,
            "user_question": run.question,
            "public_answer": run.answer if run.answer is not None else run.partial_output,
            "status": run.status,
            "model": snapshot.get("model_id"),
            "provider": snapshot.get("provider"),
            "protocol": snapshot.get("protocol"),
            "thinking_mode": snapshot.get("thinking_mode"),
            "created_at": run.created_at,
            "first_event_at": run.first_event_at,
            "first_delta_at": run.first_delta_at,
            "completed_at": run.completed_at,
            "elapsed_ms": elapsed_ms,
            "error_code": run.error_code,
            "nodes": nodes,
            "sources": cls._public_sources(run.progress_json),
            "artifacts": cls._public_artifacts(run.progress_json),
        }

    @staticmethod
    def _snapshot(run: ConversationRunModel) -> dict:
        raw = getattr(run, "model_snapshot_json", None)
        if raw:
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                value = {}
            if isinstance(value, dict):
                return value
        try:
            payload = json.loads(run.payload_json or "{}")
        except json.JSONDecodeError:
            return {}
        snapshot = payload.get("model_snapshot") if isinstance(payload, dict) else None
        return snapshot if isinstance(snapshot, dict) else {}

    @staticmethod
    def _public_nodes(raw: str | None) -> list[dict]:
        try:
            progress = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return []
        nodes = progress.get("nodes") if isinstance(progress, dict) else None
        if not isinstance(nodes, list):
            return []
        result = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id, label = node.get("id"), node.get("label")
            if not isinstance(node_id, str) or not isinstance(label, str):
                continue
            item = {"id": node_id, "label": label}
            if isinstance(node.get("status"), str):
                item["status"] = node["status"]
            elapsed = node.get("elapsed_ms")
            if isinstance(elapsed, int) and elapsed >= 0:
                item["elapsed_ms"] = elapsed
            result.append(item)
        return result

    @staticmethod
    def _progress(raw: str | None) -> dict:
        try:
            progress = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        return progress if isinstance(progress, dict) else {}

    @classmethod
    def _public_sources(cls, raw: str | None) -> list[dict]:
        progress = cls._progress(raw)
        values = progress.get("source_refs")
        if not isinstance(values, list):
            return []
        allowed = {
            "source_id", "kind", "title", "locator", "section", "version_date",
            "version_label", "authors", "collection",
        }
        result = []
        for value in values:
            if not isinstance(value, dict) or not isinstance(value.get("source_id"), str):
                continue
            title = value.get("title")
            if not isinstance(title, str) or not title.strip():
                continue
            item = {key: value[key] for key in allowed if key in value and value[key] is not None}
            if item.get("kind") not in {"law", "case", "paper"}:
                item.pop("kind", None)
            authors = item.get("authors")
            if isinstance(authors, (list, tuple)):
                item["authors"] = "、".join(
                    str(author).strip() for author in authors if str(author).strip()
                )
            elif authors is not None and not isinstance(authors, str):
                item.pop("authors", None)
            result.append(item)
        return result

    @classmethod
    def _public_artifacts(cls, raw: str | None) -> list[dict]:
        progress = cls._progress(raw)
        values = progress.get("artifacts")
        if not isinstance(values, list):
            return []
        allowed = {
            "artifact_id", "name", "media_type", "size_bytes", "sha256", "version",
            "kind", "preview_artifact_id",
        }
        result = []
        for value in values:
            if not isinstance(value, dict):
                continue
            if not all(isinstance(value.get(key), str) and value[key] for key in ("artifact_id", "name", "media_type", "sha256")):
                continue
            if not isinstance(value.get("size_bytes"), int) or value["size_bytes"] < 0:
                continue
            item = {key: value[key] for key in allowed if key in value and value[key] is not None}
            if item.get("kind") not in {"document", "preview", "file"}:
                item.pop("kind", None)
            result.append(item)
        return result

    @staticmethod
    def _encode_cursor(value: datetime, row_id: str) -> str:
        payload = json.dumps(
            {"time": AdminConversationService._utc(value).isoformat(), "id": row_id},
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(value: str | None) -> tuple[datetime | None, str | None]:
        if value is None:
            return None, None
        try:
            padded = value + "=" * (-len(value) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            timestamp = datetime.fromisoformat(payload["time"])
            row_id = payload["id"]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise AdminServiceError("invalid_conversation_cursor") from None
        if not isinstance(row_id, str) or not row_id:
            raise AdminServiceError("invalid_conversation_cursor")
        return timestamp, row_id

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


__all__ = ["AdminConversationService"]
