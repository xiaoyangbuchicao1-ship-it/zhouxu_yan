"""Small account application service backed by the relational adapter.

The service owns account rules and token lifecycle.  The API layer only maps
these outcomes to HTTP; it never hashes passwords or queries account tables.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import (
    AccountModel,
    EmailVerificationCodeModel,
    SessionModel,
)

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
ACCOUNT_ROLES = frozenset({"user", "admin"})


class AccountError(Exception):
    """An expected account outcome represented by a stable error code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AccountPrincipal:
    id: str
    email: str
    display_name: str
    role: str = "user"


@dataclass(frozen=True)
class AuthResult:
    account: AccountPrincipal
    token: str


class AccountService:
    def __init__(
        self,
        database: Database,
        database_settings: DatabaseSettings,
        auth_settings: AuthSettings,
    ):
        self.database = database
        self.database_settings = database_settings
        self.auth_settings = auth_settings
        self.passwords = PasswordHasher()

    def register(
        self,
        *,
        email: str,
        password: str,
        display_name: str = "",
        verification_code: str = "",
        require_verification: bool = False,
        register_ip: str | None = None,
    ) -> AuthResult:
        normalized_email = self._normalize_email(email)
        self._validate_password(password)
        normalized_name = display_name.strip()
        if len(normalized_name) > 80:
            raise AccountError("display_name_too_long")
        now = self._now()
        session = self.database.session()
        try:
            existing = session.scalar(
                select(AccountModel.id).where(AccountModel.email == normalized_email).limit(1)
            )
            if existing is not None:
                raise AccountError("account_email_exists")
            if require_verification:
                self._consume_verification_code(
                    session,
                    email=normalized_email,
                    purpose="register",
                    code=verification_code,
                    now=now,
                )
            account = AccountModel(
                id=secrets.token_hex(16),
                email=normalized_email,
                display_name=normalized_name,
                password_hash=self.passwords.hash(password),
                status="active",
                role="user",
                register_ip=register_ip or None,
                created_at=now,
                updated_at=now,
            )
            session.add(account)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                raise AccountError("account_email_exists") from None
            result = self._create_session(session, account, now)
            self._audit(
                session,
                account.id,
                "auth.registered",
                details={
                    "email_verified": require_verification,
                    "register_ip": register_ip or None,
                },
            )
            session.commit()
            return result
        finally:
            session.close()

    def issue_verification_code(
        self,
        *,
        email: str,
        purpose: str,
        request_ip: str = "",
    ) -> tuple[str, str]:
        """Validate rate limits and store a short-lived 6-digit code hash.

        Returns ``(code_id, plaintext_6digit_code)`` so the caller can deliver
        it over Resend and cancel it if upstream delivery fails.
        """
        if purpose not in {"register", "reset_password"}:
            raise AccountError("verification_purpose_invalid")
        normalized_email = self._normalize_email(email)
        now = self._now()
        ip_key = (request_ip or "").strip()[:64]
        session = self.database.session()
        try:
            existing_account = session.scalar(
                select(AccountModel).where(AccountModel.email == normalized_email)
            )
            if purpose == "register" and existing_account is not None:
                raise AccountError("account_email_exists")
            if purpose == "reset_password":
                if existing_account is None:
                    raise AccountError("email_not_registered")
                if existing_account.status != "active":
                    raise AccountError("account_disabled")

            latest = session.scalar(
                select(EmailVerificationCodeModel)
                .where(
                    EmailVerificationCodeModel.email == normalized_email,
                    EmailVerificationCodeModel.purpose == purpose,
                )
                .order_by(EmailVerificationCodeModel.created_at.desc())
                .limit(1)
            )
            if latest is not None and (now - self._as_utc(latest.created_at)).total_seconds() < 60:
                raise AccountError("verification_cooldown")

            day_count = session.scalar(
                select(func.count(EmailVerificationCodeModel.id)).where(
                    EmailVerificationCodeModel.email == normalized_email,
                    EmailVerificationCodeModel.created_at >= now - timedelta(hours=24),
                )
            ) or 0
            if day_count >= 10:
                raise AccountError("verification_rate_limited")

            if ip_key:
                ip_count = session.scalar(
                    select(func.count(EmailVerificationCodeModel.id)).where(
                        EmailVerificationCodeModel.request_ip == ip_key,
                        EmailVerificationCodeModel.created_at >= now - timedelta(hours=1),
                    )
                ) or 0
                if ip_count >= 25:
                    raise AccountError("verification_rate_limited")

            # Global circuit breaker to protect Resend quota against distributed bot traffic.
            max_per_hour = max(1, int(os.environ.get("ZHOUXU_EMAIL_MAX_PER_HOUR", "60") or 60))
            max_per_day = max(1, int(os.environ.get("ZHOUXU_EMAIL_MAX_PER_DAY", "200") or 200))
            global_hour = session.scalar(
                select(func.count(EmailVerificationCodeModel.id)).where(
                    EmailVerificationCodeModel.created_at >= now - timedelta(hours=1),
                )
            ) or 0
            if global_hour >= max_per_hour:
                raise AccountError("verification_rate_limited")
            global_day = session.scalar(
                select(func.count(EmailVerificationCodeModel.id)).where(
                    EmailVerificationCodeModel.created_at >= now - timedelta(hours=24),
                )
            ) or 0
            if global_day >= max_per_day:
                raise AccountError("verification_rate_limited")

            code = f"{secrets.randbelow(1_000_000):06d}"
            code_id = secrets.token_hex(16)
            session.add(
                EmailVerificationCodeModel(
                    id=code_id,
                    purpose=purpose,
                    email=normalized_email,
                    code_hash=self._hash_verification_code(normalized_email, purpose, code),
                    attempts=0,
                    request_ip=ip_key,
                    expires_at=now + timedelta(minutes=5),
                    consumed_at=None,
                    created_at=now,
                )
            )
            session.commit()
            return code_id, code
        finally:
            session.close()

    def cancel_verification_code(self, code_id: str) -> None:
        with self.database.session() as session:
            row = session.get(EmailVerificationCodeModel, code_id)
            if row is not None and row.consumed_at is None:
                session.delete(row)
                session.commit()

    def reset_password(
        self,
        *,
        email: str,
        verification_code: str,
        new_password: str,
    ) -> AuthResult:
        normalized_email = self._normalize_email(email)
        self._validate_password(new_password)
        now = self._now()
        session = self.database.session()
        try:
            account = session.scalar(
                select(AccountModel).where(AccountModel.email == normalized_email)
            )
            if account is None:
                raise AccountError("email_not_registered")
            if account.status != "active":
                raise AccountError("account_disabled")
            self._consume_verification_code(
                session,
                email=normalized_email,
                purpose="reset_password",
                code=verification_code,
                now=now,
            )
            account.password_hash = self.passwords.hash(new_password)
            account.updated_at = now
            self.revoke_sessions(session, account.id, now)
            result = self._create_session(session, account, now)
            self._audit(session, account.id, "auth.password_reset")
            session.commit()
            return result
        finally:
            session.close()

    def _consume_verification_code(
        self,
        session,
        *,
        email: str,
        purpose: str,
        code: str,
        now: datetime,
    ) -> None:
        clean_code = (code or "").strip()
        if not clean_code or len(clean_code) != 6 or not clean_code.isdigit():
            raise AccountError("verification_code_invalid")
        row = session.scalar(
            select(EmailVerificationCodeModel)
            .where(
                EmailVerificationCodeModel.email == email,
                EmailVerificationCodeModel.purpose == purpose,
                EmailVerificationCodeModel.consumed_at.is_(None),
            )
            .order_by(EmailVerificationCodeModel.created_at.desc())
            .limit(1)
        )
        if row is None or self._as_utc(row.expires_at) <= now:
            raise AccountError("verification_code_expired")
        if row.attempts >= 5:
            raise AccountError("verification_code_exhausted")
        expected = self._hash_verification_code(email, purpose, clean_code)
        if not secrets.compare_digest(row.code_hash, expected):
            row.attempts += 1
            session.commit()
            raise AccountError("verification_code_invalid")
        row.consumed_at = now

    @staticmethod
    def _hash_verification_code(email: str, purpose: str, code: str) -> str:
        return hashlib.sha256(f"{email}:{purpose}:{code}".encode("utf-8")).hexdigest()

    def login(self, *, email: str, password: str, required_role: str | None = None) -> AuthResult:
        if required_role is not None and required_role not in ACCOUNT_ROLES:
            raise ValueError("unsupported_account_role")
        normalized_email = self._normalize_email(email)
        session = self.database.session()
        try:
            account = session.scalar(
                select(AccountModel).where(AccountModel.email == normalized_email)
            )
            if account is None:
                raise AccountError("authentication_failed")
            try:
                valid = self.passwords.verify(account.password_hash, password)
            except (InvalidHashError, VerificationError, VerifyMismatchError):
                valid = False
            if not valid:
                self._audit_failure(session, account.id, "wrong_password", required_role)
                raise AccountError("authentication_failed")
            # Only someone who knows the password learns why they are refused.
            if account.status != "active":
                self._audit_failure(session, account.id, "account_disabled", required_role)
                raise AccountError("account_disabled")
            if required_role is not None and account.role != required_role:
                self._audit_failure(session, account.id, "not_an_administrator", required_role)
                raise AccountError("admin_required")
            now = self._now()
            result = self._create_session(session, account, now)
            self._audit(session, account.id, "auth.login")
            session.commit()
            return result
        finally:
            session.close()

    def ensure_admin(
        self, *, email: str, password: str, display_name: str = "管理员"
    ) -> AccountPrincipal:
        """Create the deployment administrator once without rotating secrets."""

        normalized_email = self._normalize_email(email)
        self._validate_password(password)
        session = self.database.session()
        try:
            account = session.scalar(
                select(AccountModel).where(AccountModel.email == normalized_email)
            )
            if account is not None:
                if account.role != "admin":
                    raise AccountError("admin_email_conflict")
                if account.status != "active":
                    raise AccountError("admin_account_inactive")
                return self._principal(account)
            now = self._now()
            normalized_name = display_name.strip()
            if len(normalized_name) > 80:
                raise AccountError("display_name_too_long")
            account = AccountModel(
                id=secrets.token_hex(16),
                email=normalized_email,
                display_name=normalized_name,
                password_hash=self.passwords.hash(password),
                status="active",
                role="admin",
                created_at=now,
                updated_at=now,
            )
            session.add(account)
            session.commit()
            return self._principal(account)
        except IntegrityError:
            session.rollback()
            raise AccountError("admin_email_conflict") from None
        finally:
            session.close()

    def has_active_admin(self) -> bool:
        """Whether at least one administrator can still sign in."""

        session = self.database.session()
        try:
            return (
                session.scalar(
                    select(AccountModel.id)
                    .where(AccountModel.role == "admin", AccountModel.status == "active")
                    .limit(1)
                )
                is not None
            )
        finally:
            session.close()

    def admin_login(self, *, email: str, password: str) -> AuthResult:
        return self.login(email=email, password=password, required_role="admin")

    def authenticate(self, token: str, *, update_last_seen: bool = True) -> AccountPrincipal:
        if not token or len(token) > 256:
            raise AccountError("authentication_required")
        token_hash = self._hash_token(token)
        session = self.database.session()
        try:
            auth_session = session.scalar(
                select(SessionModel).where(SessionModel.token_hash == token_hash)
            )
            now = self._now()
            if (
                auth_session is None
                or auth_session.revoked_at is not None
                or self._as_utc(auth_session.expires_at) <= now
                or auth_session.account is None
                or auth_session.account.status != "active"
            ):
                raise AccountError("authentication_required")
            if update_last_seen:
                auth_session.last_seen_at = now
                session.commit()
            return self._principal(auth_session.account)
        finally:
            session.close()

    def update_display_name(self, *, account_id: str, display_name: str) -> AccountPrincipal:
        normalized_name = display_name.strip()
        if len(normalized_name) > 80:
            raise AccountError("display_name_too_long")
        session = self.database.session()
        try:
            account = session.get(AccountModel, account_id)
            if account is None or account.status != "active":
                raise AccountError("authentication_required")
            account.display_name = normalized_name
            account.updated_at = self._now()
            session.commit()
            return self._principal(account)
        finally:
            session.close()

    def change_password(
        self,
        *,
        account_id: str,
        current_password: str,
        new_password: str,
        keep_token: str | None = None,
    ) -> None:
        """Change the password and sign out every other device."""

        self._validate_password(new_password)
        session = self.database.session()
        try:
            account = session.get(AccountModel, account_id)
            if account is None or account.status != "active":
                raise AccountError("authentication_required")
            try:
                valid = self.passwords.verify(account.password_hash, current_password)
            except (InvalidHashError, VerificationError, VerifyMismatchError):
                valid = False
            if not valid:
                raise AccountError("authentication_failed")
            account.password_hash = self.passwords.hash(new_password)
            now = self._now()
            account.updated_at = now
            self.revoke_sessions(session, account.id, now, except_token=keep_token)
            self._audit(session, account.id, "auth.password_changed")
            session.commit()
        finally:
            session.close()

    def logout(self, token: str) -> None:
        if not token:
            raise AccountError("authentication_required")
        session = self.database.session()
        try:
            auth_session = session.scalar(
                select(SessionModel).where(SessionModel.token_hash == self._hash_token(token))
            )
            if auth_session is None or auth_session.revoked_at is not None:
                raise AccountError("authentication_required")
            auth_session.revoked_at = self._now()
            session.commit()
        finally:
            session.close()

    def revoke_sessions(
        self, session, account_id: str, now: datetime, *, except_token: str | None = None
    ) -> None:
        """Mark every live session of the account revoked, optionally sparing one."""

        keep = self._hash_token(except_token) if except_token else None
        for row in session.scalars(
            select(SessionModel).where(
                SessionModel.account_id == account_id, SessionModel.revoked_at.is_(None)
            )
        ):
            if row.token_hash != keep:
                row.revoked_at = now

    def _audit(self, session, account_id: str, action: str, *, outcome: str = "succeeded", details: dict | None = None) -> None:
        from backend.application.admin import AdminService

        AdminService._add_audit(
            session,
            actor_id=account_id,
            target_account_id=account_id,
            action=action,
            resource_type="auth",
            resource_id=account_id,
            outcome=outcome,
            details=details or {},
        )

    def _audit_failure(self, session, account_id: str, reason: str, required_role: str | None) -> None:
        """Keep a record of a refused sign-in for a known account."""

        self._audit(
            session,
            account_id,
            "auth.login_failed",
            outcome="failed",
            details={"reason": reason, "entry": "admin" if required_role == "admin" else "user"},
        )
        session.commit()

    def _create_session(self, session, account: AccountModel, now: datetime) -> AuthResult:
        token = secrets.token_urlsafe(self.auth_settings.token_bytes)
        auth_session = SessionModel(
            id=secrets.token_hex(16),
            account_id=account.id,
            token_hash=self._hash_token(token),
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(seconds=self.database_settings.session_ttl_seconds),
        )
        session.add(auth_session)
        return AuthResult(account=self._principal(account), token=token)

    def _normalize_email(self, email: str) -> str:
        normalized = email.strip().lower()
        if len(normalized) > 320 or not EMAIL_PATTERN.fullmatch(normalized):
            raise AccountError("email_invalid")
        return normalized

    def _validate_password(self, password: str) -> None:
        if len(password) < self.auth_settings.password_min_length:
            raise AccountError("password_length_invalid")

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _principal(account: AccountModel) -> AccountPrincipal:
        return AccountPrincipal(
            id=account.id,
            email=account.email,
            display_name=account.display_name,
            role=account.role,
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
