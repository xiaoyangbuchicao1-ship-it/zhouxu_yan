import unittest

import httpx
from pydantic import SecretStr

from backend.infrastructure.model.health import probe_relay_models
from backend.infrastructure.model.relay_config import RelayConfig


class RelayProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_sends_one_hello_per_advertised_model_and_redacts_details(self):
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/v1/models":
                return httpx.Response(
                    200,
                    json={"data": [{"id": "gpt-6.1-sol"}, {"id": "gpt-6.1-terra"}]},
                )
            return httpx.Response(
                200,
                json={"id": "chatcmpl-1", "model": "gpt-6.1-sol", "choices": [{"message": {"content": "你好"}}]},
            )

        results = await probe_relay_models(
            RelayConfig("https://relay.example", SecretStr("sk-test"), "test"),
            ["gpt-6.1-sol", "gpt-6.1-terra"],
            transport=httpx.MockTransport(handler),
        )

        self.assertEqual([item.ok for item in results], [True, True])
        self.assertEqual(len(requests), 3)
        posted = requests[1].read().decode()
        self.assertIn("你好", posted)
        self.assertNotIn("sk-test", " ".join(str(item) for item in results))

    async def test_unadvertised_model_is_not_called(self):
        calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == "/v1/models":
                return httpx.Response(200, json={"data": [{"id": "gpt-6.1-sol"}]})
            return httpx.Response(
                200,
                json={"model": "gpt-6.1-sol", "choices": [{"message": {"content": "你好"}}]},
            )

        results = await probe_relay_models(
            RelayConfig("https://relay.example", SecretStr("sk-test"), "test"),
            ["gpt-6.1-sol", "missing"],
            transport=httpx.MockTransport(handler),
        )
        self.assertEqual([item.error_code for item in results], [None, "model_not_advertised"])
        self.assertEqual(calls, ["/v1/models", "/v1/chat/completions"])


if __name__ == "__main__":
    unittest.main()
