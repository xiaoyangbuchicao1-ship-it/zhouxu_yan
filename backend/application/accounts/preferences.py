"""Per-account persistent preferences, custom '/' commands, and workspace summary."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select

from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import (
    AccountModel,
    ConversationModel,
    MaterialModel,
    ProjectModel,
    UserPreferenceModel,
    UserPromptCommandModel,
)

TRIGGER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
VALID_MODES = frozenset({"chat", "work"})
VALID_THINKING = frozenset({"none", "low", "medium", "high", "xhigh", "max"})

DEFAULT_STARTER_COMMANDS: tuple[dict[str, str | None], ...] = (
    {
        "trigger": "law",
        "title": "核对现行法条与例外",
        "content": "请依据舟序现行法规库核对以下法律问题的适用条文、前提条件与例外情形，标注条文出处：",
        "mode_hint": "work",
    },
    {
        "trigger": "case",
        "title": "类案裁判观点对比",
        "content": "请检索相关海事裁判，归纳各方核心争议焦点与法院认定理由，并指出可能改变结论的关键事实差异：",
        "mode_hint": "work",
    },
    {
        "trigger": "review",
        "title": "条款风险逐条审阅",
        "content": "请站在我的委托人立场，逐条审查以下合同/提单条款的潜在责任风险、免责漏洞并给出修改建议：",
        "mode_hint": "work",
    },
    {
        "trigger": "paper",
        "title": "海事法学论文双链路成稿",
        "content": "请围绕以下选题撰写规范海事法学论文，明确论点结构与权威文献/法条出处，并生成 PDF 与 Word 交付件：",
        "mode_hint": "work",
    },
)


class AccountPreferenceError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class AccountPreferenceService:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _pref_view(row: UserPreferenceModel | None) -> dict:
        if row is None:
            return {
                "default_mode": "chat",
                "default_thinking_mode": "none",
                "concise_process": False,
                "compact_density": False,
                "reduced_motion": False,
                "completion_sound_enabled": True,
                "memory_enabled": False,
                "memory_text": "",
                "updated_at": None,
            }
        return {
            "default_mode": row.default_mode if row.default_mode in VALID_MODES else "chat",
            "default_thinking_mode": (
                row.default_thinking_mode
                if row.default_thinking_mode in VALID_THINKING
                else "none"
            ),
            "concise_process": bool(row.concise_process),
            "compact_density": bool(row.compact_density),
            "reduced_motion": bool(row.reduced_motion),
            "completion_sound_enabled": bool(
                getattr(row, "completion_sound_enabled", True)
                if getattr(row, "completion_sound_enabled", None) is not None
                else True
            ),
            "memory_enabled": bool(row.memory_enabled),
            "memory_text": row.memory_text or "",
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _cmd_view(row: UserPromptCommandModel) -> dict:
        return {
            "id": row.id,
            "trigger": row.trigger,
            "title": row.title,
            "content": row.content,
            "mode_hint": row.mode_hint if row.mode_hint in VALID_MODES else None,
            "is_builtin": bool(row.is_builtin),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def _ensure_pref_row(self, session, account_id: str) -> UserPreferenceModel:
        row = session.get(UserPreferenceModel, account_id)
        if row is None:
            row = UserPreferenceModel(
                account_id=account_id,
                default_mode="chat",
                default_thinking_mode="none",
                concise_process=False,
                compact_density=False,
                reduced_motion=False,
                completion_sound_enabled=True,
                memory_enabled=False,
                memory_text="",
                commands_seeded=False,
                updated_at=self._now(),
            )
            session.add(row)
            session.flush()
        return row

    def get_preferences(self, account_id: str) -> dict:
        with self.database.session() as session:
            return self._pref_view(session.get(UserPreferenceModel, account_id))

    def active_memory(self, account_id: str) -> str | None:
        with self.database.session() as session:
            row = session.get(UserPreferenceModel, account_id)
            if row is None or not row.memory_enabled:
                return None
            text = (row.memory_text or "").strip()
            return text[:1200] if text else None

    def update_preferences(self, account_id: str, updates: dict) -> dict:
        with self.database.session() as session:
            row = self._ensure_pref_row(session, account_id)
            if updates.get("default_mode") is not None:
                mode = updates["default_mode"]
                if mode not in VALID_MODES:
                    raise AccountPreferenceError("invalid_default_mode")
                row.default_mode = mode
            if updates.get("default_thinking_mode") is not None:
                thinking = updates["default_thinking_mode"]
                if thinking not in VALID_THINKING:
                    raise AccountPreferenceError("invalid_default_thinking_mode")
                row.default_thinking_mode = thinking
            for bool_field in (
                "concise_process",
                "compact_density",
                "reduced_motion",
                "completion_sound_enabled",
                "memory_enabled",
            ):
                if updates.get(bool_field) is not None:
                    setattr(row, bool_field, bool(updates[bool_field]))
            if updates.get("memory_text") is not None:
                text = str(updates["memory_text"]).strip()
                if len(text) > 1200:
                    raise AccountPreferenceError("memory_text_too_long")
                row.memory_text = text
            row.updated_at = self._now()
            session.commit()
            return self._pref_view(row)

    @staticmethod
    def _normalize_trigger(raw: str) -> str:
        cleaned = raw.strip().lstrip("/").lower()
        if not TRIGGER_PATTERN.fullmatch(cleaned):
            raise AccountPreferenceError("command_trigger_invalid")
        return cleaned

    def list_commands(self, account_id: str) -> list[dict]:
        with self.database.session() as session:
            pref = self._ensure_pref_row(session, account_id)
            if not pref.commands_seeded:
                existing_triggers = set(
                    session.scalars(
                        select(UserPromptCommandModel.trigger).where(
                            UserPromptCommandModel.account_id == account_id
                        )
                    )
                )
                now = self._now()
                for item in DEFAULT_STARTER_COMMANDS:
                    if item["trigger"] in existing_triggers:
                        continue
                    session.add(
                        UserPromptCommandModel(
                            id=secrets.token_hex(16),
                            account_id=account_id,
                            trigger=str(item["trigger"]),
                            title=str(item["title"]),
                            content=str(item["content"]),
                            mode_hint=item["mode_hint"],
                            is_builtin=True,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                pref.commands_seeded = True
                session.commit()
            rows = session.scalars(
                select(UserPromptCommandModel)
                .where(UserPromptCommandModel.account_id == account_id)
                .order_by(
                    UserPromptCommandModel.is_builtin.desc(),
                    UserPromptCommandModel.created_at.asc(),
                )
            ).all()
            return [self._cmd_view(row) for row in rows]

    def create_command(
        self,
        *,
        account_id: str,
        trigger: str,
        title: str,
        content: str,
        mode_hint: str | None = None,
    ) -> dict:
        normalized_trigger = self._normalize_trigger(trigger)
        normalized_title = title.strip()
        normalized_content = content.strip()
        if not normalized_title or len(normalized_title) > 80:
            raise AccountPreferenceError("command_title_invalid")
        if not normalized_content or len(normalized_content) > 4000:
            raise AccountPreferenceError("command_content_invalid")
        if mode_hint is not None and mode_hint not in VALID_MODES:
            raise AccountPreferenceError("command_mode_invalid")
        with self.database.session() as session:
            self._ensure_pref_row(session, account_id).commands_seeded = True
            duplicate = session.scalar(
                select(UserPromptCommandModel.id).where(
                    UserPromptCommandModel.account_id == account_id,
                    UserPromptCommandModel.trigger == normalized_trigger,
                )
            )
            if duplicate is not None:
                raise AccountPreferenceError("command_trigger_exists")
            now = self._now()
            row = UserPromptCommandModel(
                id=secrets.token_hex(16),
                account_id=account_id,
                trigger=normalized_trigger,
                title=normalized_title,
                content=normalized_content,
                mode_hint=mode_hint,
                is_builtin=False,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            return self._cmd_view(row)

    def update_command(self, *, account_id: str, command_id: str, updates: dict) -> dict:
        with self.database.session() as session:
            row = session.scalar(
                select(UserPromptCommandModel).where(
                    UserPromptCommandModel.id == command_id,
                    UserPromptCommandModel.account_id == account_id,
                )
            )
            if row is None:
                raise AccountPreferenceError("command_not_found")
            if updates.get("trigger") is not None:
                normalized_trigger = self._normalize_trigger(updates["trigger"])
                dup = session.scalar(
                    select(UserPromptCommandModel.id).where(
                        UserPromptCommandModel.account_id == account_id,
                        UserPromptCommandModel.trigger == normalized_trigger,
                        UserPromptCommandModel.id != command_id,
                    )
                )
                if dup is not None:
                    raise AccountPreferenceError("command_trigger_exists")
                row.trigger = normalized_trigger
            if updates.get("title") is not None:
                normalized_title = updates["title"].strip()
                if not normalized_title or len(normalized_title) > 80:
                    raise AccountPreferenceError("command_title_invalid")
                row.title = normalized_title
            if updates.get("content") is not None:
                normalized_content = updates["content"].strip()
                if not normalized_content or len(normalized_content) > 4000:
                    raise AccountPreferenceError("command_content_invalid")
                row.content = normalized_content
            if "mode_hint" in updates:
                mode_hint = updates["mode_hint"]
                if mode_hint is not None and mode_hint not in VALID_MODES:
                    raise AccountPreferenceError("command_mode_invalid")
                row.mode_hint = mode_hint
            row.updated_at = self._now()
            session.commit()
            return self._cmd_view(row)

    def delete_command(self, *, account_id: str, command_id: str) -> None:
        with self.database.session() as session:
            row = session.scalar(
                select(UserPromptCommandModel).where(
                    UserPromptCommandModel.id == command_id,
                    UserPromptCommandModel.account_id == account_id,
                )
            )
            if row is None:
                raise AccountPreferenceError("command_not_found")
            session.delete(row)
            session.commit()

    def account_summary(
        self,
        *,
        account_id: str,
        admin_ops=None,
        model_directory=None,
        maritime_office=None,
    ) -> dict:
        with self.database.session() as session:
            account = session.get(AccountModel, account_id)
            if account is None:
                raise AccountPreferenceError("account_not_found")
            project_count = int(
                session.scalar(
                    select(func.count(ProjectModel.id)).where(
                        ProjectModel.account_id == account_id
                    )
                )
                or 0
            )
            conversation_count = int(
                session.scalar(
                    select(func.count(ConversationModel.id)).where(
                        ConversationModel.account_id == account_id,
                        ConversationModel.archived_at.is_(None),
                    )
                )
                or 0
            )
            material_count = int(
                session.scalar(
                    select(func.count(MaterialModel.id)).where(
                        MaterialModel.account_id == account_id
                    )
                )
                or 0
            )
        grant = (
            admin_ops.account_grant(account_id)
            if admin_ops is not None
            else {
                "model_policy": "default",
                "allowed_models": [],
                "skill_policy": "default",
                "can_upload_skill": True,
            }
        )
        allowed_labels: list[str] = []
        if model_directory is not None:
            revisions = model_directory.list_current()
            if admin_ops is not None:
                revisions = admin_ops.filter_model_revisions(
                    account_id=account_id,
                    account_role=account.role,
                    revisions=revisions,
                )
            allowed_labels = [rev.profile.label for rev in revisions if rev.profile.available]

        custom_skill_count = 0
        if maritime_office is not None:
            custom_skill_count = sum(
                1
                for item in maritime_office.skills(account_id)
                if item.get("owner_id") == account_id
            )

        return {
            "account": {
                "id": account.id,
                "email": account.email,
                "display_name": account.display_name,
                "role": account.role,
            },
            "capabilities": {
                "model_policy": grant["model_policy"],
                "allowed_model_labels": allowed_labels,
                "skill_policy": grant["skill_policy"],
                "can_upload_skill": bool(grant["can_upload_skill"]),
            },
            "usage": {
                "project_count": project_count,
                "conversation_count": conversation_count,
                "material_count": material_count,
                "custom_skill_count": custom_skill_count,
            },
        }
