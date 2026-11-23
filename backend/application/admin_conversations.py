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
    MaterialModel,
)


STATUS_BUCKET_VALUES: dict[str, tuple[str, ...]] = {
    "succeeded": ("succeeded",),
    "active": ("queued", "running", "pausing", "paused", "waiting_user"),
    "failed": ("failed", "cancelled"),
}
RAW_STATUS_TO_BUCKET: dict[str, str] = {
    raw: bucket for bucket, raws in STATUS_BUCKET_VALUES.items() for raw in raws
}


def resolve_status_filter(
    status: str | None,
    *,
    allow_no_runs: bool,
    error_code: str,
) -> tuple[str | None, tuple[str, ...] | None]:
    """Normalize and validate coarse status bucket filters for conversations and tasks."""
    if status is None:
        return None, None
    normalized = status.strip().lower()
    if not normalized or normalized == "all":
        return None, None
    if allow_no_runs and normalized == "no_runs":
        return "no_runs", ()
    raw_values = STATUS_BUCKET_VALUES.get(normalized)
    if raw_values is None:
        raise AdminServiceError(error_code)
    return normalized, raw_values


class AdminConversationService:
    """Read only the text and metadata already visible to the account owner."""

    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _latest_run_field(column):
        return (
            select(column)
            .where(ConversationRunModel.conversation_id == ConversationModel.id)
            .order_by(
                ConversationRunModel.created_at.desc(),
                ConversationRunModel.run_id.desc(),
            )
            .limit(1)
            .scalar_subquery()
        )

    def list_conversations(
        self,
        *,
        account_id: str,
        query: str = "",
        mode: str | None = None,
        archived: bool | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict:
        if mode not in {None, "chat", "work"}:
            raise AdminServiceError("invalid_conversation_mode")
        status_bucket, status_values = resolve_status_filter(
            status,
            allow_no_runs=True,
            error_code="invalid_conversation_status",
        )
        if limit < 1 or limit > 100:
            raise AdminServiceError("invalid_conversation_limit")
        cursor_time, cursor_id = self._decode_cursor(cursor)
        term = query.strip()[:200]
        with self.database.session() as session:
            account = session.get(AccountModel, account_id)
            if account is None:
                raise AdminServiceError("account_not_found")
            account_summary = {
                "id": account.id,
                "email": account.email,
                "display_name": account.display_name,
            }
            run_count = (
                select(
                    ConversationRunModel.conversation_id.label("conversation_id"),
                    func.count(ConversationRunModel.run_id).label("run_count"),
                )
                .group_by(ConversationRunModel.conversation_id)
                .subquery()
            )
            latest_status = self._latest_run_field(ConversationRunModel.status)
            latest_run_at = self._latest_run_field(ConversationRunModel.created_at)
            activity_time = func.coalesce(latest_run_at, ConversationModel.created_at)

            base_conditions = [ConversationModel.account_id == account_id]
            if term:
                base_conditions.append(ConversationModel.title.ilike(f"%{term}%"))
            if mode:
                base_conditions.append(ConversationModel.mode == mode)
            if archived is not None:
                base_conditions.append(
                    ConversationModel.archived_at.is_not(None)
                    if archived
                    else ConversationModel.archived_at.is_(None)
                )

            status_counts = {"all": 0, "succeeded": 0, "active": 0, "failed": 0, "no_runs": 0}
            for raw_status in session.scalars(select(latest_status).where(*base_conditions)):
                status_counts["all"] += 1
                if raw_status is None:
                    status_counts["no_runs"] += 1
                else:
                    bucket = RAW_STATUS_TO_BUCKET.get(raw_status)
                    if bucket is not None:
                        status_counts[bucket] += 1

            statement = (
                select(
                    ConversationModel,
                    func.coalesce(run_count.c.run_count, 0),
                    latest_status,
                    latest_run_at,
                )
                .outerjoin(
                    run_count,
                    run_count.c.conversation_id == ConversationModel.id,
                )
                .where(*base_conditions)
                .order_by(
                    activity_time.desc(),
                    ConversationModel.id.desc(),
                )
                .limit(limit + 1)
            )
            if status_bucket == "no_runs":
                statement = statement.where(latest_status.is_(None))
            elif status_values:
                statement = statement.where(latest_status.in_(status_values))
            if cursor_time is not None:
                statement = statement.where(
                    or_(
                        activity_time < cursor_time,
                        and_(
                            activity_time == cursor_time,
                            ConversationModel.id < cursor_id,
                        ),
                    )
                )
            rows = session.execute(statement).all()
            has_more = len(rows) > limit
            rows = rows[:limit]
            conversations = [
                self._summary(conversation, int(count), latest, run_at)
                for conversation, count, latest, run_at in rows
            ]
            next_cursor = None
            if has_more and rows:
                last_conv, _, _, last_run_at = rows[-1]
                next_cursor = self._encode_cursor(
                    last_run_at or last_conv.created_at,
                    last_conv.id,
                )
            return {
                "account": account_summary,
                "conversations": conversations,
                "status_counts": status_counts,
                "next_cursor": next_cursor,
            }

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
            account = session.get(AccountModel, account_id)
            account_summary = (
                {
                    "id": account.id,
                    "email": account.email,
                    "display_name": account.display_name,
                }
                if account is not None
                else None
            )
            material_ids: set[str] = set()
            for row in rows:
                material_ids.update(self._material_ids(row.material_ids_json))
            material_map: dict[str, dict] = {}
            if material_ids:
                for mat in session.scalars(
                    select(MaterialModel).where(
                        MaterialModel.account_id == account_id,
                        MaterialModel.id.in_(list(material_ids)),
                    )
                ).all():
                    material_map[mat.id] = {
                        "material_id": mat.id,
                        "filename": mat.filename,
                        "media_type": mat.media_type,
                        "size_bytes": mat.size_bytes,
                        "status": mat.status,
                    }
            runs = [self._run_log(row, material_map=material_map) for row in rows]
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
                "account": account_summary,
                "conversation": self._summary(conversation, int(count), latest_status),
                "runs": runs,
                "next_cursor": next_cursor,
            }

    @staticmethod
    def _summary(
        conversation,
        count: int,
        latest_status: str | None,
        latest_run_at: datetime | None = None,
    ) -> dict:
        return {
            "id": conversation.id,
            "account_id": conversation.account_id,
            "title": conversation.title,
            "mode": conversation.mode or "chat",
            "archived": conversation.archived_at is not None,
            "run_count": count,
            "latest_status": latest_status,
            "latest_run_at": latest_run_at,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
        }

    @classmethod
    def _run_log(
        cls, run: ConversationRunModel, *, material_map: dict[str, dict] | None = None
    ) -> dict:
        snapshot = cls._snapshot(run)
        progress = cls._progress(run.progress_json)
        nodes = cls._public_nodes(run.progress_json)
        sources = cls._public_sources(run.progress_json)
        tool_traces = cls._public_tool_traces(progress)
        materials = [
            material_map[mid]
            for mid in cls._material_ids(run.material_ids_json)
            if material_map and mid in material_map
        ]
        source_summary: dict[str, int] = {"total": len(sources)}
        for src in sources:
            kind = src.get("kind") or "other"
            source_summary[kind] = source_summary.get(kind, 0) + 1
        finished = run.completed_at or run.last_event_at
        elapsed_ms = None
        if finished is not None:
            elapsed_ms = max(0, int((cls._utc(finished) - cls._utc(run.created_at)).total_seconds() * 1000))
        first_delta_ms = None
        if run.first_delta_at is not None:
            first_delta_ms = max(
                0, int((cls._utc(run.first_delta_at) - cls._utc(run.created_at)).total_seconds() * 1000)
            )
        raw_guidance = progress.get("continuation_guidance")
        guidance = (
            [str(item).strip() for item in raw_guidance if isinstance(item, str) and item.strip()]
            if isinstance(raw_guidance, list)
            else []
        )
        compaction = progress.get("context_compaction_count")
        compaction_count = int(compaction) if isinstance(compaction, int) and compaction >= 0 else 0
        current_unit = progress.get("current_unit")
        if not isinstance(current_unit, str) or not current_unit.strip():
            current_unit = None
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
            "first_delta_ms": first_delta_ms,
            "elapsed_ms": elapsed_ms,
            "error_code": run.error_code,
            "error_message": run.error.strip() if isinstance(run.error, str) and run.error.strip() else None,
            "current_unit": current_unit,
            "context_compaction_count": compaction_count,
            "continuation_guidance": guidance,
            "source_summary": source_summary,
            "nodes": nodes,
            "materials": materials,
            "tool_traces": tool_traces,
            "sources": sources,
            "artifacts": cls._public_artifacts(run.progress_json),
        }

    @staticmethod
    def _material_ids(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(loaded, list):
            return []
        return [item for item in loaded if isinstance(item, str) and item.strip()]

    @staticmethod
    def _public_tool_traces(progress: dict) -> list[dict]:
        raw = progress.get("tool_traces")
        if not isinstance(raw, list):
            return []
        result = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            tool_name = item.get("tool_name")
            tool_label = item.get("tool_label")
            if not isinstance(tool_name, str) or not isinstance(tool_label, str):
                continue
            status = item.get("status")
            clean = {
                "tool_name": tool_name,
                "tool_label": tool_label,
                "status": "failed" if status == "failed" else "succeeded",
            }
            if isinstance(item.get("query"), str) and item["query"].strip():
                clean["query"] = item["query"].strip()[:160]
            if isinstance(item.get("result_count"), int) and not isinstance(item.get("result_count"), bool) and item["result_count"] >= 0:
                clean["result_count"] = item["result_count"]
            if isinstance(item.get("summary"), str) and item["summary"].strip():
                clean["summary"] = item["summary"].strip()[:160]
            result.append(clean)
        return result

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
