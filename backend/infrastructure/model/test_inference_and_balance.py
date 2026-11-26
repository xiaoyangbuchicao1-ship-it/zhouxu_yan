"""Unit & HTTP tests for automatic model capability inference and provider balance probing."""

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
from backend.infrastructure.model.health import discover_provider_models, probe_provider_balance
from backend.infrastructure.model.inference import infer_model_defaults
from backend.interfaces.api.app import create_app


class ModelInferenceUnitTests(unittest.TestCase):
    def test_curated_deepseek_models(self):
        flash = infer_model_defaults("deepseek-flash")
        self.assertTrue(flash.matched)
        self.assertEqual(flash.context_tokens, 1_000_000)
        self.assertEqual(flash.max_output_tokens, 393_216)
        self.assertIn("high", flash.thinking_modes)

        pro = infer_model_defaults("deepseek-v4-pro")
        self.assertTrue(pro.matched)
        self.assertEqual(pro.context_tokens, 1_000_000)

    def test_mainstream_reasoning_and_chat_patterns(self):
        r1 = infer_model_defaults("Pro/deepseek-ai/DeepSeek-R1")
        self.assertTrue(r1.matched)
        self.assertEqual(r1.context_tokens, 131_072)
        self.assertEqual(r1.max_output_tokens, 32_768)
        self.assertEqual(r1.default_thinking, "high")

        claude = infer_model_defaults("anthropic/claude-sonnet-4-5")
        self.assertTrue(claude.matched)
        self.assertEqual(claude.context_tokens, 200_000)
        self.assertIn("high", claude.thinking_modes)

        gemini = infer_model_defaults("gemini-2.5-pro")
        self.assertTrue(gemini.matched)
        self.assertEqual(gemini.context_tokens, 1_048_576)
        self.assertIn("high", gemini.thinking_modes)

        o3 = infer_model_defaults("o3-mini")
        self.assertTrue(o3.matched)
        self.assertEqual(o3.default_thinking, "medium")

        qwen = infer_model_defaults("Qwen/QwQ-32B")
        self.assertTrue(qwen.matched)
        self.assertIn("high", qwen.thinking_modes)

    def test_unknown_model_falls_back_to_safe_default(self):
        unknown = infer_model_defaults("acme/custom-legal-v1")
        self.assertFalse(unknown.matched)
        self.assertEqual(unknown.context_tokens, 131_072)
        self.assertEqual(unknown.max_output_tokens, 16_384)
        self.assertEqual(unknown.thinking_modes, ("none",))
        self.assertEqual(unknown.default_thinking, "none")


class ProviderBalanceProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_deepseek_balance(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/user/balance")
            return httpx.Response(
                200,
                json={
                    "is_available": True,
                    "balance_infos": [
                        {
                            "currency": "CNY",
                            "total_balance": "38.79",
                            "granted_balance": "0.00",
                            "topped_up_balance": "38.79",
                        }
                    ],
                },
            )

        res = await probe_provider_balance(
            "https://api.deepseek.com",
            "deepseek",
            "bearer",
            "sk-test",
            transport=httpx.MockTransport(handler),
        )
        self.assertEqual(res.status, "ok")
        self.assertTrue(res.supported)
        self.assertEqual(res.display, "¥38.79")
        self.assertEqual(res.currency, "CNY")

    async def test_siliconflow_balance(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/user/info")
            return httpx.Response(
                200,
                json={"data": {"balance": "5.50", "chargeBalance": "120.00", "totalBalance": "125.50"}},
            )

        res = await probe_provider_balance(
            "https://api.siliconflow.cn/v1",
            "siliconflow",
            "bearer",
            "sk-sf",
            transport=httpx.MockTransport(handler),
        )
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.display, "¥125.50")

    async def test_oneapi_subscription_and_usage(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/dashboard/billing/subscription":
                return httpx.Response(200, json={"hard_limit_usd": 100.0})
            if request.url.path == "/v1/dashboard/billing/usage":
                return httpx.Response(200, json={"total_usage": 1500.0})  # $15.00 in cents
            return httpx.Response(404, json={"error": "not found"})

        res = await probe_provider_balance(
            "https://relay.example.test/v1",
            "custom",
            "bearer",
            "sk-relay",
            transport=httpx.MockTransport(handler),
        )
        self.assertEqual(res.status, "ok")
        self.assertEqual(res.display, "$85.00")
        self.assertEqual(res.used_amount, "15.00")

    async def test_html_spa_fallback_treated_as_unsupported(self):
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<!doctype html><html><body>SPA</body></html>",
            )

        res = await probe_provider_balance(
            "https://custom-relay.example.test",
            "custom",
            "bearer",
            "sk-relay",
            transport=httpx.MockTransport(handler),
        )
        self.assertEqual(res.status, "unsupported")
        self.assertFalse(res.supported)
        self.assertIsNotNone(res.notice)

    async def test_auth_failed_on_401(self):
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "invalid api key"})

        res = await probe_provider_balance(
            "https://api.deepseek.com",
            "deepseek",
            "bearer",
            "sk-bad",
            transport=httpx.MockTransport(handler),
        )
        self.assertEqual(res.status, "auth_failed")
        self.assertFalse(res.supported)

    async def test_local_no_key_provider(self):
        res = await probe_provider_balance(
            "http://127.0.0.1:11434/v1",
            "ollama",
            "none",
            "",
        )
        self.assertEqual(res.status, "unsupported")
        self.assertFalse(res.supported)

    async def test_discover_attaches_inferred_defaults(self):
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "deepseek-reasoner"},
                        {"id": "custom/my-model", "context_length": 65536},
                    ]
                },
            )

        res = await discover_provider_models(
            "https://relay.example.test/v1",
            "openai_chat_completions",
            "bearer",
            "sk-test",
            transport=httpx.MockTransport(handler),
        )
        self.assertTrue(res.supported)
        self.assertEqual(len(res.models), 2)
        self.assertEqual(res.models[0].default_thinking, "high")
        self.assertEqual(res.models[1].context_tokens, 65536)
        self.assertEqual(res.models[1].inferred_source, "上游接口返回")


class AdminBalanceAndInferenceHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key_name = "ZHOUXU_MODEL_KEY_BALANCE_TEST"
        self.prev_key = os.environ.get(self.key_name)
        os.environ[self.key_name] = "sk-balance-test"
        settings = DatabaseSettings(url=f"sqlite:///{Path(self.tmp.name) / 'bal.sqlite3'}")
        self.database = Database(settings)
        self.database.initialize()
        self.accounts = AccountService(self.database, settings, AuthSettings(password_min_length=8))
        self.accounts.ensure_admin(email="admin@example.com", password="a-long-admin-password")
        self.admin_login = self.accounts.login(email="admin@example.com", password="a-long-admin-password")
        self.app = create_app(account_service=self.accounts)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )
        self.headers = {"Authorization": f"Bearer {self.admin_login.token}"}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.database.close()
        if self.prev_key is None:
            os.environ.pop(self.key_name, None)
        else:
            os.environ[self.key_name] = self.prev_key
        self.tmp.cleanup()

    async def test_infer_and_create_model_without_manual_context_or_thinking(self):
        prov = await self.client.post(
            "/admin/model-providers",
            headers=self.headers,
            json={
                "id": "sf-test",
                "name": "硅基流动测试",
                "preset_type": "siliconflow",
                "default_protocol": "openai_chat_completions",
                "auth_style": "bearer",
                "base_url": "https://api.siliconflow.cn/v1",
                "credential_mode": "env_ref",
                "credential_ref": self.key_name,
            },
        )
        self.assertEqual(prov.status_code, 201, prov.text)

        inferred = await self.client.get(
            "/admin/models/infer",
            params={"model_id": "Pro/deepseek-ai/DeepSeek-R1"},
            headers=self.headers,
        )
        self.assertEqual(inferred.status_code, 200, inferred.text)
        body = inferred.json()
        self.assertTrue(body["matched"])
        self.assertEqual(body["default_thinking"], "high")
        self.assertEqual(body["max_output_tokens"], 32768)

        created = await self.client.post(
            "/admin/models",
            headers=self.headers,
            json={
                "provider_id": "sf-test",
                "upstream_model_id": "Pro/deepseek-ai/DeepSeek-R1",
                "label": "DeepSeek R1",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        model = created.json()
        self.assertEqual(model["context_tokens"], 131072)
        self.assertEqual(model["max_output_tokens"], 32768)
        self.assertEqual(model["default_thinking"], "high")

        async def sf_handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"totalBalance": "42.10", "chargeBalance": "42.10"}})

        with patch(
            "backend.infrastructure.model.health.pinned_async_transport",
            return_value=httpx.MockTransport(sf_handler),
        ):
            bal = await self.client.get("/admin/model-providers/sf-test/balance", headers=self.headers)
        self.assertEqual(bal.status_code, 200, bal.text)
        bal_json = bal.json()
        self.assertEqual(bal_json["status"], "ok")
        self.assertEqual(bal_json["display"], "¥42.10")


if __name__ == "__main__":
    unittest.main()
