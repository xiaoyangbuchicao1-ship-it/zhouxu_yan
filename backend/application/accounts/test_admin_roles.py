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
