"""Administrator use cases for business accounts and audit records."""

from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select

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

    def list_accounts_page(
        self,
        *,
        query: str = "",
        status: str | None = None,
        role: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """Return the administrator's account table as a bounded server page."""
        if page < 1 or page_size < 1 or page_size > 100:
            raise AdminServiceError("invalid_account_pagination")
        if status is not None and status not in {"active", "disabled"}:
            raise AdminServiceError("invalid_account_status")
        if role is not None and role not in {"user", "admin"}:
            raise AdminServiceError("invalid_account_role")
        with self.database.session() as session:
            statement = select(AccountModel)
            count_statement = select(func.count(AccountModel.id))
            if query.strip():
                needle = f"%{query.strip().lower()}%"
                predicate = or_(
                    func.lower(AccountModel.email).like(needle),
                    func.lower(AccountModel.display_name).like(needle),
                )
                statement = statement.where(predicate)
                count_statement = count_statement.where(predicate)
            if status is not None:
                statement = statement.where(AccountModel.status == status)
                count_statement = count_statement.where(AccountModel.status == status)
            if role is not None:
                statement = statement.where(AccountModel.role == role)
                count_statement = count_statement.where(AccountModel.role == role)
            total = int(session.scalar(count_statement) or 0)
            total_pages = max(1, (total + page_size - 1) // page_size)
            effective_page = min(page, total_pages)
            accounts = session.scalars(
                statement.order_by(AccountModel.created_at.desc(), AccountModel.id)
                .offset((effective_page - 1) * page_size)
                .limit(page_size)
            ).all()
            return {
                "accounts": [self._account_view(account) for account in accounts],
                "query": query,
                "status": status,
                "role": role,
                "page": effective_page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            }

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

    AUDIT_CATEGORY_RESOURCES: dict[str, tuple[str, ...]] = {
        "account": ("account",),
        "capability": ("account_capability",),
        "model": ("model_provider", "model_profile"),
        "conversation": ("conversation",),
    }
    _RESOURCE_TO_CATEGORY: dict[str, str] = {
        resource: cat
        for cat, resources in AUDIT_CATEGORY_RESOURCES.items()
        for resource in resources
    }

    def list_audit(self, *, limit: int = 50) -> list[dict]:
        if limit < 1 or limit > 200:
            raise AdminServiceError("invalid_audit_limit")
        with self.database.session() as session:
            return self._list_audit_with_aliases(session, limit)

    def query_audit(
        self,
        *,
        category: str | None = None,
        outcome: str | None = None,
        query: str = "",
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict:
        if limit < 1 or limit > 200:
            raise AdminServiceError("invalid_audit_limit")
        normalized_cat = category.strip().lower() if category else None
        if normalized_cat == "all":
            normalized_cat = None
        if normalized_cat is not None and normalized_cat not in self.AUDIT_CATEGORY_RESOURCES:
            raise AdminServiceError("invalid_audit_category")
        normalized_out = outcome.strip().lower() if outcome else None
        if normalized_out == "all":
            normalized_out = None
        if normalized_out is not None and normalized_out not in {"succeeded", "failed"}:
            raise AdminServiceError("invalid_audit_outcome")
        cursor_time, cursor_id = self._decode_audit_cursor(cursor)
        term = query.strip()[:200]

        from sqlalchemy.orm import aliased

        actor = aliased(AccountModel)
        target = aliased(AccountModel)
        with self.database.session() as session:
            base_filters = []
            if normalized_out is not None:
                base_filters.append(BusinessAuditModel.outcome == normalized_out)
            if term:
                pattern = f"%{term}%"
                base_filters.append(
                    or_(
                        actor.email.ilike(pattern),
                        target.email.ilike(pattern),
                        BusinessAuditModel.action.ilike(pattern),
                        BusinessAuditModel.resource_id.ilike(pattern),
                    )
                )

            counts = {"all": 0, "account": 0, "capability": 0, "model": 0, "conversation": 0}
            count_stmt = (
                select(BusinessAuditModel.resource_type)
                .join(actor, actor.id == BusinessAuditModel.actor_id)
                .outerjoin(target, target.id == BusinessAuditModel.target_account_id)
            )
            if base_filters:
                count_stmt = count_stmt.where(*base_filters)
            for resource_type in session.scalars(count_stmt):
                counts["all"] += 1
                cat = self._RESOURCE_TO_CATEGORY.get(resource_type or "account")
                if cat is not None:
                    counts[cat] += 1

            statement = (
                select(BusinessAuditModel, actor, target)
                .join(actor, actor.id == BusinessAuditModel.actor_id)
                .outerjoin(target, target.id == BusinessAuditModel.target_account_id)
                .order_by(BusinessAuditModel.created_at.desc(), BusinessAuditModel.id.desc())
                .limit(limit + 1)
            )
            if base_filters:
                statement = statement.where(*base_filters)
            if normalized_cat is not None:
                statement = statement.where(
                    BusinessAuditModel.resource_type.in_(
                        self.AUDIT_CATEGORY_RESOURCES[normalized_cat]
                    )
                )
            if cursor_time is not None:
                statement = statement.where(
                    or_(
                        BusinessAuditModel.created_at < cursor_time,
                        and_(
                            BusinessAuditModel.created_at == cursor_time,
                            BusinessAuditModel.id < cursor_id,
                        ),
                    )
                )
            rows = session.execute(statement).all()
            has_more = len(rows) > limit
            rows = rows[:limit]
            events = [self._audit_view(event, actor_acc, target_acc) for event, actor_acc, target_acc in rows]
            next_cursor = None
            if has_more and rows:
                next_cursor = self._encode_audit_cursor(rows[-1][0].created_at, rows[-1][0].id)
            return {
                "events": events,
                "category_counts": counts,
                "next_cursor": next_cursor,
            }

    @staticmethod
    def _encode_audit_cursor(value: datetime, row_id: str) -> str:
        utc_dt = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        payload = json.dumps({"time": utc_dt.isoformat(), "id": row_id}, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_audit_cursor(value: str | None) -> tuple[datetime | None, str | None]:
        if value is None:
            return None, None
        try:
            padded = value + "=" * (-len(value) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            timestamp = datetime.fromisoformat(payload["time"])
            row_id = payload["id"]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise AdminServiceError("invalid_audit_cursor") from None
        if not isinstance(row_id, str) or not row_id:
            raise AdminServiceError("invalid_audit_cursor")
        return timestamp, row_id

    @staticmethod
    def _audit_view(event: BusinessAuditModel, actor_account: AccountModel, target_account: AccountModel | None) -> dict:
        return {
            "id": event.id,
            "action": event.action,
            "actor_id": event.actor_id,
            "actor_email": actor_account.email,
            "target_account_id": event.target_account_id,
            "target_email": target_account.email if target_account else None,
            "resource_type": getattr(event, "resource_type", "account") or "account",
            "resource_id": getattr(event, "resource_id", None),
            "outcome": getattr(event, "outcome", "succeeded") or "succeeded",
            "details": json.loads(event.details_json) if event.details_json else {},
            "created_at": event.created_at,
        }

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
        return [
            self._audit_view(event, actor_account, target_account)
            for event, actor_account, target_account in rows
        ]

    @staticmethod
    def _add_audit(
        session,
        *,
        actor_id: str,
        target_account_id: str | None,
        action: str,
        details: dict,
        resource_type: str = "account",
        resource_id: str | None = None,
        outcome: str = "succeeded",
    ) -> None:
        session.add(
            BusinessAuditModel(
                id=secrets.token_hex(16),
                actor_id=actor_id,
                target_account_id=target_account_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome=outcome,
                details_json=json.dumps(details, ensure_ascii=False, separators=(",", ":")),
                created_at=AdminService._now(),
            )
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
