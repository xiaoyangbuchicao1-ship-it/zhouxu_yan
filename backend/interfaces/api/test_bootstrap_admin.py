import tempfile
import unittest
from pathlib import Path

from backend.application.accounts.service import AccountError, AccountService
from backend.application.admin import AdminService
from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database import Database
from backend.interfaces.api.app import ensure_bootstrap_admin

BOOT = ("boot@example.com", "a-long-admin-password")


class BootstrapAdminTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = DatabaseSettings(url=f"sqlite:///{Path(self.tmp.name) / 'a.sqlite3'}")
        self.database = Database(settings)
        self.database.initialize()
        self.accounts = AccountService(self.database, settings, AuthSettings(password_min_length=8))

    def tearDown(self):
        self.database.close()
        self.tmp.cleanup()

    def _second_admin_demotes_bootstrap_admin(self):
        boot = self.accounts.ensure_admin(email=BOOT[0], password=BOOT[1])
        second = self.accounts.ensure_admin(email="second@example.com", password="another-long-password")
        AdminService(self.database).update_account(
            actor_id=second.id, account_id=boot.id, status="disabled"
        )

    def test_restart_survives_a_disabled_bootstrap_admin_when_another_admin_is_live(self):
        self._second_admin_demotes_bootstrap_admin()
        ensure_bootstrap_admin(self.accounts, *BOOT)  # must not raise
        self.assertTrue(self.accounts.has_active_admin())

    def test_restart_refuses_when_no_live_administrator_exists(self):
        self.accounts.register(email=BOOT[0], password=BOOT[1])  # ordinary user holds the email
        with self.assertRaises(AccountError) as raised:
            ensure_bootstrap_admin(self.accounts, *BOOT)
        self.assertEqual(raised.exception.code, "admin_email_conflict")

    def test_first_start_creates_the_administrator(self):
        ensure_bootstrap_admin(self.accounts, *BOOT)
        self.assertTrue(self.accounts.has_active_admin())


if __name__ == "__main__":
    unittest.main()
