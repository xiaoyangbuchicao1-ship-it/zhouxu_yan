"""Administrator use cases for business accounts and audit records."""

from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, or_, select, update

from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import (
    AccountModel,
    AdminAccountAliasModel,
    BusinessAuditModel,
    ConversationModel,
    ConversationRunModel,
    MaterialModel,
    SessionModel,
)


class AdminServiceError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class AdminService:
    def __init__(self, database: Database):
        self.database = database

    def list_accounts(self, *, actor_id: str | None = None) -> list[dict]:
        with self.database.session() as session:
            accounts = session.scalars(
                select(AccountModel).order_by(AccountModel.created_at.desc(), AccountModel.id)
            ).all()
            alias_map: dict[str, AdminAccountAliasModel] = {}
            if actor_id:
                for row in session.scalars(
                    select(AdminAccountAliasModel).where(
                        AdminAccountAliasModel.admin_account_id == actor_id
                    )
                ).all():
                    alias_map[row.target_account_id] = row
            return [self._account_view(account, alias_map.get(account.id)) for account in accounts]

    def list_accounts_page(
        self,
        *,
        query: str = "",
        status: str | None = None,
        role: str | None = None,
        page: int = 1,
        page_size: int = 20,
        actor_id: str | None = None,
    ) -> dict:
        """Return the administrator's account table as a bounded server page."""
        if page < 1 or page_size < 1 or page_size > 100:
            raise AdminServiceError("invalid_account_pagination")
        if status is not None and status not in {"active", "disabled"}:
            raise AdminServiceError("invalid_account_status")
        if role is not None and role not in {"user", "admin"}:
            raise AdminServiceError("invalid_account_role")
        with self.database.session() as session:
            if actor_id:
                statement = select(AccountModel, AdminAccountAliasModel).outerjoin(
                    AdminAccountAliasModel,
                    and_(
                        AdminAccountAliasModel.admin_account_id == actor_id,
                        AdminAccountAliasModel.target_account_id == AccountModel.id,
                    ),
                )
                count_statement = select(func.count(AccountModel.id)).outerjoin(
                    AdminAccountAliasModel,
                    and_(
                        AdminAccountAliasModel.admin_account_id == actor_id,
                        AdminAccountAliasModel.target_account_id == AccountModel.id,
                    ),
                )
            else:
                statement = select(AccountModel)
                count_statement = select(func.count(AccountModel.id))
            if query.strip():
                needle = f"%{query.strip().lower()}%"
                predicates = [
                    func.lower(AccountModel.email).like(needle),
                    func.lower(AccountModel.display_name).like(needle),
                ]
                if actor_id:
                    predicates.extend([
                        func.lower(func.coalesce(AdminAccountAliasModel.alias, "")).like(needle),
                        func.lower(func.coalesce(AdminAccountAliasModel.note, "")).like(needle),
                    ])
                predicate = or_(*predicates)
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
            ordered = (
                statement.order_by(AccountModel.created_at.desc(), AccountModel.id)
                .offset((effective_page - 1) * page_size)
                .limit(page_size)
            )
            if actor_id:
                rows = session.execute(ordered).all()
                views = [self._account_view(account, alias_row) for account, alias_row in rows]
            else:
                accounts = session.scalars(ordered).all()
                views = [self._account_view(account) for account in accounts]
            return {
                "accounts": views,
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
            if next_status != "active":
                # Re-enabling later must not bring old sign-ins back to life.
                for live in session.scalars(
                    select(SessionModel).where(
                        SessionModel.account_id == target.id, SessionModel.revoked_at.is_(None)
                    )
                ):
                    live.revoked_at = target.updated_at
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
            alias_row = session.get(AdminAccountAliasModel, (actor.id, target.id))
            return self._account_view(target, alias_row)

    def set_account_alias(
        self,
        *,
        actor_id: str,
        account_id: str,
        alias: str = "",
        note: str = "",
    ) -> dict:
        clean_alias = (alias or "").strip()[:80]
        clean_note = (note or "").strip()[:240]
        with self.database.session() as session:
            actor = session.get(AccountModel, actor_id)
            if actor is None or actor.role != "admin" or actor.status != "active":
                raise AdminServiceError("admin_required")
            target = session.get(AccountModel, account_id)
            if target is None:
                raise AdminServiceError("account_not_found")
            row = session.get(AdminAccountAliasModel, (actor.id, target.id))
            if not clean_alias and not clean_note:
                if row is not None:
                    session.delete(row)
                    row = None
                    session.commit()
                return self._account_view(target, None)
            now = self._now()
            if row is None:
                row = AdminAccountAliasModel(
                    admin_account_id=actor.id,
                    target_account_id=target.id,
                    alias=clean_alias,
                    note=clean_note,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.alias = clean_alias
                row.note = clean_note
                row.updated_at = now
            session.commit()
            return self._account_view(target, row)

    _ACTIVE_RUN_STATUSES = ("queued", "preparing", "running", "pausing", "resuming", "retrying")

    def delete_account(self, *, actor_id: str, account_id: str) -> dict:
        """Permanently delete a non-self account and cascade its relational data.

        Returns the conversation IDs and material storage keys so the HTTP
        boundary can also purge runtime store sessions, generated workspace
        files, original uploads, and account-owned uploaded skills.
        """

        with self.database.session() as session:
            if self.database.engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            actor = session.get(AccountModel, actor_id)
            if actor is None or actor.role != "admin" or actor.status != "active":
                raise AdminServiceError("admin_required")
            target = session.get(AccountModel, account_id)
            if target is None:
                raise AdminServiceError("account_not_found")
            if target.id == actor.id:
                raise AdminServiceError("cannot_delete_self")
            if target.role == "admin" and target.status == "active":
                live_admins = session.scalar(
                    select(func.count(AccountModel.id)).where(
                        AccountModel.role == "admin",
                        AccountModel.status == "active",
                    )
                ) or 0
                if live_admins <= 1:
                    raise AdminServiceError("last_admin_required")

            active_runs = session.scalar(
                select(func.count(ConversationRunModel.run_id))
                .join(ConversationModel, ConversationModel.id == ConversationRunModel.conversation_id)
                .where(
                    ConversationModel.account_id == target.id,
                    ConversationRunModel.status.in_(self._ACTIVE_RUN_STATUSES),
                )
            ) or 0
            if active_runs > 0:
                raise AdminServiceError("account_has_active_runs")

            conversation_ids = list(
                session.scalars(
                    select(ConversationModel.id).where(ConversationModel.account_id == target.id)
                ).all()
            )
            material_storage_keys = list(
                session.scalars(
                    select(MaterialModel.storage_key).where(MaterialModel.account_id == target.id)
                ).all()
            )
            deleted_email = target.email
            deleted_name = target.display_name
            deleted_role = target.role
            deleted_id = target.id

            # business_audits.actor_id uses ON DELETE RESTRICT; remove rows
            # authored by the deleted account and detach target references
            # before deleting the account itself.
            session.execute(
                delete(AdminAccountAliasModel).where(
                    or_(
                        AdminAccountAliasModel.admin_account_id == deleted_id,
                        AdminAccountAliasModel.target_account_id == deleted_id,
                    )
                )
            )
            session.execute(
                delete(BusinessAuditModel).where(BusinessAuditModel.actor_id == deleted_id)
            )
            session.execute(
                update(BusinessAuditModel)
                .where(BusinessAuditModel.target_account_id == deleted_id)
                .values(target_account_id=None)
            )
            session.delete(target)
            session.flush()
            self._add_audit(
                session,
                actor_id=actor.id,
                target_account_id=None,
                action="account.deleted",
                resource_type="account",
                resource_id=deleted_id,
                details={
                    "email": deleted_email,
                    "display_name": deleted_name,
                    "role": deleted_role,
                    "conversations_deleted": len(conversation_ids),
                    "materials_deleted": len(material_storage_keys),
                },
            )
            session.commit()
            return {
                "account_id": deleted_id,
                "email": deleted_email,
                "conversation_ids": conversation_ids,
                "material_storage_keys": material_storage_keys,
            }

    AUDIT_CATEGORY_RESOURCES: dict[str, tuple[str, ...]] = {
        "account": ("account",),
        "capability": ("account_capability",),
        "model": ("model_provider", "model_profile"),
        "conversation": ("conversation", "material", "artifact"),
        "skill": ("skill",),
        "auth": ("auth",),
        "system": ("system",),
    }
    # What administrators changed: everything except sign-ins and log views.
    OPERATION_RESOURCES: tuple[str, ...] = (
        "account", "account_capability", "model_provider", "model_profile", "skill", "system",
    )
    _RESOURCE_TO_CATEGORY: dict[str, str] = {
        resource: cat
        for cat, resources in AUDIT_CATEGORY_RESOURCES.items()
        for resource in resources
    }

    def audit_summary(self) -> dict:
        """The last 24 hours at a glance, for the audit page header."""

        since = self._now() - timedelta(hours=24)
        with self.database.session() as session:
            def count(*conditions) -> int:
                return int(
                    session.scalar(
                        select(func.count(BusinessAuditModel.id)).where(BusinessAuditModel.created_at >= since, *conditions)
                    )
                    or 0
                )

            disabled = session.scalar(
                select(func.count(AccountModel.id)).where(AccountModel.status == "disabled")
            ) or 0
            return {
                "operations_24h": count(
                    BusinessAuditModel.resource_type != "auth",
                    BusinessAuditModel.action != "conversation.log_viewed",
                ),
                "logins_24h": count(BusinessAuditModel.action == "auth.login"),
                "login_failures_24h": count(BusinessAuditModel.action == "auth.login_failed"),
                "conversation_views_24h": count(BusinessAuditModel.action == "conversation.log_viewed"),
                "disabled_accounts": int(disabled),
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
        actor_id: str | None = None,
    ) -> dict:
        if limit < 1 or limit > 200:
            raise AdminServiceError("invalid_audit_limit")
        normalized_cat = category.strip().lower() if category else None
        if normalized_cat == "all":
            normalized_cat = None
        if (
            normalized_cat is not None
            and normalized_cat != "operations"
            and normalized_cat not in self.AUDIT_CATEGORY_RESOURCES
        ):
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

            counts = {"all": 0, "operations": 0, **{category: 0 for category in self.AUDIT_CATEGORY_RESOURCES}}
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
                if resource_type in self.OPERATION_RESOURCES:
                    counts["operations"] += 1

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
                resources = (
                    self.OPERATION_RESOURCES
                    if normalized_cat == "operations"
                    else self.AUDIT_CATEGORY_RESOURCES[normalized_cat]
                )
                statement = statement.where(BusinessAuditModel.resource_type.in_(resources))
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
            alias_map: dict[str, str] = {}
            if actor_id and rows:
                target_ids = list({e.target_account_id for e, _, _ in rows if e.target_account_id})
                if target_ids:
                    for item in session.scalars(
                        select(AdminAccountAliasModel).where(
                            AdminAccountAliasModel.admin_account_id == actor_id,
                            AdminAccountAliasModel.target_account_id.in_(target_ids),
                        )
                    ).all():
                        if item.alias:
                            alias_map[item.target_account_id] = item.alias
            events = [
                self._audit_view(event, actor_acc, target_acc, alias_map.get(event.target_account_id or ""))
                for event, actor_acc, target_acc in rows
            ]
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
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    @staticmethod
    def _audit_view(
        event: BusinessAuditModel,
        actor_account: AccountModel,
        target_account: AccountModel | None,
        target_alias: str | None = None,
    ) -> dict:
        return {
            "id": event.id,
            "action": event.action,
            "actor_id": event.actor_id,
            "actor_email": actor_account.email,
            "target_account_id": event.target_account_id,
            "target_email": target_account.email if target_account else None,
            "target_alias": target_alias or None,
            "resource_type": getattr(event, "resource_type", "account") or "account",
            "resource_id": getattr(event, "resource_id", None),
            "outcome": getattr(event, "outcome", "succeeded") or "succeeded",
            "details": json.loads(event.details_json) if event.details_json else {},
            "created_at": AdminService._utc(event.created_at),
        }

    @staticmethod
    def _account_view(account: AccountModel, alias_row: AdminAccountAliasModel | None = None) -> dict:
        return {
            "id": account.id,
            "email": account.email,
            "display_name": account.display_name,
            "alias": (alias_row.alias or None) if alias_row is not None else None,
            "alias_note": (alias_row.note or None) if alias_row is not None else None,
            "status": account.status,
            "role": account.role,
            "register_ip": getattr(account, "register_ip", None) or None,
            "created_at": AdminService._utc(account.created_at),
            "updated_at": AdminService._utc(account.updated_at),
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
