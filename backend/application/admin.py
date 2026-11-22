"""Administrator use cases for business accounts and audit records."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select

from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import (
    AccountModel,
    BusinessAuditModel,
)


class AdminServiceError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class AdminService:
    def __init__(self, database: Database):
        self.database = database

    def list_accounts(self) -> list[dict]:
        with self.database.session() as session:
            accounts = session.scalars(
                select(AccountModel).order_by(AccountModel.created_at.desc(), AccountModel.id)
            ).all()
            return [self._account_view(account) for account in accounts]

    def overview(self) -> dict:
        with self.database.session() as session:
            total = session.scalar(select(func.count(AccountModel.id))) or 0
            active = session.scalar(
                select(func.count(AccountModel.id)).where(AccountModel.status == "active")
            ) or 0
            disabled = session.scalar(
                select(func.count(AccountModel.id)).where(AccountModel.status == "disabled")
            ) or 0
            admins = session.scalar(
                select(func.count(AccountModel.id)).where(AccountModel.role == "admin")
            ) or 0
            audits = session.scalar(select(func.count(BusinessAuditModel.id))) or 0
            return {
                "total_accounts": int(total),
                "active_accounts": int(active),
                "disabled_accounts": int(disabled),
                "admin_accounts": int(admins),
                "audit_events": int(audits),
            }

    def update_account(
        self,
        *,
        actor_id: str,
        account_id: str,
        status: str | None = None,
        role: str | None = None,
    ) -> dict:
        valid_statuses = {"active", "disabled"}
        valid_roles = {"user", "admin"}
        if status is not None and status not in valid_statuses:
            raise AdminServiceError("invalid_account_status")
        if role is not None and role not in valid_roles:
            raise AdminServiceError("invalid_account_role")
        if status is None and role is None:
            raise AdminServiceError("account_change_required")

        with self.database.session() as session:
            # SQLite serialises writers only after the first write.  Acquiring
            # the write reservation before counting live administrators makes
            # the last-admin rule atomic across concurrent admin requests.
            if self.database.engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            actor = session.get(AccountModel, actor_id)
            if actor is None or actor.role != "admin" or actor.status != "active":
                raise AdminServiceError("admin_required")
            target = session.get(AccountModel, account_id)
            if target is None:
                raise AdminServiceError("account_not_found")

            old_status = target.status
            old_role = target.role
            next_status = status if status is not None else old_status
            next_role = role if role is not None else old_role
            if next_status == old_status and next_role == old_role:
                return self._account_view(target)

            # A live administrator is needed to keep the admin entry point
            # reachable.  This check applies to both disabling and demoting.
            target_loses_admin_access = old_role == "admin" and (
                next_role != "admin" or next_status != "active"
            )
            if target_loses_admin_access:
                live_admins = session.scalar(
                    select(func.count(AccountModel.id)).where(
                        AccountModel.role == "admin",
                        AccountModel.status == "active",
                    )
                ) or 0
                if live_admins <= 1:
                    raise AdminServiceError("last_admin_required")
            if target.id == actor.id:
                raise AdminServiceError("cannot_change_self")

            target.status = next_status
            target.role = next_role
            target.updated_at = self._now()
            changes = {"status": {"from": old_status, "to": next_status},
                       "role": {"from": old_role, "to": next_role}}
            if old_status == next_status:
                changes.pop("status")
            if old_role == next_role:
                changes.pop("role")
            for field, change in changes.items():
                self._add_audit(
                    session,
                    actor_id=actor.id,
                    target_account_id=target.id,
                    action=f"account.{field}_changed",
                    details=change,
                )
            session.commit()
            return self._account_view(target)

    def list_audit(self, *, limit: int = 50) -> list[dict]:
        if limit < 1 or limit > 200:
            raise AdminServiceError("invalid_audit_limit")
        with self.database.session() as session:
            return self._list_audit_with_aliases(session, limit)

    @staticmethod
    def _account_view(account: AccountModel) -> dict:
        return {
            "id": account.id,
            "email": account.email,
            "display_name": account.display_name,
            "status": account.status,
            "role": account.role,
            "created_at": account.created_at,
            "updated_at": account.updated_at,
        }

    def _list_audit_with_aliases(self, session, limit: int) -> list[dict]:
        from sqlalchemy.orm import aliased

        actor = aliased(AccountModel)
        target = aliased(AccountModel)
        rows = session.execute(
            select(BusinessAuditModel, actor, target)
            .join(actor, actor.id == BusinessAuditModel.actor_id)
            .outerjoin(target, target.id == BusinessAuditModel.target_account_id)
            .order_by(BusinessAuditModel.created_at.desc(), BusinessAuditModel.id.desc())
            .limit(limit)
        ).all()
        result = []
        for event, actor_account, target_account in rows:
            result.append(
                {
                    "id": event.id,
                    "action": event.action,
                    "actor_id": event.actor_id,
                    "actor_email": actor_account.email,
                    "target_account_id": event.target_account_id,
                    "target_email": target_account.email if target_account else None,
                    "details": json.loads(event.details_json) if event.details_json else {},
                    "created_at": event.created_at,
                }
            )
        return result

    @staticmethod
    def _add_audit(
        session,
        *,
        actor_id: str,
        target_account_id: str | None,
        action: str,
        details: dict,
    ) -> None:
        session.add(
            BusinessAuditModel(
                id=secrets.token_hex(16),
                actor_id=actor_id,
                target_account_id=target_account_id,
                action=action,
                details_json=json.dumps(details, ensure_ascii=False, separators=(",", ":")),
                created_at=AdminService._now(),
            )
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
