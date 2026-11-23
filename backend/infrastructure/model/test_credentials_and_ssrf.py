from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from backend.infrastructure.model.credentials import (
    credential_encryption_ready,
    decrypt_api_key,
    encrypt_api_key,
    is_allowed_env_ref,
)
from backend.infrastructure.model.endpoint_policy import (
    EndpointPolicyError,
    PinnedAsyncNetworkBackend,
    validate_model_endpoint,
)
from backend.infrastructure.model.relay_config import relay_endpoint_for_protocol


class CredentialsAndSsrfTests(unittest.TestCase):
    def test_fernet_encryption_roundtrip_and_hint_masking_without_sk_prefix_gate(self):
        key = Fernet.generate_key().decode("ascii")
        with patch.dict(os.environ, {"ZHOUXU_ADMIN_CREDENTIAL_KEY": key}, clear=False):
            self.assertTrue(credential_encryption_ready())
            ciphertext, hint = encrypt_api_key("AIzaSyDummyGoogleKey9876")
            self.assertNotIn("AIzaSyDummyGoogleKey9876", ciphertext)
            self.assertEqual(hint, "AIza****9876")
            self.assertEqual(decrypt_api_key(ciphertext).get_secret_value(), "AIzaSyDummyGoogleKey9876")

            _, sk_hint = encrypt_api_key("sk-siliconflow-1234567890abcd")
            self.assertEqual(sk_hint, "sk-****abcd")

        with patch.dict(os.environ, {"ZHOUXU_ADMIN_CREDENTIAL_KEY": ""}, clear=False):
            self.assertFalse(credential_encryption_ready())
            with self.assertRaisesRegex(ValueError, "credential_encryption_unconfigured"):
                encrypt_api_key("sk-12345678")

    def test_env_ref_whitelist_blocks_internal_secrets_and_allows_model_keys(self):
        for forbidden in (
            "OPENSEARCH_INITIAL_ADMIN_PASSWORD",
            "ZHOUXU_ADMIN_PASSWORD",
            "ZHOUXU_ADMIN_CREDENTIAL_KEY",
            "PATH",
        ):
            self.assertFalse(is_allowed_env_ref(forbidden), forbidden)
        for allowed in (
            "DEEPSEEK_API_KEY",
            "SILICONFLOW_API_KEY",
            "ZHOUXU_MODEL_KEY_CUSTOM_1",
            "ZHOUXU_PROVIDER_KEY_OLLAMA",
        ):
            self.assertTrue(is_allowed_env_ref(allowed), allowed)

    def test_local_http_allowed_only_for_local_hosts_and_metadata_always_blocked(self):
        with self.assertRaises(EndpointPolicyError):
            validate_model_endpoint("http://127.0.0.1:11434/v1")
        local = validate_model_endpoint("http://127.0.0.1:11434/v1", allow_local=True)
        self.assertEqual(local.scheme, "http")
        self.assertEqual(local.port, 11434)

        with self.assertRaises(EndpointPolicyError):
            validate_model_endpoint("http://api.siliconflow.cn/v1", allow_local=True)
        with self.assertRaises(EndpointPolicyError):
            validate_model_endpoint("http://169.254.169.254/latest/meta-data", allow_local=True)
        with self.assertRaises(EndpointPolicyError):
            validate_model_endpoint("https://169.254.169.254/v1", allow_local=True)

    def test_endpoint_mapping_preserves_versioned_paths(self):
        self.assertEqual(
            relay_endpoint_for_protocol("https://api.siliconflow.cn/v1", "openai_chat_completions"),
            "https://api.siliconflow.cn/v1/chat/completions",
        )
        self.assertEqual(
            relay_endpoint_for_protocol("https://ark.cn-beijing.volces.com/api/v3", "openai_chat_completions"),
            "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        )
        self.assertEqual(
            relay_endpoint_for_protocol("https://generativelanguage.googleapis.com/v1beta", "google_generative_language"),
            "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        )
        self.assertEqual(
            relay_endpoint_for_protocol("http://127.0.0.1:11434", "openai_chat_completions"),
            "http://127.0.0.1:11434/v1/chat/completions",
        )


class PinnedDnsBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_pinned_backend_blocks_dns_rebinding_to_private_or_metadata_ips(self):
        calls = []

        class _FakeInner:
            async def connect_tcp(self, host, port, **kwargs):
                calls.append((host, port))
                return object()

        backend = PinnedAsyncNetworkBackend(
            inner=_FakeInner(),
            resolver=lambda host, port: ["169.254.169.254"],
        )
        with self.assertRaises(EndpointPolicyError):
            await backend.connect_tcp("evil.example.com", 443)
        self.assertEqual(calls, [])

        safe_backend = PinnedAsyncNetworkBackend(
            inner=_FakeInner(),
            resolver=lambda host, port: ["93.184.216.34"],
        )
        await safe_backend.connect_tcp("models.example.com", 443)
        self.assertEqual(calls, [("93.184.216.34", 443)])


if __name__ == "__main__":
    unittest.main()
