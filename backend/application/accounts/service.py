"""Small account application service backed by the relational adapter.

The service owns account rules and token lifecycle.  The API layer only maps
these outcomes to HTTP; it never hashes passwords or queries account tables.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import AccountModel, SessionModel

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


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

    def register(self, *, email: str, password: str, display_name: str = "") -> AuthResult:
        normalized_email = self._normalize_email(email)
        self._validate_password(password)
        normalized_name = display_name.strip()
        if len(normalized_name) > 80:
            raise AccountError("display_name_too_long")
        now = self._now()
        account = AccountModel(
            id=secrets.token_hex(16),
            email=normalized_email,
            display_name=normalized_name,
            password_hash=self.passwords.hash(password),
            status="active",
            created_at=now,
            updated_at=now,
        )
        session = self.database.session()
        try:
            session.add(account)
            session.flush()
        except IntegrityError:
            session.rollback()
            raise AccountError("account_email_exists") from None
        result = self._create_session(session, account, now)
        session.commit()
        session.close()
        return result

    def login(self, *, email: str, password: str) -> AuthResult:
        normalized_email = self._normalize_email(email)
        session = self.database.session()
        try:
            account = session.scalar(
                select(AccountModel).where(AccountModel.email == normalized_email)
            )
            if account is None or account.status != "active":
                raise AccountError("authentication_failed")
            try:
                valid = self.passwords.verify(account.password_hash, password)
            except (InvalidHashError, VerificationError, VerifyMismatchError):
                valid = False
            if not valid:
                raise AccountError("authentication_failed")
            now = self._now()
            result = self._create_session(session, account, now)
            session.commit()
            return result
        finally:
            session.close()

    def authenticate(self, token: str) -> AccountPrincipal:
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
                or auth_session.account.status != "active"
            ):
                raise AccountError("authentication_required")
            auth_session.last_seen_at = now
            session.commit()
            return self._principal(auth_session.account)
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
            id=account.id, email=account.email, display_name=account.display_name
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
