"""Truthful read models for the combined business and operations console.

This service deliberately returns projections, never raw payloads.  It keeps
the administrator's business view (accounts and public task facts) separate
from runtime diagnostics (health and index state), while using the same
database and model directory as the user-facing application.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from backend.application.admin import AdminService, AdminServiceError
from backend.config.settings import Settings
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import (
    AccountCapabilityGrantModel,
    AccountModel,
    ConversationModel,
    ConversationRunModel,
)
from backend.infrastructure.search.maritime_corpus import MaritimeCorpus
from backend.skills.registry import default_skill_registry


_MODEL_POLICIES = {"default", "allowlist", "disabled"}
_SKILL_POLICIES = {"default", "builtin_only", "disabled"}


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class AdminOperationsService:
    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings

    def list_capabilities(self) -> list[dict]:
        with self.database.session() as session:
            rows = session.execute(
                select(AccountModel, AccountCapabilityGrantModel)
                .outerjoin(
                    AccountCapabilityGrantModel,
                    AccountCapabilityGrantModel.account_id == AccountModel.id,
                )
                .order_by(AccountModel.created_at.desc(), AccountModel.id)
            ).all()
            result = []
            for account, grant in rows:
                allowed = []
                if grant is not None and grant.allowed_models_json:
                    try:
                        parsed = json.loads(grant.allowed_models_json)
                    except json.JSONDecodeError as exc:
                        raise AdminServiceError("capability_policy_corrupt") from exc
                    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
                        raise AdminServiceError("capability_policy_corrupt")
                    allowed = parsed
                result.append(
                    {
                        "account_id": account.id,
                        "email": account.email,
                        "display_name": account.display_name,
                        "model_policy": grant.model_policy if grant is not None else "default",
                        "allowed_models": allowed,
                        "skill_policy": grant.skill_policy if grant is not None else "default",
                        "can_upload_skill": bool(grant.can_upload_skill) if grant is not None else True,
                        "updated_at": grant.updated_at if grant is not None else None,
                    }
                )
            return result

    def update_capability(
        self,
        *,
        actor_id: str,
        account_id: str,
        model_policy: str | None = None,
        allowed_models: list[str] | None = None,
        skill_policy: str | None = None,
        can_upload_skill: bool | None = None,
    ) -> dict:
        if model_policy is not None and model_policy not in _MODEL_POLICIES:
            raise AdminServiceError("capability_model_policy_invalid")
        if skill_policy is not None and skill_policy not in _SKILL_POLICIES:
            raise AdminServiceError("capability_skill_policy_invalid")
        if allowed_models is not None:
            if any(not isinstance(item, str) or not item.strip() for item in allowed_models):
                raise AdminServiceError("capability_model_list_invalid")
            allowed_models = list(dict.fromkeys(item.strip() for item in allowed_models))
        if model_policy == "allowlist" and allowed_models is not None and not allowed_models:
            raise AdminServiceError("capability_model_list_required")
        if all(value is None for value in (model_policy, allowed_models, skill_policy, can_upload_skill)):
            raise AdminServiceError("capability_change_required")

        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            actor = session.get(AccountModel, actor_id)
            if actor is None or actor.role != "admin" or actor.status != "active":
                raise AdminServiceError("admin_required")
            account = session.get(AccountModel, account_id)
            if account is None:
                raise AdminServiceError("account_not_found")
            grant = session.get(AccountCapabilityGrantModel, account_id)
            if grant is None:
                grant = AccountCapabilityGrantModel(
                    account_id=account_id,
                    model_policy="default",
                    allowed_models_json=None,
                    skill_policy="default",
                    can_upload_skill=True,
                    updated_at=now,
                )
                session.add(grant)
                session.flush()
            if model_policy is not None:
                grant.model_policy = model_policy
            if allowed_models is not None:
                grant.allowed_models_json = json.dumps(allowed_models, ensure_ascii=False, separators=(",", ":"))
            if skill_policy is not None:
                grant.skill_policy = skill_policy
            if can_upload_skill is not None:
                grant.can_upload_skill = can_upload_skill
            grant.updated_at = now
            AdminService._add_audit(
                session,
                actor_id=actor_id,
                target_account_id=account_id,
                action="account.capability_changed",
                resource_type="account_capability",
                resource_id=account_id,
                details={
                    "model_policy": grant.model_policy,
                    "allowed_models": json.loads(grant.allowed_models_json) if grant.allowed_models_json else [],
                    "skill_policy": grant.skill_policy,
                    "can_upload_skill": bool(grant.can_upload_skill),
                },
            )
            session.commit()
            return self._capability_view(account, grant)

    def list_skills(self) -> dict:
        skills = []
        for descriptor in default_skill_registry().descriptors():
            skills.append(
                {
                    "id": descriptor.skill_id,
                    "name": descriptor.name,
                    "summary": descriptor.description,
                    "version": descriptor.version,
                    "visibility": "builtin",
                    "status": "ready" if descriptor.path.is_file() else "unavailable",
                    "owner_email": None,
                    "updated_at": datetime.fromtimestamp(
                        descriptor.path.stat().st_mtime, tz=timezone.utc
                    )
                    if descriptor.path.is_file()
                    else None,
                }
            )
        status = "ready" if skills and all(item["status"] == "ready" for item in skills) else "degraded"
        return {"status": status, "total": len(skills), "skills": skills}

    def list_tasks(self, *, limit: int = 50, cursor: str | None = None) -> dict:
        if limit < 1 or limit > 100:
            raise AdminServiceError("invalid_task_limit")
        # The cursor is intentionally a simple opaque run id.  The first
        # implementation has stable ordering and a bounded page; a malformed
        # cursor is an input error rather than a silent restart at page one.
        if cursor is not None and not cursor.strip():
            raise AdminServiceError("invalid_task_cursor")
        with self.database.session() as session:
            statement = (
                select(ConversationRunModel, ConversationModel, AccountModel)
                .join(ConversationModel, ConversationModel.id == ConversationRunModel.conversation_id)
                .join(AccountModel, AccountModel.id == ConversationModel.account_id)
                .order_by(ConversationRunModel.created_at.desc(), ConversationRunModel.run_id.desc())
                .limit(limit + 1)
            )
            if cursor:
                current = session.get(ConversationRunModel, cursor)
                if current is None:
                    raise AdminServiceError("invalid_task_cursor")
                statement = statement.where(
                    (ConversationRunModel.created_at < current.created_at)
                    | (
                        (ConversationRunModel.created_at == current.created_at)
                        & (ConversationRunModel.run_id < current.run_id)
                    )
                )
            rows = session.execute(statement).all()
            has_more = len(rows) > limit
            rows = rows[:limit]
            tasks = [self._task_view(run, conversation, account) for run, conversation, account in rows]
            return {
                "tasks": tasks,
                "next_cursor": tasks[-1]["run_id"] if has_more and tasks else None,
            }

    def resource_health(self) -> dict:
        sources: dict[str, dict] = {}
        corpus = MaritimeCorpus()
        status = corpus.status()
        if status.get("indexed"):
            for kind, count in status.get("counts", {}).items():
                sources[str(kind)] = {"status": "ready", "count": int(count)}
        else:
            sources["laws"] = {"status": "unavailable", "reason": "本地法规索引未就绪"}
            sources["cases"] = {"status": "unavailable", "reason": "本地案例索引未就绪"}
        if self.settings.research_cards.enabled:
            sources["papers"] = {
                "status": "ready" if Path(self.settings.research_cards.skill_dir).is_dir() else "degraded",
                "reason": "论文 Skill 已注册" if Path(self.settings.research_cards.skill_dir).is_dir() else "论文索引由运行服务提供",
            }
        else:
            sources["papers"] = {"status": "not_configured", "reason": "论文资料服务未启用"}
        states = {item["status"] for item in sources.values()}
        overall = "ready" if states == {"ready"} else "degraded" if "ready" in states else "unavailable"
        return {"status": overall, "sources": sources}

    def runtime_health(
        self,
        *,
        runtime_ready: bool | None,
        event_hub_present: bool,
        model_directory_present: bool,
    ) -> dict:
        components: dict[str, dict] = {}
        try:
            with self.database.engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1").scalar_one()
            components["database"] = {"status": "ready"}
        except (SQLAlchemyError, OSError) as exc:  # database boundary: expose state, never fake healthy
            components["database"] = {"status": "unavailable", "reason": type(exc).__name__}
        components["runtime"] = {
            "status": "ready" if runtime_ready is True else "degraded" if runtime_ready is None else "unavailable",
            "reason": "运行链路已启动" if runtime_ready is True else "运行链路尚未就绪" if runtime_ready is None else "模型运行链路不可用",
        }
        components["events"] = {
            "status": "ready" if event_hub_present else "unavailable",
            "reason": "事件流已接通" if event_hub_present else "事件流未接通",
        }
        components["model_directory"] = {
            "status": "ready" if model_directory_present else "unavailable",
            "reason": "模型目录已接通" if model_directory_present else "模型目录未接通",
        }
        states = {item["status"] for item in components.values()}
        overall = "ready" if states == {"ready"} else "degraded" if "ready" in states else "unavailable"
        return {"status": overall, "components": components}

    @staticmethod
    def _capability_view(account: AccountModel, grant: AccountCapabilityGrantModel) -> dict:
        allowed = json.loads(grant.allowed_models_json) if grant.allowed_models_json else []
        return {
            "account_id": account.id,
            "email": account.email,
            "display_name": account.display_name,
            "model_policy": grant.model_policy,
            "allowed_models": allowed,
            "skill_policy": grant.skill_policy,
            "can_upload_skill": bool(grant.can_upload_skill),
            "updated_at": grant.updated_at,
        }

    @staticmethod
    def _task_view(run, conversation, account) -> dict:
        finished = _utc(run.completed_at or run.last_event_at)
        created = _utc(run.created_at)
        elapsed_ms = None
        if finished is not None and created is not None:
            elapsed_ms = max(0, int((finished - created).total_seconds() * 1000))
        snapshot = {}
        raw = getattr(run, "model_snapshot_json", None)
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                snapshot = parsed
        text = run.answer if run.answer is not None else run.partial_output or ""
        return {
            "run_id": run.run_id,
            "account_id": account.id,
            "account_email": account.email,
            "conversation_id": conversation.id,
            "question": run.question,
            "status": run.status,
            "model": snapshot.get("model_id"),
            "provider": snapshot.get("provider"),
            "created_at": run.created_at,
            "completed_at": run.completed_at,
            "elapsed_ms": elapsed_ms,
            "answer_chars": len(text),
        }


__all__ = ["AdminOperationsService"]
