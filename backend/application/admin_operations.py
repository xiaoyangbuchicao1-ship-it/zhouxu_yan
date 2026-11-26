"""Truthful read models for the combined business and operations console.

This service deliberately returns projections, never raw payloads.  It keeps
the administrator's business view (accounts and public task facts) separate
from runtime diagnostics (health and index state), while using the same
database and model directory as the user-facing application.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from backend.application.admin import AdminService, AdminServiceError
from backend.config.settings import Settings
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import (
    AccountCapabilityGrantModel,
    AccountModel,
    ConversationModel,
    ConversationRunModel,
    MaterialModel,
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

    def account_grant(self, account_id: str) -> dict:
        with self.database.session() as session:
            grant = session.get(AccountCapabilityGrantModel, account_id)
            if grant is None:
                return {
                    "model_policy": "default",
                    "allowed_models": [],
                    "skill_policy": "default",
                    "can_upload_skill": True,
                    "material_quota_mb": None,
                }
            allowed: list[str] = []
            if grant.allowed_models_json:
                try:
                    parsed = json.loads(grant.allowed_models_json)
                except json.JSONDecodeError as exc:
                    raise AdminServiceError("capability_policy_corrupt") from exc
                if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
                    raise AdminServiceError("capability_policy_corrupt")
                allowed = parsed
            return {
                "model_policy": grant.model_policy,
                "allowed_models": allowed,
                "skill_policy": grant.skill_policy,
                "can_upload_skill": bool(grant.can_upload_skill),
                "material_quota_mb": grant.material_quota_mb,
            }

    def is_model_permitted(
        self,
        *,
        account_id: str,
        account_role: str,
        profile_id: str,
        access_scope: str,
        grant: dict | None = None,
    ) -> bool:
        policy = grant if grant is not None else self.account_grant(account_id)
        model_policy = policy["model_policy"]
        allowed_models = set(policy["allowed_models"])
        if model_policy == "disabled":
            return False
        if model_policy == "allowlist" and profile_id not in allowed_models:
            return False
        if access_scope == "admin_only":
            return account_role == "admin"
        if access_scope == "allowlist":
            return account_role == "admin" or profile_id in allowed_models
        return True

    def filter_model_revisions(
        self,
        *,
        account_id: str,
        account_role: str,
        revisions: list,
    ) -> list:
        grant = self.account_grant(account_id)
        return [
            revision
            for revision in revisions
            if self.is_model_permitted(
                account_id=account_id,
                account_role=account_role,
                profile_id=revision.profile.profile_id,
                access_scope=getattr(revision, "access_scope", "all"),
                grant=grant,
            )
        ]

    def is_skill_permitted(
        self,
        *,
        account_id: str,
        skill_id: str,
        is_builtin: bool,
        grant: dict | None = None,
    ) -> bool:
        policy = grant if grant is not None else self.account_grant(account_id)
        skill_policy = policy["skill_policy"]
        if skill_policy == "disabled":
            return False
        if skill_policy == "builtin_only":
            return bool(is_builtin)
        return True

    def can_upload_skill(self, account_id: str) -> bool:
        return bool(self.account_grant(account_id)["can_upload_skill"])

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
            usage_rows = session.execute(
                select(
                    MaterialModel.account_id,
                    func.coalesce(func.sum(MaterialModel.size_bytes), 0),
                ).group_by(MaterialModel.account_id)
            ).all()
            usage_map = {str(aid): int(total or 0) for aid, total in usage_rows}
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
                        "material_quota_mb": grant.material_quota_mb if grant is not None else None,
                        "material_used_bytes": usage_map.get(account.id, 0),
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
        material_quota_mb: int | None = None,
        clear_material_quota: bool | None = None,
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
        if material_quota_mb is not None and (
            isinstance(material_quota_mb, bool)
            or not isinstance(material_quota_mb, int)
            or material_quota_mb < 0
            or material_quota_mb > 1_048_576
        ):
            raise AdminServiceError("material_quota_invalid")
        if (
            all(
                value is None
                for value in (model_policy, allowed_models, skill_policy, can_upload_skill, material_quota_mb)
            )
            and not clear_material_quota
        ):
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
                    material_quota_mb=None,
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
            if clear_material_quota:
                grant.material_quota_mb = None
            elif material_quota_mb is not None:
                grant.material_quota_mb = int(material_quota_mb)
            grant.updated_at = now
            used_bytes = int(
                session.scalar(
                    select(func.coalesce(func.sum(MaterialModel.size_bytes), 0)).where(
                        MaterialModel.account_id == account_id
                    )
                )
                or 0
            )
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
                    "material_quota_mb": grant.material_quota_mb,
                },
            )
            session.commit()
            return self._capability_view(account, grant, material_used_bytes=used_bytes)

    def list_skills(self) -> dict:
        from backend.application.maritime.catalog import READY_BUILTIN_METADATA

        skills = []
        for descriptor in default_skill_registry().descriptors():
            ready_meta = READY_BUILTIN_METADATA.get(descriptor.skill_id)
            if descriptor.path.is_file():
                skill_status = "ready"
            else:
                skill_status = "unavailable"
            skills.append(
                {
                    "id": descriptor.skill_id,
                    "name": ready_meta["display_name"] if ready_meta else descriptor.name,
                    "summary": (ready_meta.get("summary") if ready_meta else None) or descriptor.description,
                    "version": descriptor.version,
                    "visibility": "builtin",
                    "mode_scope": ready_meta.get("mode_scope") if ready_meta else descriptor.mode_scope,
                    "status": skill_status,
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

    def account_emails(self, account_ids) -> dict[str, str]:
        ids = list({item for item in account_ids if item})
        if not ids:
            return {}
        with self.database.session() as session:
            rows = session.execute(
                select(AccountModel.id, AccountModel.email).where(AccountModel.id.in_(ids))
            ).all()
        return {row[0]: row[1] for row in rows}

    def record_skill_removed(self, *, actor_id: str, owner_id: str, skill_id: str, name: str) -> None:
        with self.database.session() as session:
            actor = session.get(AccountModel, actor_id)
            if actor is None or actor.role != "admin" or actor.status != "active":
                raise AdminServiceError("admin_required")
            AdminService._add_audit(
                session,
                actor_id=actor_id,
                target_account_id=owner_id,
                action="skill.removed",
                resource_type="skill",
                resource_id=skill_id,
                details={"name": name},
            )
            session.commit()

    def attention_summary(self) -> dict:
        """What an administrator should look at: recent failures and stuck runs.

        Cancelled runs are the user's own choice and are not failures.  A run
        that is still queued or running an hour after it was created is
        reported as stuck rather than hidden in a long list.
        """

        now = datetime.now(timezone.utc)
        day_ago = now - timedelta(hours=24)
        week_ago = now - timedelta(days=7)
        stuck_before = now - timedelta(minutes=60)
        live = ("queued", "running", "pausing")
        with self.database.session() as session:
            def count(*conditions) -> int:
                return int(
                    session.scalar(
                        select(func.count(ConversationRunModel.run_id)).where(*conditions)
                    )
                    or 0
                )

            base = (
                select(ConversationRunModel, ConversationModel, AccountModel)
                .join(ConversationModel, ConversationModel.id == ConversationRunModel.conversation_id)
                .join(AccountModel, AccountModel.id == ConversationModel.account_id)
            )
            failed_rows = session.execute(
                base.where(
                    ConversationRunModel.status == "failed",
                    ConversationRunModel.created_at >= week_ago,
                )
                .order_by(ConversationRunModel.created_at.desc(), ConversationRunModel.run_id.desc())
                .limit(8)
            ).all()
            stuck_rows = session.execute(
                base.where(
                    ConversationRunModel.status.in_(live),
                    ConversationRunModel.created_at < stuck_before,
                )
                .order_by(ConversationRunModel.created_at.asc())
                .limit(8)
            ).all()
            return {
                "tasks_24h": count(ConversationRunModel.created_at >= day_ago),
                "failed_24h": count(
                    ConversationRunModel.status == "failed",
                    ConversationRunModel.created_at >= day_ago,
                ),
                "active_tasks": count(ConversationRunModel.status.in_(live)),
                "recent_failed": [self._task_view(*row) for row in failed_rows],
                "stuck_tasks": [self._task_view(*row) for row in stuck_rows],
            }

    def resource_health(self) -> dict:
        sources: dict[str, dict] = {}
        corpus = MaritimeCorpus(settings=self.settings)
        local_status = corpus.status()
        local_counts = local_status.get("counts", {}) if local_status.get("indexed") else {}
        remote_status = corpus.opensearch_status()
        remote_counts = remote_status.get("counts", {})
        remote_errors = remote_status.get("errors", {})

        for kind, label in (("laws", "法规"), ("cases", "案例")):
            remote_count = remote_counts.get(kind)
            local_count = local_counts.get(kind)
            if isinstance(remote_count, int) and remote_count > 0:
                reason = (
                    f"OpenSearch 在线索引与本地{label}精确索引均已就绪"
                    if isinstance(local_count, int) and local_count > 0
                    else f"OpenSearch 在线{label}索引已就绪"
                )
                sources[kind] = {"status": "ready", "count": remote_count, "reason": reason}
            elif isinstance(local_count, int) and local_count > 0:
                sources[kind] = {
                    "status": "ready",
                    "count": local_count,
                    "reason": f"本地{label}语料索引已就绪",
                }
            else:
                sources[kind] = {
                    "status": "unavailable",
                    "reason": f"在线 OpenSearch 与本地{label}索引均未就绪",
                }
        if self.settings.research_cards.enabled:
            remote_papers = remote_counts.get("papers")
            if isinstance(remote_papers, int) and remote_papers > 0:
                sources["papers"] = {
                    "status": "ready",
                    "count": remote_papers,
                    "reason": "OpenSearch 在线论文信息卡索引已就绪",
                }
            elif "papers" in remote_errors or "cluster" in remote_errors:
                sources["papers"] = {
                    "status": "unavailable",
                    "reason": "OpenSearch 在线论文信息卡索引未就绪",
                }
            else:
                skill_ready = Path(self.settings.research_cards.skill_dir).is_dir()
                sources["papers"] = {
                    "status": "ready" if skill_ready else "degraded",
                    "reason": "论文信息卡与写作规范已启用" if skill_ready else "论文索引由运行服务提供",
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
    def _capability_view(
        account: AccountModel,
        grant: AccountCapabilityGrantModel,
        *,
        material_used_bytes: int = 0,
    ) -> dict:
        allowed = json.loads(grant.allowed_models_json) if grant.allowed_models_json else []
        return {
            "account_id": account.id,
            "email": account.email,
            "display_name": account.display_name,
            "model_policy": grant.model_policy,
            "allowed_models": allowed,
            "skill_policy": grant.skill_policy,
            "can_upload_skill": bool(grant.can_upload_skill),
            "material_quota_mb": grant.material_quota_mb,
            "material_used_bytes": int(material_used_bytes),
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
            "error_code": run.error_code,
        }


__all__ = ["AdminOperationsService"]
