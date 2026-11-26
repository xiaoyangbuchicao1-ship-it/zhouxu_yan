from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

from sqlalchemy import event, select

from backend.application.accounts.service import AccountService
from backend.application.admin import AdminService, AdminServiceError
from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import AccountModel


class AdminServiceContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = DatabaseSettings(url=f"sqlite:///{Path(self.tmp.name) / 'admin.sqlite3'}")
        self.database = Database(settings)
        self.database.initialize()
        self.accounts = AccountService(self.database, settings, AuthSettings(password_min_length=8))
        self.admin = self.accounts.ensure_admin(email="admin@example.com", password="a-long-admin-password")
        self.user = self.accounts.register(email="user@example.com", password="a-long-user-password")
        self.service = AdminService(self.database)

    def tearDown(self):
        self.database.close()
        self.tmp.cleanup()

    def test_admin_can_list_accounts_without_secrets(self):
        rows = self.service.list_accounts()
        self.assertEqual({row["email"] for row in rows}, {"admin@example.com", "user@example.com"})
        self.assertTrue(all("password_hash" not in row and "token" not in row for row in rows))

    def test_admin_can_disable_user_and_new_login_is_rejected(self):
        updated = self.service.update_account(actor_id=self.admin.id, account_id=self.user.account.id, status="disabled")
        self.assertEqual(updated["status"], "disabled")
        with self.assertRaisesRegex(Exception, "account_disabled"):
            self.accounts.login(email="user@example.com", password="a-long-user-password")

    def test_cannot_disable_last_admin_or_change_self(self):
        with self.assertRaisesRegex(AdminServiceError, "last_admin_required"):
            self.service.update_account(actor_id=self.admin.id, account_id=self.admin.id, status="disabled")

    def test_business_audit_contains_account_change_only(self):
        self.service.update_account(actor_id=self.admin.id, account_id=self.user.account.id, role="admin")
        events = self.service.list_audit()
        self.assertEqual(events[0]["action"], "account.role_changed")
        self.assertNotIn("password_hash", events[0])
        self.assertNotIn("token", events[0])

    def test_concurrent_admin_changes_cannot_remove_both_administrators(self):
        second = self.accounts.ensure_admin(
            email="second-admin@example.com", password="a-long-admin-password"
        )
        both_checked = Event()
        count_lock = Lock()
        checked = 0

        def coordinate_reads(connection, cursor, statement, parameters, context, executemany):
            nonlocal checked
            if "SELECT count(accounts.id)" in statement and "accounts.status" in statement:
                with count_lock:
                    checked += 1
                    if checked == 2:
                        both_checked.set()
                both_checked.wait(0.4)

        def disable(actor, target):
            try:
                self.service.update_account(actor_id=actor.id, account_id=target.id, status="disabled")
                return "changed"
            except AdminServiceError as exc:
                return exc.code

        event.listen(self.database.engine, "after_cursor_execute", coordinate_reads)
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(disable, self.admin, second)
                other = executor.submit(disable, second, self.admin)
                outcomes = [first.result(timeout=5), other.result(timeout=5)]
        finally:
            event.remove(self.database.engine, "after_cursor_execute", coordinate_reads)
        with self.database.session() as session:
            live = session.scalars(select(AccountModel).where(AccountModel.role == "admin", AccountModel.status == "active")).all()
        self.assertEqual(len(live), 1)
        self.assertEqual(outcomes.count("changed"), 1)
        changes = [item for item in self.service.list_audit() if item["action"].startswith("account.")]
        self.assertEqual(len(changes), 1)


if __name__ == "__main__":
    unittest.main()
