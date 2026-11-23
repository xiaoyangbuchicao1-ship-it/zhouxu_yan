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
from unittest.mock import patch

import httpx

from backend.application.accounts.service import AccountService
from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database.connection import Database
from backend.interfaces.api.app import create_app


class AdminDynamicModelHttpContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key_name = "ZHOUXU_MODEL_KEY_TEST"
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

    async def _verify_and_enable(self, profile_id: str) -> dict:
        headers = {"Authorization": f"Bearer {self.admin_login.token}"}
        encoded = profile_id.replace(":", "%3A")

        async def ok_upstream(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": "ok", "choices": [{"message": {"content": "你好"}}]})

        with patch(
            "backend.infrastructure.model.health.pinned_async_transport",
            return_value=httpx.MockTransport(ok_upstream),
        ):
            probed = await self.client.post(f"/admin/models/{encoded}/test", headers=headers)
            self.assertEqual(probed.status_code, 200, probed.text)
            self.assertTrue(probed.json()["ok"])
        enabled = await self.client.patch(
            f"/admin/models/{encoded}", headers=headers, json={"enabled": True}
        )
        self.assertEqual(enabled.status_code, 200, enabled.text)
        return enabled.json()

    async def create_model(self, *, provider_id: str = "preview-relay", profile_id: str = "preview-relay:gpt-preview"):
        headers = {"Authorization": f"Bearer {self.admin_login.token}"}
        provider = await self.client.post(
            "/admin/model-providers",
            headers=headers,
            json={
                "id": provider_id,
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
        model = await self.client.post(
            "/admin/models",
            headers=headers,
            json={
                "profile_id": profile_id,
                "provider_id": provider_id,
                "upstream_model_id": "gpt-preview",
                "label": "GPT 预览",
                "family": "gpt",
                "protocol_override": "openai_chat_completions",
                "context_tokens": 128000,
                "max_output_tokens": 16000,
                "thinking_modes": ["none", "low"],
                "default_thinking": "low",
                "enabled": False,
                "access_scope": "all",
                "sort_order": 20,
            },
        )
        self.assertEqual(model.status_code, 201, model.text)
        enabled = await self._verify_and_enable(profile_id)
        return provider.json(), enabled

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
                "enabled": False,
                "access_scope": "all",
            },
        )
        self.assertEqual(model.status_code, 201, model.text)
        enabled = await self._verify_and_enable("preview-relay:gpt-preview")
        self.assertEqual(enabled["profile_id"], "preview-relay:gpt-preview")
        first_revision = enabled["revision"]

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

    async def test_admin_can_list_directory_without_exposing_a_credential_value(self):
        await self.create_model()
        response = await self.client.get(
            "/admin/model-directory",
            headers={"Authorization": f"Bearer {self.admin_login.token}"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        provider = next(item for item in body["providers"] if item["id"] == "preview-relay")
        self.assertEqual(provider["credential_ref"], self.key_name)
        self.assertTrue(provider["credential_configured"])
        self.assertEqual(body["models"][0]["profile_id"], "preview-relay:gpt-preview")
        serialized = response.text
        self.assertNotIn("test-only-key", serialized)
        self.assertNotIn("api_key_encrypted", serialized)

    async def test_provider_change_creates_new_pinned_model_revision(self):
        _, created = await self.create_model()
        old_revision_id = f'{created["profile_id"]}:r{created["revision"]}'
        directory = self.app.state.model_directory
        old = directory.resolve(created["profile_id"], profile_revision_id=old_revision_id)

        response = await self.client.patch(
            "/admin/model-providers/preview-relay",
            headers={"Authorization": f"Bearer {self.admin_login.token}"},
            json={
                "name": "预览服务商新版",
                "default_protocol": "openai_responses",
                "base_url": "https://responses.example.test/v2",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["revision"], 2)

        current = directory.resolve(created["profile_id"])
        pinned = directory.resolve(created["profile_id"], profile_revision_id=old_revision_id)
        self.assertNotEqual(current.profile_revision_id, old_revision_id)
        self.assertEqual(current.profile.protocol, "openai_chat_completions")
        self.assertIn("responses.example.test", current.endpoint)
        self.assertEqual(pinned.profile.model_id, old.profile.model_id)
        self.assertEqual(pinned.endpoint, old.endpoint)
        self.assertEqual(pinned.provider_revision_id, old.provider_revision_id)

    async def test_model_change_keeps_the_old_revision_exactly_unchanged(self):
        _, created = await self.create_model()
        profile_id = created["profile_id"]
        old_revision_id = f'{profile_id}:r{created["revision"]}'
        directory = self.app.state.model_directory
        before = directory.resolve(profile_id, profile_revision_id=old_revision_id)

        changed = await self.client.patch(
            "/admin/models/preview-relay%3Agpt-preview",
            headers={"Authorization": f"Bearer {self.admin_login.token}"},
            json={
                "upstream_model_id": "gpt-preview-2",
                "label": "GPT 预览 2",
                "protocol_override": "openai_responses",
                "context_tokens": 256000,
                "max_output_tokens": 32000,
                "thinking_modes": ["low", "medium", "high"],
                "default_thinking": "medium",
                "sort_order": 5,
            },
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertGreater(changed.json()["revision"], created["revision"])

        current = directory.resolve(profile_id)
        pinned = directory.resolve(profile_id, profile_revision_id=old_revision_id)
        self.assertEqual(current.profile.model_id, "gpt-preview-2")
        self.assertEqual(current.profile.protocol, "openai_responses")
        self.assertEqual(current.profile.context_tokens, 256000)
        self.assertEqual(current.profile.thinking_modes, ["low", "medium", "high"])
        self.assertEqual(pinned.profile.model_dump(mode="json"), before.profile.model_dump(mode="json"))
        self.assertEqual(pinned.endpoint, before.endpoint)

    async def test_plaintext_api_key_is_rejected_at_the_http_boundary(self):
        response = await self.client.post(
            "/admin/model-providers",
            headers={"Authorization": f"Bearer {self.admin_login.token}"},
            json={
                "id": "unsafe",
                "name": "不安全",
                "default_protocol": "openai_chat_completions",
                "base_url": "https://models.example.test/v1",
                "credential_mode": "env_ref",
                "credential_ref": self.key_name,
                "api_key": "must-not-be-accepted",
            },
        )
        self.assertEqual(response.status_code, 422, response.text)

    async def test_public_provider_does_not_require_a_credential_reference(self):
        response = await self.client.post(
            "/admin/model-providers",
            headers={"Authorization": f"Bearer {self.admin_login.token}"},
            json={
                "id": "public-models",
                "name": "公开服务",
                "default_protocol": "openai_chat_completions",
                "auth_style": "none",
                "base_url": "https://models.example.test/v1",
                "credential_mode": "env_ref",
                "enabled": True,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.json()["credential_configured"])


if __name__ == "__main__":
    unittest.main()
