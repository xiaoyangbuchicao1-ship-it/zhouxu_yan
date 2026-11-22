"""Small, truthful provider probes used before enabling relay profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import httpx

from backend.infrastructure.model.relay_config import RelayConfig


@dataclass(frozen=True)
class ModelProbe:
    model_id: str
    ok: bool
    status_code: int | None
    response_model: str | None = None
    error_code: str | None = None


def _v1_origin(endpoint: str) -> str:
    return endpoint if endpoint.endswith("/v1") else endpoint + "/v1"


async def probe_relay_models(
    config: RelayConfig,
    model_ids: Iterable[str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[ModelProbe]:
    """Send one minimal ``你好`` request per configured chat model.

    The response body is reduced to a model name and status.  It is never
    returned wholesale, so provider diagnostics cannot accidentally carry a
    credential or a long generated answer into logs or the UI.
    """
    origin = _v1_origin(config.endpoint)
    headers = {"Authorization": f"Bearer {config.key.get_secret_value()}"}
    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    ) as client:
        response = await client.get(f"{origin}/models")
        if not response.is_success:
            raise RuntimeError("relay_models_unavailable")
        data = response.json()
        advertised = {
            item.get("id")
            for item in data.get("data", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        results: list[ModelProbe] = []
        for model_id in model_ids:
            if model_id not in advertised:
                results.append(ModelProbe(model_id, False, 404, error_code="model_not_advertised"))
                continue
            try:
                probe = await client.post(
                    f"{origin}/chat/completions",
                    json={
                        "model": model_id,
                        "messages": [{"role": "user", "content": "你好"}],
                        "max_tokens": 32,
                        "temperature": 0,
                    },
                )
            except httpx.RequestError:
                results.append(ModelProbe(model_id, False, None, error_code="provider_unavailable"))
                continue
            if not probe.is_success:
                results.append(ModelProbe(model_id, False, probe.status_code, error_code="model_rejected"))
                continue
            body = probe.json()
            choices = body.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                results.append(ModelProbe(model_id, False, probe.status_code, error_code="invalid_response"))
                continue
            results.append(
                ModelProbe(
                    model_id=model_id,
                    ok=True,
                    status_code=probe.status_code,
                    response_model=body.get("model") if isinstance(body.get("model"), str) else None,
                )
            )
        return results


__all__ = ["ModelProbe", "probe_relay_models"]
