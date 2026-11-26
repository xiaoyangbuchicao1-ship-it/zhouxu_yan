"""Runtime operational settings managed from the admin console (email, quotas, maintenance)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.application.admin import AdminService, AdminServiceError
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import AccountModel, SystemSettingModel
from backend.infrastructure.email.resend import EmailSendResult, send_resend_email
from backend.infrastructure.model.credentials import (
    credential_encryption_ready,
    decrypt_api_key,
    encrypt_api_key,
    mask_api_key,
)

OPS_SETTING_KEY = "ops_config"
_DEFAULTS = {
    "email_verification_enabled": False,
    "allow_password_reset": True,
    "resend_from_email": "",
    "resend_from_name": "舟序 Zhouxu",
    "resend_api_key_encrypted": None,
    "resend_api_key_hint": None,
    "material_quota_mb": 1024,
    "auto_maintenance_enabled": True,
    "backup_keep_days": 7,
    "captcha_enabled": True,
}


@dataclass(frozen=True)
class OpsConfig:
    email_verification_enabled: bool
    allow_password_reset: bool
    resend_from_email: str
    resend_from_name: str
    resend_api_key: str
    resend_api_key_hint: str | None
    resend_key_source: str  # "encrypted_db" | "env" | "none"
    material_quota_mb: int
    auto_maintenance_enabled: bool
    backup_keep_days: int
    captcha_enabled: bool
    updated_at: datetime | None = None

    @property
    def email_ready(self) -> bool:
        return bool(self.resend_api_key and self.resend_from_email)

    @property
    def material_quota_bytes(self) -> int:
        return max(0, int(self.material_quota_mb)) * 1024 * 1024


class OpsSettingsService:
    def __init__(self, database: Database):
        self.database = database

    def load(self) -> OpsConfig:
        raw: dict = {}
        updated_at = None
        with self.database.session() as session:
            row = session.get(SystemSettingModel, OPS_SETTING_KEY)
            if row is not None:
                updated_at = row.updated_at
                try:
                    parsed = json.loads(row.value_json or "{}")
                    if isinstance(parsed, dict):
                        raw = parsed
                except json.JSONDecodeError:
                    raw = {}

        env_key = os.environ.get("ZHOUXU_RESEND_API_KEY", "").strip()
        env_from = os.environ.get("ZHOUXU_RESEND_FROM_EMAIL", "").strip()
        env_name = os.environ.get("ZHOUXU_RESEND_FROM_NAME", "").strip()

        db_encrypted = raw.get("resend_api_key_encrypted")
        db_hint = raw.get("resend_api_key_hint")
        resolved_key = ""
        key_source = "none"
        hint: str | None = db_hint if isinstance(db_hint, str) and db_hint else None

        if isinstance(db_encrypted, str) and db_encrypted:
            try:
                resolved_key = decrypt_api_key(db_encrypted).get_secret_value()
                key_source = "encrypted_db"
            except ValueError:
                resolved_key = ""
        if not resolved_key and env_key:
            resolved_key = env_key
            key_source = "env"
            try:
                hint = mask_api_key(env_key)
            except ValueError:
                hint = None

        from_email = str(raw.get("resend_from_email") or env_from or "").strip()
        from_name = str(raw.get("resend_from_name") or env_name or _DEFAULTS["resend_from_name"]).strip()

        def _env_bool(name: str, default: bool) -> bool:
            val = os.environ.get(name, "").strip().lower()
            if val in {"1", "true", "yes", "on"}:
                return True
            if val in {"0", "false", "no", "off"}:
                return False
            return default

        def _env_int(name: str, default: int, minimum: int) -> int:
            val = os.environ.get(name, "").strip()
            if val:
                try:
                    parsed = int(val)
                    if parsed >= minimum:
                        return parsed
                except ValueError:
                    pass
            return default

        quota_mb = raw.get("material_quota_mb", _DEFAULTS["material_quota_mb"])
        if isinstance(quota_mb, bool) or not isinstance(quota_mb, int) or quota_mb < 0:
            quota_mb = int(_DEFAULTS["material_quota_mb"])
        quota_mb = _env_int("ZHOUXU_MATERIAL_QUOTA_MB", quota_mb, 0)

        keep_days = raw.get("backup_keep_days", _DEFAULTS["backup_keep_days"])
        if isinstance(keep_days, bool) or not isinstance(keep_days, int) or keep_days < 1:
            keep_days = int(_DEFAULTS["backup_keep_days"])
        keep_days = _env_int("ZHOUXU_BACKUP_KEEP_DAYS", keep_days, 1)

        return OpsConfig(
            email_verification_enabled=_env_bool(
                "ZHOUXU_EMAIL_VERIFICATION_ENABLED",
                bool(raw.get("email_verification_enabled", False)),
            ),
            allow_password_reset=_env_bool(
                "ZHOUXU_ALLOW_PASSWORD_RESET",
                bool(raw.get("allow_password_reset", True)),
            ),
            resend_from_email=from_email,
            resend_from_name=from_name or "舟序 Zhouxu",
            resend_api_key=resolved_key,
            resend_api_key_hint=hint,
            resend_key_source=key_source,
            material_quota_mb=quota_mb,
            auto_maintenance_enabled=_env_bool(
                "ZHOUXU_AUTO_MAINTENANCE_ENABLED",
                bool(raw.get("auto_maintenance_enabled", True)),
            ),
            backup_keep_days=keep_days,
            captcha_enabled=_env_bool(
                "ZHOUXU_CAPTCHA_ENABLED",
                bool(raw.get("captcha_enabled", True)),
            ),
            updated_at=updated_at,
        )

    def public_auth_config(self) -> dict:
        cfg = self.load()
        return {
            "email_verification_enabled": cfg.email_verification_enabled and cfg.email_ready,
            "allow_password_reset": cfg.allow_password_reset and cfg.email_ready,
            "captcha_enabled": cfg.captcha_enabled,
        }

    def admin_view(self, *, actor_id: str) -> dict:
        with self.database.session() as session:
            actor = session.get(AccountModel, actor_id)
            if actor is None or actor.role != "admin" or actor.status != "active":
                raise AdminServiceError("admin_required")
        cfg = self.load()
        return {
            "credential_encryption_ready": credential_encryption_ready(),
            "email_verification_enabled": cfg.email_verification_enabled,
            "allow_password_reset": cfg.allow_password_reset,
            "email_ready": cfg.email_ready,
            "resend_from_email": cfg.resend_from_email,
            "resend_from_name": cfg.resend_from_name,
            "resend_api_key_hint": cfg.resend_api_key_hint,
            "resend_key_configured": bool(cfg.resend_api_key),
            "resend_key_source": cfg.resend_key_source,
            "material_quota_mb": cfg.material_quota_mb,
            "auto_maintenance_enabled": cfg.auto_maintenance_enabled,
            "backup_keep_days": cfg.backup_keep_days,
            "updated_at": cfg.updated_at,
        }

    def update(self, *, actor_id: str, data: dict) -> dict:
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            actor = session.get(AccountModel, actor_id)
            if actor is None or actor.role != "admin" or actor.status != "active":
                raise AdminServiceError("admin_required")

            row = session.get(SystemSettingModel, OPS_SETTING_KEY)
            stored: dict = dict(_DEFAULTS)
            if row is not None:
                try:
                    parsed = json.loads(row.value_json or "{}")
                    if isinstance(parsed, dict):
                        stored.update(parsed)
                except json.JSONDecodeError:
                    pass

            if "resend_from_email" in data and data["resend_from_email"] is not None:
                from_email = str(data["resend_from_email"]).strip()
                if from_email and ("@" not in from_email or len(from_email) > 320):
                    raise AdminServiceError("email_invalid")
                stored["resend_from_email"] = from_email
            if "resend_from_name" in data and data["resend_from_name"] is not None:
                stored["resend_from_name"] = str(data["resend_from_name"]).strip()[:80] or "舟序 Zhouxu"

            if "resend_api_key" in data and isinstance(data["resend_api_key"], str) and data["resend_api_key"].strip():
                try:
                    enc, hint = encrypt_api_key(data["resend_api_key"].strip())
                except ValueError as exc:
                    raise AdminServiceError(str(exc)) from None
                stored["resend_api_key_encrypted"] = enc
                stored["resend_api_key_hint"] = hint

            if data.get("clear_resend_api_key"):
                stored["resend_api_key_encrypted"] = None
                stored["resend_api_key_hint"] = None

            if "allow_password_reset" in data and data["allow_password_reset"] is not None:
                stored["allow_password_reset"] = bool(data["allow_password_reset"])
            if "material_quota_mb" in data and data["material_quota_mb"] is not None:
                quota = data["material_quota_mb"]
                if isinstance(quota, bool) or not isinstance(quota, int) or quota < 0 or quota > 1024 * 1024:
                    raise AdminServiceError("material_quota_invalid")
                stored["material_quota_mb"] = quota
            if "auto_maintenance_enabled" in data and data["auto_maintenance_enabled"] is not None:
                stored["auto_maintenance_enabled"] = bool(data["auto_maintenance_enabled"])
            if "backup_keep_days" in data and data["backup_keep_days"] is not None:
                keep = data["backup_keep_days"]
                if isinstance(keep, bool) or not isinstance(keep, int) or not 1 <= keep <= 90:
                    raise AdminServiceError("backup_keep_days_invalid")
                stored["backup_keep_days"] = keep

            if "email_verification_enabled" in data and data["email_verification_enabled"] is not None:
                want_verify = bool(data["email_verification_enabled"])
                if want_verify:
                    has_key = bool(stored.get("resend_api_key_encrypted") or os.environ.get("ZHOUXU_RESEND_API_KEY", "").strip())
                    has_from = bool(str(stored.get("resend_from_email") or os.environ.get("ZHOUXU_RESEND_FROM_EMAIL", "")).strip())
                    if not has_key or not has_from:
                        raise AdminServiceError("email_provider_unconfigured")
                stored["email_verification_enabled"] = want_verify

            if row is None:
                row = SystemSettingModel(
                    key=OPS_SETTING_KEY,
                    value_json=json.dumps(stored, ensure_ascii=False, separators=(",", ":")),
                    updated_at=now,
                )
                session.add(row)
            else:
                row.value_json = json.dumps(stored, ensure_ascii=False, separators=(",", ":"))
                row.updated_at = now

            AdminService._add_audit(
                session,
                actor_id=actor_id,
                target_account_id=None,
                action="system.ops_settings_updated",
                resource_type="system",
                resource_id=OPS_SETTING_KEY,
                details={
                    "email_verification_enabled": stored["email_verification_enabled"],
                    "allow_password_reset": stored["allow_password_reset"],
                    "material_quota_mb": stored["material_quota_mb"],
                    "auto_maintenance_enabled": stored["auto_maintenance_enabled"],
                    "backup_keep_days": stored["backup_keep_days"],
                },
            )
            session.commit()
        return self.admin_view(actor_id=actor_id)

    async def send_test_email(self, *, actor_id: str, to_email: str) -> EmailSendResult:
        with self.database.session() as session:
            actor = session.get(AccountModel, actor_id)
            if actor is None or actor.role != "admin" or actor.status != "active":
                raise AdminServiceError("admin_required")
        target = (to_email or "").strip().lower()
        if not target or "@" not in target or len(target) > 320:
            raise AdminServiceError("email_invalid")
        cfg = self.load()
        result = await send_resend_email(
            api_key=cfg.resend_api_key,
            from_email=cfg.resend_from_email,
            from_name=cfg.resend_from_name,
            to_email=target,
            subject="舟序 Zhouxu · 发信测试邮件",
            html_body=(
                "<div style='font-family:sans-serif;padding:24px;'>"
                "<h3>舟序邮件配置测试成功</h3>"
                "<p>如果你收到这封邮件，说明 Resend 发信配置已可用，可以在管理台开启“注册邮箱验证码”。</p>"
                "</div>"
            ),
            text_body="舟序邮件配置测试成功。你可以开启注册邮箱验证码。",
        )
        with self.database.session() as session:
            AdminService._add_audit(
                session,
                actor_id=actor_id,
                target_account_id=None,
                action="system.email_tested",
                resource_type="system",
                resource_id=OPS_SETTING_KEY,
                outcome="succeeded" if result.ok else "failed",
                details={"to": target, "error_code": result.error_code},
            )
            session.commit()
        return result


__all__ = ["OpsConfig", "OpsSettingsService"]
