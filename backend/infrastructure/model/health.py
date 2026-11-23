"""Small, truthful provider probes and model discovery used before enabling models."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Iterable, Mapping

import httpx

from backend.infrastructure.model.endpoint_policy import (
    EndpointPolicyError,
    local_model_hosts_enabled,
    pinned_async_transport,
    validate_model_endpoint,
)
from backend.infrastructure.model.registry import model_family
from backend.infrastructure.model.relay_config import RelayConfig, relay_endpoint_for_protocol

_VERSION_SUFFIX_RE = re.compile(r"(?:/api)?/v\d+(?:beta\d*|alpha\d*)?$")
_BLOCKED_HEADERS = frozenset({
    "authorization",
    "x-api-key",
    "x-goog-api-key",
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "cookie",
    "proxy-authorization",
})


@dataclass(frozen=True)
class ModelProbe:
    model_id: str
    ok: bool
    status_code: int | None
    response_model: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class DiscoveredModel:
    id: str
    family: str
    label: str


@dataclass(frozen=True)
class ProviderDiscoveryResult:
    supported: bool
    models: list[DiscoveredModel]
    notice: str | None = None


@dataclass(frozen=True)
class ConnectivityProbeResult:
    ok: bool
    latency_ms: int | None = None
    http_status: int | None = None
    error_code: str | None = None


def _v1_origin(endpoint: str) -> str:
    return endpoint if endpoint.endswith("/v1") else endpoint + "/v1"


def _build_probe_headers(
    auth_style: str,
    api_key: str,
    protocol: str,
    extra_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if protocol == "anthropic_messages":
        headers["anthropic-version"] = "2023-06-01"
    if extra_headers:
        for name, value in extra_headers.items():
            if name.lower() in _BLOCKED_HEADERS:
                raise ValueError("model_extra_header_forbidden")
            headers[name] = value
    if auth_style == "bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    elif auth_style == "x-api-key":
        headers["x-api-key"] = api_key
    elif auth_style == "x-goog-api-key":
        headers["x-goog-api-key"] = api_key
    elif auth_style != "none":
        raise ValueError("model_auth_style_invalid")
    return headers


def _discovery_models_url(base_url: str, protocol: str) -> str:
    clean = base_url.strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/messages", "/models"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
            break
    if ":generateContent" in clean:
        clean = clean.split("/models/", 1)[0]
    if _VERSION_SUFFIX_RE.search(clean):
        return f"{clean}/models"
    if protocol == "google_generative_language":
        return f"{clean}/v1beta/models"
    return f"{clean}/v1/models"


def _probe_target_url(base_url: str, protocol: str, model_id: str) -> str:
    clean = base_url.strip().rstrip("/")
    if any(clean.endswith(suffix) for suffix in ("/chat/completions", "/responses", "/messages")):
        return clean
    if ":generateContent" in clean:
        return clean
    return relay_endpoint_for_protocol(clean, protocol).format(model=model_id)


def _probe_payload(protocol: str, model_id: str) -> dict:
    if protocol == "openai_responses":
        return {
            "model": model_id,
            "input": "你好",
            "max_output_tokens": 16,
        }
    if protocol == "anthropic_messages":
        return {
            "model": model_id,
            "messages": [{"role": "user", "content": "你好"}],
            "max_tokens": 16,
        }
    if protocol == "google_generative_language":
        return {
            "contents": [{"role": "user", "parts": [{"text": "你好"}]}],
            "generationConfig": {"maxOutputTokens": 16},
        }
    return {
        "model": model_id,
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 16,
        "temperature": 0,
    }


def _valid_probe_body(protocol: str, body: object) -> bool:
    if not isinstance(body, dict):
        return False
    if protocol in {"openai_chat_completions", "deepseek_chat_completions"}:
        choices = body.get("choices")
        return isinstance(choices, list) and len(choices) > 0 and isinstance(choices[0], dict)
    if protocol == "openai_responses":
        return "output" in body or "output_text" in body or isinstance(body.get("id"), str)
    if protocol == "anthropic_messages":
        content = body.get("content")
        return (isinstance(content, list) and len(content) > 0) or isinstance(body.get("id"), str)
    if protocol == "google_generative_language":
        candidates = body.get("candidates")
        return isinstance(candidates, list) and len(candidates) > 0 and isinstance(candidates[0], dict)
    return False


async def discover_provider_models(
    base_url: str,
    protocol: str,
    auth_style: str,
    api_key: str,
    *,
    extra_headers: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ProviderDiscoveryResult:
    """Query the provider's /models catalog safely using the pinned transport."""

    allow_local = local_model_hosts_enabled()
    url = _discovery_models_url(base_url, protocol)
    validate_model_endpoint(url, allow_local=allow_local)
    headers = _build_probe_headers(auth_style, api_key, protocol, extra_headers)
    resolved_transport = transport or pinned_async_transport(allow_local=allow_local)
    try:
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(20.0),
            follow_redirects=False,
            trust_env=False,
            transport=resolved_transport,
        ) as client:
            response = await client.get(url)
    except httpx.RequestError:
        return ProviderDiscoveryResult(
            supported=False,
            models=[],
            notice="无法连接到上游模型列表接口，请检查地址或网络。",
        )

    if response.status_code in {404, 405, 501}:
        return ProviderDiscoveryResult(
            supported=False,
            models=[],
            notice="该服务商暂不支持自动拉取模型列表，请手动填写模型 ID。",
        )
    if response.status_code in {401, 403}:
        return ProviderDiscoveryResult(
            supported=False,
            models=[],
            notice=f"上游鉴权失败（HTTP {response.status_code}），请检查密钥与认证方式。",
        )
    if not response.is_success:
        return ProviderDiscoveryResult(
            supported=False,
            models=[],
            notice=f"上游模型列表接口返回异常（HTTP {response.status_code}）。",
        )

    try:
        payload = response.json()
    except ValueError:
        return ProviderDiscoveryResult(
            supported=False,
            models=[],
            notice="上游模型列表返回内容不是有效 JSON，请手动添加模型。",
        )

    raw_items: list[dict] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            raw_items = [item for item in payload["data"] if isinstance(item, dict)]
        elif isinstance(payload.get("models"), list):
            raw_items = [item for item in payload["models"] if isinstance(item, dict)]
    discovered: list[DiscoveredModel] = []
    seen: set[str] = set()
    for item in raw_items:
        raw_id = item.get("id") or item.get("name")
        if not isinstance(raw_id, str) or not raw_id.strip():
            continue
        model_id = raw_id.strip()
        if model_id.startswith("models/"):
            model_id = model_id.removeprefix("models/")
        if model_id in seen:
            continue
        seen.add(model_id)
        label = item.get("displayName") if isinstance(item.get("displayName"), str) else model_id
        discovered.append(
            DiscoveredModel(
                id=model_id,
                family=model_family(model_id),
                label=label.strip() or model_id,
            )
        )
    return ProviderDiscoveryResult(supported=True, models=discovered)


