from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from backend.application.accounts.service import AccountService
from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database.connection import Database
from backend.interfaces.api.app import create_app


class AdminHttpContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = DatabaseSettings(url=f"sqlite:///{Path(self.tmp.name) / 'admin.sqlite3'}")
        self.database = Database(settings)
        self.database.initialize()
        self.accounts = AccountService(
            self.database,
            settings,
            AuthSettings(password_min_length=8),
        )
        self.admin = self.accounts.ensure_admin(
            email="admin@example.com", password="a-long-admin-password"
        )
        self.user = self.accounts.register(
            email="user@example.com", password="a-long-user-password"
        )
        self.app = create_app(account_service=self.accounts)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.database.close()
        self.tmp.cleanup()

    async def test_admin_http_journey_and_user_forbidden(self):
        login = await self.client.post(
            "/auth/admin-login",
            json={"email": "admin@example.com", "password": "a-long-admin-password"},
        )
        self.assertEqual(login.status_code, 200)
        admin_token = login.json()["session"]["token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        overview = await self.client.get("/admin/overview", headers=headers)
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["overview"]["total_accounts"], 2)

        accounts = await self.client.get("/admin/accounts", headers=headers)
        self.assertEqual(accounts.status_code, 200)
        self.assertTrue(all("password_hash" not in item for item in accounts.json()["accounts"]))

        forbidden = await self.client.get(
            "/admin/overview",
            headers={"Authorization": f"Bearer {self.user.token}"},
        )
        self.assertEqual(forbidden.status_code, 403)

        update = await self.client.patch(
            f"/admin/accounts/{self.user.account.id}",
            headers=headers,
            json={"status": "disabled"},
        )
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.json()["status"], "disabled")

        audit = await self.client.get("/admin/audit", headers=headers)
        self.assertEqual(audit.status_code, 200)
        self.assertEqual(audit.json()["events"][0]["action"], "account.status_changed")

        user_login = await self.client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "a-long-user-password"},
        )
        self.assertEqual(user_login.status_code, 401)


if __name__ == "__main__":
    unittest.main()
