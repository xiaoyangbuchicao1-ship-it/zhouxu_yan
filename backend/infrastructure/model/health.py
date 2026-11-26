"""Small, truthful provider probes and model discovery used before enabling models."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping
from urllib.parse import urlparse

import httpx

from backend.infrastructure.model.endpoint_policy import (
    EndpointPolicyError,
    local_model_hosts_enabled,
    pinned_async_transport,
    validate_model_endpoint,
)
from backend.infrastructure.model.inference import infer_model_defaults
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
    context_tokens: int = 131_072
    max_output_tokens: int = 16_384
    thinking_modes: tuple[str, ...] = ("none",)
    default_thinking: str = "none"
    inferred_matched: bool = False
    inferred_source: str = "通用默认值"


@dataclass(frozen=True)
class ProviderDiscoveryResult:
    supported: bool
    models: list[DiscoveredModel]
    notice: str | None = None


@dataclass(frozen=True)
class ProviderBalanceResult:
    status: str  # "ok" | "unsupported" | "auth_failed" | "unreachable"
    supported: bool
    display: str | None = None
    currency: str | None = None
    total_balance: str | None = None
    granted_balance: str | None = None
    topped_up_balance: str | None = None
    used_amount: str | None = None
    source: str | None = None
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
        inferred = infer_model_defaults(model_id)
        ctx = inferred.context_tokens
        out = inferred.max_output_tokens
        matched = inferred.matched
        source_hint = inferred.source_hint
        if not matched:
            for key in ("context_length", "max_model_len", "inputTokenLimit", "context_window"):
                val = item.get(key)
                if isinstance(val, int) and not isinstance(val, bool) and 4_096 <= val <= 10_000_000:
                    ctx = val
                    matched = True
                    source_hint = "上游接口返回"
                    break
            for key in ("max_output_tokens", "outputTokenLimit", "max_completion_tokens"):
                val = item.get(key)
                if isinstance(val, int) and not isinstance(val, bool) and 256 <= val <= ctx:
                    out = val
                    break
        discovered.append(
            DiscoveredModel(
                id=model_id,
                family=model_family(model_id),
                label=label.strip() or model_id,
                context_tokens=ctx,
                max_output_tokens=out,
                thinking_modes=inferred.thinking_modes,
                default_thinking=inferred.default_thinking,
                inferred_matched=matched,
                inferred_source=source_hint,
            )
        )
    return ProviderDiscoveryResult(supported=True, models=discovered)


def _balance_origins(base_url: str) -> tuple[str, str]:
    clean = base_url.strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/messages", "/models"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
            break
    if ":generateContent" in clean:
        clean = clean.split("/models/", 1)[0]
    root = _VERSION_SUFFIX_RE.sub("", clean).rstrip("/")
    v1_base = clean if _VERSION_SUFFIX_RE.search(clean) else f"{root}/v1"
    return root, v1_base


def _format_money(amount: float | str, currency: str | None) -> str:
    try:
        numeric = float(amount)
        formatted = f"{numeric:.2f}"
    except (TypeError, ValueError):
        formatted = str(amount)
    code = (currency or "").upper()
    if code in {"CNY", "RMB"}:
        return f"¥{formatted}"
    if code == "USD":
        return f"${formatted}"
    return f"{formatted} {code}".strip()


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_deepseek_balance(payload: object) -> ProviderBalanceResult | None:
    if not isinstance(payload, dict):
        return None
    infos = payload.get("balance_infos")
    if not isinstance(infos, list) or not infos:
        return None
    picked = next(
        (item for item in infos if isinstance(item, dict) and str(item.get("currency", "")).upper() == "CNY"),
        None,
    )
    if picked is None:
        picked = next((item for item in infos if isinstance(item, dict)), None)
    if picked is None or picked.get("total_balance") is None:
        return None
    currency = str(picked.get("currency") or "CNY").upper()
    total = str(picked.get("total_balance"))
    granted = str(picked.get("granted_balance")) if picked.get("granted_balance") is not None else None
    topped = str(picked.get("topped_up_balance")) if picked.get("topped_up_balance") is not None else None
    return ProviderBalanceResult(
        status="ok",
        supported=True,
        display=_format_money(total, currency),
        currency=currency,
        total_balance=total,
        granted_balance=granted,
        topped_up_balance=topped,
        source="deepseek",
    )


def _parse_siliconflow_balance(payload: object) -> ProviderBalanceResult | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    raw_total = data.get("totalBalance")
    if raw_total is None:
        raw_total = data.get("balance")
    if raw_total is None:
        return None
    total = _as_float(raw_total)
    if total is None:
        return None
    charge = _as_float(data.get("chargeBalance"))
    granted = _as_float(data.get("balance"))
    return ProviderBalanceResult(
        status="ok",
        supported=True,
        display=_format_money(total, "CNY"),
        currency="CNY",
        total_balance=f"{total:.2f}",
        granted_balance=f"{granted:.2f}" if granted is not None else None,
        topped_up_balance=f"{charge:.2f}" if charge is not None else None,
        source="siliconflow",
    )


def _parse_openrouter_balance(payload: object) -> ProviderBalanceResult | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    total_credits = _as_float(data.get("total_credits"))
    total_usage = _as_float(data.get("total_usage"))
    if total_credits is None:
        return None
    used = total_usage or 0.0
    remaining = max(0.0, total_credits - used)
    return ProviderBalanceResult(
        status="ok",
        supported=True,
        display=_format_money(remaining, "USD"),
        currency="USD",
        total_balance=f"{remaining:.2f}",
        used_amount=f"{used:.2f}",
        source="openrouter",
    )


def _parse_credit_grants_balance(payload: object) -> ProviderBalanceResult | None:
    if not isinstance(payload, dict):
        return None
    available = _as_float(payload.get("total_available"))
    granted = _as_float(payload.get("total_granted"))
    used = _as_float(payload.get("total_used"))
    if available is None and granted is None:
        return None
    if available is None and granted is not None:
        available = max(0.0, granted - (used or 0.0))
    return ProviderBalanceResult(
        status="ok",
        supported=True,
        display=_format_money(available or 0.0, "USD"),
        currency="USD",
        total_balance=f"{(available or 0.0):.2f}",
        granted_balance=f"{granted:.2f}" if granted is not None else None,
        used_amount=f"{used:.2f}" if used is not None else None,
        source="credit_grants",
    )


def _is_json_response(response: httpx.Response) -> bool:
    ctype = response.headers.get("content-type", "").lower()
    return "json" in ctype


async def probe_provider_balance(
    base_url: str,
    preset_type: str,
    auth_style: str,
    api_key: str,
    *,
    extra_headers: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ProviderBalanceResult:
    """Query account balance from providers/relays that expose a JSON balance API.

    Never fakes a balance: relays without a balance API (or returning SPA HTML)
    cleanly resolve to ``status="unsupported"``.
    """
    if auth_style == "none":
        return ProviderBalanceResult(
            status="unsupported",
            supported=False,
            notice="本地无密钥服务不需要查询余额。",
        )

    allow_local = local_model_hosts_enabled()
    root, v1_base = _balance_origins(base_url)
    host = (urlparse(root).hostname or "").lower()
    preset = (preset_type or "").strip().lower()

    headers = _build_probe_headers(auth_style, api_key, "openai_chat_completions", extra_headers)
    resolved_transport = transport or pinned_async_transport(allow_local=allow_local)

    # Order probes so known providers hit their exact official endpoint first.
    probes: list[tuple[str, str]] = []
    if preset == "deepseek" or "deepseek" in host:
        probes.append(("deepseek", f"{root}/user/balance"))
    elif preset == "siliconflow" or "siliconflow" in host:
        probes.append(("siliconflow", f"{v1_base}/user/info"))
    elif "openrouter" in host:
        probes.append(("openrouter", f"{v1_base}/credits"))
    else:
        probes.extend([
            ("oneapi_sub", f"{v1_base}/dashboard/billing/subscription"),
            ("credit_grants", f"{v1_base}/dashboard/billing/credit_grants"),
            ("credit_grants_root", f"{root}/dashboard/billing/credit_grants"),
            ("siliconflow", f"{v1_base}/user/info"),
            ("deepseek", f"{root}/user/balance"),
            ("openrouter", f"{v1_base}/credits"),
        ])

    saw_auth_error = False
    saw_network_error = False
    strict_primary = len(probes) == 1

    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(10.0),
        follow_redirects=False,
        trust_env=False,
        transport=resolved_transport,
    ) as client:
        for kind, url in probes:
            validate_model_endpoint(url, allow_local=allow_local)
            try:
                response = await client.get(url)
            except httpx.RequestError:
                saw_network_error = True
                if strict_primary:
                    return ProviderBalanceResult(
                        status="unreachable",
                        supported=False,
                        notice="无法连接到服务商余额接口，请检查地址或网络。",
                    )
                continue

            if response.status_code in {401, 403}:
                saw_auth_error = True
                if strict_primary:
                    return ProviderBalanceResult(
                        status="auth_failed",
                        supported=False,
                        notice=f"上游鉴权失败（HTTP {response.status_code}），请检查 API Key。",
                    )
                continue

            if not response.is_success or not _is_json_response(response):
                continue

            try:
                payload = response.json()
            except ValueError:
                continue

            if kind == "deepseek":
                parsed = _parse_deepseek_balance(payload)
                if parsed is not None:
                    return parsed
            elif kind == "siliconflow":
                parsed = _parse_siliconflow_balance(payload)
                if parsed is not None:
                    return parsed
            elif kind == "openrouter":
                parsed = _parse_openrouter_balance(payload)
                if parsed is not None:
                    return parsed
            elif kind in {"credit_grants", "credit_grants_root"}:
                parsed = _parse_credit_grants_balance(payload)
                if parsed is not None:
                    return parsed
            elif kind == "oneapi_sub" and isinstance(payload, dict):
                hard_limit = _as_float(payload.get("hard_limit_usd"))
                if hard_limit is None:
                    hard_limit = _as_float(payload.get("system_hard_limit_usd"))
                if hard_limit is None:
                    continue
                if hard_limit >= 100_000_000:
                    return ProviderBalanceResult(
                        status="ok",
                        supported=True,
                        display="无限额度",
                        currency="USD",
                        source="oneapi",
                    )
                used_usd = 0.0
                now = datetime.now(timezone.utc)
                start = (now - timedelta(days=90)).strftime("%Y-%m-%d")
                end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
                usage_base_url = f"{v1_base}/dashboard/billing/usage"
                try:
                    validate_model_endpoint(usage_base_url, allow_local=allow_local)
                    usage_resp = await client.get(
                        usage_base_url,
                        params={"start_date": start, "end_date": end},
                    )
                    if usage_resp.is_success and _is_json_response(usage_resp):
                        usage_data = usage_resp.json()
                        if isinstance(usage_data, dict):
                            raw_usage = _as_float(usage_data.get("total_usage"))
                            if raw_usage is not None:
                                used_usd = raw_usage / 100.0
                except Exception:
                    used_usd = 0.0
                remaining = max(0.0, hard_limit - used_usd)
                return ProviderBalanceResult(
                    status="ok",
                    supported=True,
                    display=_format_money(remaining, "USD"),
                    currency="USD",
                    total_balance=f"{remaining:.2f}",
                    granted_balance=f"{hard_limit:.2f}",
                    used_amount=f"{used_usd:.2f}",
                    source="oneapi",
                )

    if saw_auth_error:
        return ProviderBalanceResult(
            status="auth_failed",
            supported=False,
            notice="上游鉴权失败，请检查 API Key 是否有余额查询权限。",
        )
    if saw_network_error:
        return ProviderBalanceResult(
            status="unreachable",
            supported=False,
            notice="无法连接到服务商余额接口，请检查地址或网络。",
        )
    return ProviderBalanceResult(
        status="unsupported",
        supported=False,
        notice="该服务商未开放余额查询，请前往服务商控制台查看。",
    )


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
    except EndpointPolicyError as exc:
        return ConnectivityProbeResult(
            ok=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            error_code=str(exc),
        )
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
    "ProviderBalanceResult",
    "ProviderDiscoveryResult",
    "discover_provider_models",
    "probe_model_endpoint",
    "probe_provider_balance",
    "probe_relay_models",
]
