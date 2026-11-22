from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.infrastructure.model.endpoint_policy import EndpointPolicyError, validate_model_endpoint
from backend.infrastructure.model.relay_config import load_relay_config


class EndpointPolicyTests(unittest.TestCase):
    def test_public_https_endpoint_is_accepted_without_network_access(self):
        target = validate_model_endpoint(
            "https://models.example.com/v1",
            allowed_hosts={"models.example.com"},
            resolved_addresses={"8.8.8.8"},
        )
        self.assertEqual(target.host, "models.example.com")
        self.assertEqual(target.port, 443)

    def test_private_metadata_loopback_and_ipv6_addresses_are_blocked(self):
        values = [
            "https://127.0.0.1/v1",
            "https://10.1.2.3/v1",
            "https://169.254.169.254/latest/meta-data",
            "https://[::1]/v1",
        ]
        for value in values:
            with self.subTest(endpoint=value), self.assertRaises(EndpointPolicyError):
                validate_model_endpoint(value)
        with self.assertRaises(EndpointPolicyError):
            validate_model_endpoint(
                "https://models.example.com/v1",
                resolved_addresses={"192.168.1.10"},
            )

    def test_credentials_query_fragment_and_unlisted_host_are_blocked(self):
        values = [
            "https://user:password@models.example.com/v1",
            "https://models.example.com/v1?api_key=secret",
            "https://models.example.com/v1#fragment",
        ]
        for value in values:
            with self.subTest(endpoint=value), self.assertRaises(EndpointPolicyError):
                validate_model_endpoint(value)
        with self.assertRaises(EndpointPolicyError):
            validate_model_endpoint(
                "https://other.example.com/v1",
                allowed_hosts={"models.example.com"},
            )

    def test_local_endpoint_requires_explicit_opt_in(self):
        with self.assertRaises(EndpointPolicyError):
            validate_model_endpoint("https://127.0.0.1:11434/v1")
        target = validate_model_endpoint(
            "https://127.0.0.1:11434/v1", allow_local=True
        )
        self.assertEqual(target.host, "127.0.0.1")

    def test_relay_key_has_no_vendor_specific_prefix_requirement(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "relay.env"
            path.write_text(
                "https://relay.example.com\nprovider-token-without-sk-prefix\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"ZHOUXU_RELAY_ENDPOINT": "", "ZHOUXU_RELAY_API_KEY": ""},
            ):
                config = load_relay_config(path)
        self.assertEqual(
            config.key.get_secret_value(), "provider-token-without-sk-prefix"
        )


if __name__ == "__main__":
    unittest.main()
