from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography.fernet import Fernet

from backend.application.accounts.service import AccountService
from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database.connection import Database
from backend.interfaces.api.app import create_app


class AdminModelsV2ContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.master_key = Fernet.generate_key().decode("ascii")
        self.env_patch = patch.dict(
            os.environ,
            {
                "ZHOUXU_ADMIN_CREDENTIAL_KEY": self.master_key,
                "SILICONFLOW_API_KEY": "sf-env-secret-9999",
            },
            clear=False,
        )
        self.env_patch.start()
        settings = DatabaseSettings(url=f"sqlite:///{Path(self.tmp.name) / 'models_v2.sqlite3'}")
        self.database = Database(settings)
        self.database.initialize()
        self.accounts = AccountService(self.database, settings, AuthSettings(password_min_length=8))
        self.accounts.ensure_admin(email="admin@example.com", password="a-long-admin-password")
        self.accounts.register(email="user@example.com", password="a-long-user-password")
        self.admin_token = self.accounts.login(email="admin@example.com", password="a-long-admin-password").token
        self.user_token = self.accounts.login(email="user@example.com", password="a-long-user-password").token
        self.app = create_app(account_service=self.accounts)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.database.close()
        self.env_patch.stop()
        self.tmp.cleanup()

    async def test_new_model_requires_connectivity_verification_before_enabling_and_encrypted_db_masks_secret(self):
        admin = {"Authorization": f"Bearer {self.admin_token}"}
        user = {"Authorization": f"Bearer {self.user_token}"}

        # 1. Add provider using encrypted_db (no plaintext leakage).
        provider = await self.client.post(
            "/admin/model-providers",
            headers=admin,
            json={
                "id": "siliconflow",
                "name": "硅基流动",
                "preset_type": "siliconflow",
                "default_protocol": "openai_chat_completions",
                "auth_style": "bearer",
                "base_url": "https://api.siliconflow.cn/v1",
                "extra_headers": {"X-Custom-Origin": "zhouxu"},
                "credential_mode": "encrypted_db",
                "api_key": "sk-siliconflow-secret-3f9a",
                "enabled": True,
            },
        )
        self.assertEqual(provider.status_code, 201, provider.text)
        self.assertEqual(provider.json()["api_key_hint"], "sk-****3f9a")
        self.assertNotIn("sk-siliconflow-secret-3f9a", provider.text)

        # 2. Add model with protocol_override=None (inherits provider protocol) and enabled=False.
        created = await self.client.post(
            "/admin/models",
            headers=admin,
            json={
                "profile_id": "siliconflow:qwen-72b",
                "provider_id": "siliconflow",
                "upstream_model_id": "Qwen/Qwen2.5-72B-Instruct",
                "label": "Qwen 2.5 72B",
                "family": "qwen",
                "protocol_override": None,
                "context_tokens": 131072,
                "max_output_tokens": 16384,
                "thinking_modes": ["none", "low", "high"],
                "default_thinking": "none",
                "enabled": False,
                "access_scope": "all",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["connectivity_status"], "unverified")
        self.assertEqual(created.json()["protocol"], "openai_chat_completions")

        # 3. Enabling an unverified model is rejected with 409.
        premature = await self.client.patch(
            "/admin/models/siliconflow%3Aqwen-72b",
            headers=admin,
            json={"enabled": True},
        )
        self.assertEqual(premature.status_code, 409, premature.text)
        self.assertEqual(premature.json()["detail"], "model_connectivity_unverified")

        # User cannot see unverified/disabled model.
        catalog_before = await self.client.get("/workspace/models", headers=user)
        ids_before = {m["profile_id"] for g in catalog_before.json()["groups"] for m in g["models"]}
        self.assertNotIn("siliconflow:qwen-72b", ids_before)

        # 4. Run connectivity test against mocked upstream transport.
        captured = []

        async def upstream(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": "Qwen/Qwen2.5-72B-Instruct"}, {"id": "deepseek-ai/DeepSeek-V3"}]})
            return httpx.Response(
                200,
                json={"id": "chat-1", "model": "Qwen/Qwen2.5-72B-Instruct", "choices": [{"message": {"content": "你好"}}]},
            )

        with patch("backend.infrastructure.model.health.pinned_async_transport", return_value=httpx.MockTransport(upstream)):
            discovered = await self.client.post("/admin/model-providers/siliconflow/discover", headers=admin)
            self.assertEqual(discovered.status_code, 200, discovered.text)
            self.assertTrue(discovered.json()["supported"])
            self.assertEqual(len(discovered.json()["models"]), 2)

            tested = await self.client.post("/admin/models/siliconflow%3Aqwen-72b/test", headers=admin)
            self.assertEqual(tested.status_code, 200, tested.text)
            self.assertTrue(tested.json()["ok"])
            self.assertEqual(tested.json()["connectivity_status"], "verified")

        self.assertEqual(captured[-1].headers["authorization"], "Bearer sk-siliconflow-secret-3f9a")
        self.assertEqual(captured[-1].headers["x-custom-origin"], "zhouxu")

        # 5. Admin now enables the verified model and sets it as default.
        enabled = await self.client.patch(
            "/admin/models/siliconflow%3Aqwen-72b",
            headers=admin,
            json={"enabled": True, "is_default": True},
        )
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertTrue(enabled.json()["enabled"])
        self.assertTrue(enabled.json()["is_default"])

        catalog_after = await self.client.get("/workspace/models", headers=user)
        self.assertEqual(catalog_after.json()["default_profile_id"], "siliconflow:qwen-72b")
        ids_after = {m["profile_id"] for g in catalog_after.json()["groups"] for m in g["models"]}
        self.assertIn("siliconflow:qwen-72b", ids_after)

        # 6. Rotating the provider base_url resets model to unverified/disabled on new revision,
        # while the old pinned revision stays readable.
        old_rev_id = f"siliconflow:qwen-72b:r{enabled.json()['revision']}"
        updated_provider = await self.client.patch(
            "/admin/model-providers/siliconflow",
            headers=admin,
            json={"base_url": "https://api.siliconflow.cn/v1/"},
        )
        self.assertEqual(updated_provider.status_code, 200)
        # Updating api_key resets connectivity.
        rotated_provider = await self.client.patch(
            "/admin/model-providers/siliconflow",
            headers=admin,
            json={"api_key": "sk-siliconflow-new-8888"},
        )
        self.assertEqual(rotated_provider.status_code, 200)
        self.assertEqual(rotated_provider.json()["api_key_hint"], "sk-****8888")

        directory_after_rotate = (await self.client.get("/admin/model-directory", headers=admin)).json()
        qwen = next(m for m in directory_after_rotate["models"] if m["profile_id"] == "siliconflow:qwen-72b")
        self.assertEqual(qwen["connectivity_status"], "unverified")
        self.assertFalse(qwen["enabled"])

        pinned = self.app.state.model_directory.resolve("siliconflow:qwen-72b", profile_revision_id=old_rev_id)
        self.assertEqual(pinned.profile.label, "Qwen 2.5 72B")

    async def test_soft_archive_and_forbidden_env_ref_and_missing_master_key(self):
        admin = {"Authorization": f"Bearer {self.admin_token}"}

        # Forbidden internal env var rejected.
        bad_env = await self.client.post(
            "/admin/model-providers",
            headers=admin,
            json={
                "id": "bad-env",
                "name": "越权变量",
                "default_protocol": "openai_chat_completions",
                "base_url": "https://models.example.test/v1",
                "credential_mode": "env_ref",
                "credential_ref": "OPENSEARCH_INITIAL_ADMIN_PASSWORD",
            },
        )
        self.assertEqual(bad_env.status_code, 422)

        # Unconfigured master key rejects encrypted_db without storing plaintext.
        with patch.dict(os.environ, {"ZHOUXU_ADMIN_CREDENTIAL_KEY": ""}, clear=False):
            no_master = await self.client.post(
                "/admin/model-providers",
                headers=admin,
                json={
                    "id": "no-master",
                    "name": "无主密钥",
                    "default_protocol": "openai_chat_completions",
                    "base_url": "https://models.example.test/v1",
                    "credential_mode": "encrypted_db",
                    "api_key": "sk-test-12345678",
                },
            )
            self.assertEqual(no_master.status_code, 422)
            self.assertEqual(no_master.json()["detail"], "credential_encryption_unconfigured")

        # Archiving a model hides it from active directory & workspace while keeping historical revision.
        archived = await self.client.delete("/admin/models/relay%3Agpt-4o", headers=admin)
        self.assertEqual(archived.status_code, 200, archived.text)
        active = (await self.client.get("/admin/model-directory", headers=admin)).json()
        self.assertNotIn("relay:gpt-4o", {m["profile_id"] for m in active["models"]})
        historical = self.app.state.model_directory.resolve("relay:gpt-4o", profile_revision_id="relay:gpt-4o:r1")
        self.assertEqual(historical.profile.model_id, "gpt-4o")

    async def test_streamlined_cc_switch_provider_and_model_defaults(self):
        admin = {"Authorization": f"Bearer {self.admin_token}"}
        # Create Anthropic provider with minimal fields (no id, auth_style, or credential_mode).
        created_provider = await self.client.post(
            "/admin/model-providers",
            headers=admin,
            json={
                "name": "Skye Clark 中转",
                "notes": "公司专用账号",
                "default_protocol": "anthropic_messages",
                "base_url": "https://relay.example.test:8086/v1",
                "api_key": "relay-key-abcd9999",
            },
        )
        self.assertEqual(created_provider.status_code, 201, created_provider.text)
        pdata = created_provider.json()
        self.assertEqual(pdata["notes"], "公司专用账号")
        self.assertEqual(pdata["auth_style"], "x-api-key")
        self.assertEqual(pdata["credential_mode"], "encrypted_db")
        self.assertEqual(pdata["api_key_hint"], "rela****9999")

        # Create Gemini-overridden model without passing profile_id or family.
        created_model = await self.client.post(
            "/admin/models",
            headers=admin,
            json={
                "provider_id": pdata["id"],
                "upstream_model_id": "gemini-2.5-pro",
                "label": "Gemini 2.5 Pro",
                "protocol_override": "google_generative_language",
                "context_tokens": 1000000,
                "max_output_tokens": 65536,
                "thinking_modes": ["none", "low", "high"],
                "default_thinking": "low",
            },
        )
        self.assertEqual(created_model.status_code, 201, created_model.text)
        mdata = created_model.json()
        self.assertTrue(mdata["profile_id"].startswith(f"{pdata['id']}:"))
        self.assertEqual(mdata["family"], "gemini")
        self.assertEqual(mdata["protocol"], "google_generative_language")

        # Switching provider protocol updates auth_style automatically, and updating notes keeps it.
        updated_provider = await self.client.patch(
            f"/admin/model-providers/{pdata['id']}",
            headers=admin,
            json={"notes": "生产主用中转", "default_protocol": "google_generative_language"},
        )
        self.assertEqual(updated_provider.status_code, 200, updated_provider.text)
        self.assertEqual(updated_provider.json()["notes"], "生产主用中转")
        self.assertEqual(updated_provider.json()["auth_style"], "x-goog-api-key")


if __name__ == "__main__":
    unittest.main()
