"""Read the relay address and key without ever exposing the key."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

from backend.infrastructure.model.endpoint_policy import (
    EndpointPolicyError,
    validate_model_endpoint,
)


@dataclass(frozen=True)
class RelayConfig:
    endpoint: str
    key: SecretStr
    source: str


def relay_endpoint_for_protocol(base: str, protocol: str) -> str:
    """Turn the configured relay origin into the endpoint for one wire protocol."""
    if base.endswith("/v1"):
        origin = base[:-3].rstrip("/")
    else:
        origin = base.rstrip("/")
    if protocol == "openai_responses":
        return origin + "/v1/responses"
    if protocol == "anthropic_messages":
        return origin + "/v1/messages"
    if protocol == "google_generative_language":
        return origin + "/v1beta/models/{model}:generateContent"
    return origin + "/v1/chat/completions"


def _configured_path() -> Path | None:
    value = os.environ.get("ZHOUXU_RELAY_CREDENTIAL_FILE", "").strip()
    return Path(value) if value else None


def load_relay_config(path: str | Path | None = None) -> RelayConfig:
    """Load ``endpoint`` and ``key`` from env or a two-line credential file.

    The file format used by the local relay note is deliberately parsed as
    data, never executed: first non-empty line is the HTTPS origin and second
    non-empty line is the bearer key.  Environment variables take precedence.
    """
    endpoint = os.environ.get("ZHOUXU_RELAY_ENDPOINT", "").strip().rstrip("/")
    key = os.environ.get("ZHOUXU_RELAY_API_KEY", "").strip()
    source = "environment"
    selected = Path(path) if path is not None else _configured_path()
    if selected is not None and selected.is_file():
        lines = [line.strip() for line in selected.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        if not endpoint and lines:
            endpoint = lines[0].rstrip("/")
        if not key and len(lines) > 1:
            key = lines[1]
        source = str(selected)
    if not endpoint or not key:
        raise ValueError("relay_credentials_missing")
    try:
        validate_model_endpoint(endpoint)
    except EndpointPolicyError as exc:
        # Keep the existing config loader's ValueError boundary while exposing
        # the concrete policy reason to diagnostics.
        raise ValueError(str(exc)) from exc
    return RelayConfig(endpoint=endpoint, key=SecretStr(key), source=source)


__all__ = ["RelayConfig", "load_relay_config", "relay_endpoint_for_protocol"]