async def probe_model_endpoint(
    base_url: str,
    protocol: str,
    model_id: str,
    auth_style: str,
    api_key: str,
    *,
    extra_headers: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ConnectivityProbeResult:
    """Execute one lightweight '你好' probe against a concrete model endpoint."""

    allow_local = local_model_hosts_enabled()
    url = _probe_target_url(base_url, protocol, model_id)
    try:
        validate_model_endpoint(url, allow_local=allow_local)
    except EndpointPolicyError as exc:
        return ConnectivityProbeResult(ok=False, error_code=str(exc))

    headers = _build_probe_headers(auth_style, api_key, protocol, extra_headers)
    payload = _probe_payload(protocol, model_id)
    resolved_transport = transport or pinned_async_transport(allow_local=allow_local)
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(30.0),
            follow_redirects=False,
            trust_env=False,
            transport=resolved_transport,
        ) as client:
            response = await client.post(url, json=payload)
    except httpx.TimeoutException:
        return ConnectivityProbeResult(
            ok=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            error_code="provider_timeout",
        )
    except httpx.RequestError:
        return ConnectivityProbeResult(
            ok=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            error_code="provider_unreachable",
        )

    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    if not response.is_success:
        code = (
            "provider_auth_failed"
            if response.status_code in {401, 403}
            else "provider_rate_limited"
            if response.status_code == 429
            else "model_not_found"
            if response.status_code == 404
            else f"http_{response.status_code}"
        )
        return ConnectivityProbeResult(
            ok=False,
            latency_ms=latency_ms,
            http_status=response.status_code,
            error_code=code,
        )

    try:
        body = response.json()
    except ValueError:
        return ConnectivityProbeResult(
            ok=False,
            latency_ms=latency_ms,
            http_status=response.status_code,
            error_code="invalid_response",
        )

    if not _valid_probe_body(protocol, body):
        return ConnectivityProbeResult(
            ok=False,
            latency_ms=latency_ms,
            http_status=response.status_code,
            error_code="invalid_response",
        )

    return ConnectivityProbeResult(
        ok=True,
        latency_ms=latency_ms,
        http_status=response.status_code,
    )


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


__all__ = [
    "ConnectivityProbeResult",
    "DiscoveredModel",
    "ModelProbe",
    "ProviderDiscoveryResult",
    "discover_provider_models",
    "probe_model_endpoint",
    "probe_relay_models",
]
