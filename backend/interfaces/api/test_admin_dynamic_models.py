"""Red HTTP tests for the database-backed model directory.

No upstream request is made here.  The first node only proves that a model
entered by an administrator is persisted, visible to both work modes through
the same catalog, and changed through a new revision.  Protocol and live
provider checks belong to the next node.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import httpx

from backend.application.accounts.service import AccountService
from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database.connection import Database
from backend.interfaces.api.app import create_app


class AdminDynamicModelHttpContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key_name = "ZHOUXU_TEST_MODEL_KEY"
        self.previous_key = os.environ.get(self.key_name)
        os.environ[self.key_name] = "test-only-key"
        settings = DatabaseSettings(url=f"sqlite:///{Path(self.tmp.name) / 'models.sqlite3'}")
        self.database = Database(settings)
        self.database.initialize()
        self.accounts = AccountService(
            self.database, settings, AuthSettings(password_min_length=8)
        )
        self.admin = self.accounts.ensure_admin(
            email="admin@example.com", password="a-long-admin-password"
        )
        self.user = self.accounts.register(
            email="user@example.com", password="a-long-user-password"
        )
        self.admin_login = self.accounts.login(
            email="admin@example.com", password="a-long-admin-password"
        )
        self.user_login = self.accounts.login(
            email="user@example.com", password="a-long-user-password"
        )
        self.app = create_app(account_service=self.accounts)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.database.close()
        if self.previous_key is None:
            os.environ.pop(self.key_name, None)
        else:
            os.environ[self.key_name] = self.previous_key
        self.tmp.cleanup()

    async def test_admin_can_add_model_and_same_catalog_serves_chat_and_work(self):
        admin_headers = {"Authorization": f"Bearer {self.admin_login.token}"}
        provider = await self.client.post(
            "/admin/model-providers",
            headers=admin_headers,
            json={
                "id": "preview-relay",
                "name": "预览服务商",
                "preset_type": "custom",
                "default_protocol": "openai_chat_completions",
                "auth_style": "bearer",
                "base_url": "https://models.example.test/v1",
                "credential_mode": "env_ref",
                "credential_ref": self.key_name,
                "enabled": True,
            },
        )
        self.assertEqual(provider.status_code, 201, provider.text)
        self.assertEqual(provider.json()["id"], "preview-relay")

        model = await self.client.post(
            "/admin/models",
            headers=admin_headers,
            json={
                "profile_id": "preview-relay:gpt-preview",
                "provider_id": "preview-relay",
                "upstream_model_id": "gpt-preview",
                "label": "GPT 预览",
                "family": "gpt",
                "protocol_override": "openai_chat_completions",
                "context_tokens": 128000,
                "max_output_tokens": 16000,
                "thinking_modes": ["none", "low"],
                "default_thinking": "low",
                "enabled": True,
                "access_scope": "all",
            },
        )
        self.assertEqual(model.status_code, 201, model.text)
        self.assertEqual(model.json()["profile_id"], "preview-relay:gpt-preview")
        first_revision = model.json()["revision"]

        user_headers = {"Authorization": f"Bearer {self.user_login.token}"}
        catalog = await self.client.get("/workspace/models", headers=user_headers)
        self.assertEqual(catalog.status_code, 200, catalog.text)
        profiles = [
            item
            for group in catalog.json()["groups"]
            for item in group["models"]
        ]
        selected = next(item for item in profiles if item["profile_id"] == "preview-relay:gpt-preview")
        self.assertEqual(selected["label"], "GPT 预览")
        self.assertIn("low", selected["thinking_modes"])

        # The same response is the source for both selectors; the API must not
        # maintain a separate chat-only and work-only model list.
        work_catalog = await self.client.get("/workspace/models?mode=work", headers=user_headers)
        self.assertEqual(work_catalog.status_code, 200, work_catalog.text)
        work_profiles = {
            item["profile_id"]
            for group in work_catalog.json()["groups"]
            for item in group["models"]
        }
        self.assertIn("preview-relay:gpt-preview", work_profiles)

        changed = await self.client.patch(
            "/admin/models/preview-relay%3Agpt-preview",
            headers=admin_headers,
            json={"label": "GPT 预览新版", "context_tokens": 256000},
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertGreater(changed.json()["revision"], first_revision)
        self.assertEqual(changed.json()["label"], "GPT 预览新版")

        restarted_catalog = await self.client.get("/workspace/models", headers=user_headers)
        self.assertEqual(restarted_catalog.status_code, 200, restarted_catalog.text)
        restarted = [
            item
            for group in restarted_catalog.json()["groups"]
            for item in group["models"]
            if item["profile_id"] == "preview-relay:gpt-preview"
        ][0]
        self.assertEqual(restarted["label"], "GPT 预览新版")
        self.assertEqual(restarted["context_tokens"], 256000)

    async def test_normal_user_cannot_change_provider_or_model_directory(self):
        user_headers = {"Authorization": f"Bearer {self.user_login.token}"}
        response = await self.client.post(
            "/admin/model-providers",
            headers=user_headers,
            json={
                "id": "forbidden",
                "name": "不应创建",
                "preset_type": "custom",
                "default_protocol": "openai_chat_completions",
                "auth_style": "bearer",
                "base_url": "https://models.example.test/v1",
                "credential_mode": "env_ref",
                "credential_ref": self.key_name,
                "enabled": True,
            },
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
