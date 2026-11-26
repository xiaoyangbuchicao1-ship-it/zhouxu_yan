"""Node 1 red tests for account roles and the administrator entry point.

These tests deliberately describe the new contract before its implementation:
existing accounts stay usable, newly registered accounts are ordinary users,
and administrator bootstrap is repeatable without replacing an existing row.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.application.accounts.service import AccountError, AccountService
from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database.connection import Database
from backend.interfaces.api.contracts import AccountView


class AccountRoleContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.database = Database(
            DatabaseSettings(url=f"sqlite:///{Path(self._tmp.name) / 'accounts.sqlite3'}")
        )
        self.database.initialize()
        self.service = AccountService(
            self.database,
            DatabaseSettings(
                url=self.database.url,
                session_ttl_seconds=900,
            ),
            AuthSettings(password_min_length=8),
        )

    def tearDown(self):
        self.database.close()
        self._tmp.cleanup()

    def test_self_registered_account_is_user(self):
        result = self.service.register(
            email="reader@example.com",
            password="a-long-test-password",
            display_name="Reader",
        )

        self.assertEqual(result.account.role, "user")

        authenticated = self.service.authenticate(result.token)
        self.assertEqual(authenticated.role, "user")

    def test_public_account_view_carries_role_for_route_guards(self):
        view = AccountView(
            id="account-1",
            email="reader@example.com",
            display_name="Reader",
            role="user",
        )

        self.assertEqual(view.role, "user")

    def test_admin_bootstrap_is_idempotent_and_keeps_the_same_account(self):
        first = self.service.ensure_admin(
            email="admin@example.com",
            password="a-long-admin-password",
            display_name="Administrator",
        )
        second = self.service.ensure_admin(
            email="ADMIN@example.com",
            password="a-long-admin-password",
            display_name="Administrator",
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.role, "admin")
        self.assertEqual(second.role, "admin")

    def test_has_active_admin_follows_the_live_administrators(self):
        self.assertFalse(self.service.has_active_admin())
        self.service.ensure_admin(
            email="admin@example.com", password="a-long-admin-password"
        )
        self.assertTrue(self.service.has_active_admin())

    def test_admin_entry_rejects_an_ordinary_account(self):
        self.service.register(
            email="reader@example.com",
            password="a-long-test-password",
        )

        with self.assertRaises(AccountError) as raised:
            self.service.admin_login(
                email="reader@example.com",
                password="a-long-test-password",
            )

        self.assertEqual(raised.exception.code, "admin_required")

    def test_wrong_admin_password_is_reported_as_wrong_password_not_as_missing_role(self):
        self.service.ensure_admin(email="admin@example.com", password="a-long-admin-password")
        with self.assertRaises(AccountError) as raised:
            self.service.admin_login(email="admin@example.com", password="not-the-right-password")
        self.assertEqual(raised.exception.code, "authentication_failed")

    def test_disabled_account_is_named_only_after_the_correct_password(self):
        from backend.application.admin import AdminService

        admin = self.service.ensure_admin(email="admin@example.com", password="a-long-admin-password")
        reader = self.service.register(email="reader@example.com", password="a-long-test-password")
        AdminService(self.service.database).update_account(
            actor_id=admin.id, account_id=reader.account.id, status="disabled"
        )
        with self.assertRaises(AccountError) as wrong:
            self.service.login(email="reader@example.com", password="not-the-right-password")
        self.assertEqual(wrong.exception.code, "authentication_failed")
        with self.assertRaises(AccountError) as right:
            self.service.login(email="reader@example.com", password="a-long-test-password")
        self.assertEqual(right.exception.code, "account_disabled")

    def test_disabling_revokes_sessions_for_good_even_after_re_enabling(self):
        from backend.application.admin import AdminService

        admin = self.service.ensure_admin(email="admin@example.com", password="a-long-admin-password")
        reader = self.service.register(email="reader@example.com", password="a-long-test-password")
        admin_service = AdminService(self.service.database)
        admin_service.update_account(actor_id=admin.id, account_id=reader.account.id, status="disabled")
        admin_service.update_account(actor_id=admin.id, account_id=reader.account.id, status="active")
        with self.assertRaises(AccountError):
            self.service.authenticate(reader.token)

    def test_changing_the_password_signs_out_other_devices_but_not_this_one(self):
        first = self.service.register(email="reader@example.com", password="a-long-test-password")
        other_device = self.service.login(email="reader@example.com", password="a-long-test-password")
        self.service.change_password(
            account_id=first.account.id,
            current_password="a-long-test-password",
            new_password="another-long-password",
            keep_token=first.token,
        )
        self.assertEqual(self.service.authenticate(first.token).id, first.account.id)
        with self.assertRaises(AccountError):
            self.service.authenticate(other_device.token)

    def test_admin_entry_returns_an_admin_principal(self):
        self.service.ensure_admin(
            email="admin@example.com",
            password="a-long-admin-password",
        )

        result = self.service.admin_login(
            email="admin@example.com",
            password="a-long-admin-password",
        )

        self.assertEqual(result.account.role, "admin")


if __name__ == "__main__":
    unittest.main()
