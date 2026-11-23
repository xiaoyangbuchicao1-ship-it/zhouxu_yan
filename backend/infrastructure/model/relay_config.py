"""Read the relay address and key without ever exposing the key."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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


_VERSIONED_PATH_RE = re.compile(r"/(?:v\d+(?:alpha|beta)?|api/v\d+|openai)$", re.IGNORECASE)


def relay_endpoint_for_protocol(base: str, protocol: str) -> str:
    """Turn a provider base URL into the wire endpoint for one protocol."""

    cleaned = base.strip().rstrip("/")
    parsed = urlsplit(cleaned)
    path = parsed.path.rstrip("/")

    if protocol == "google_generative_language":
        if ":generateContent" in path:
            return cleaned
        if path.endswith("/v1beta"):
            next_path = f"{path}/models/{{model}}:generateContent"
        elif path.endswith("/v1"):
            next_path = f"{path[:-3]}/v1beta/models/{{model}}:generateContent"
        elif path:
            next_path = f"{path}/models/{{model}}:generateContent"
        else:
            next_path = "/v1beta/models/{model}:generateContent"
        return urlunsplit((parsed.scheme, parsed.netloc, next_path, "", ""))

    suffix = (
        "/responses"
        if protocol == "openai_responses"
        else "/messages"
        if protocol == "anthropic_messages"
        else "/chat/completions"
    )
    if path.endswith(suffix):
        return cleaned
    if _VERSIONED_PATH_RE.search(path):
        next_path = f"{path}{suffix}"
    elif path:
        next_path = f"{path}/v1{suffix}"
    else:
        next_path = f"/v1{suffix}"
    return urlunsplit((parsed.scheme, parsed.netloc, next_path, "", ""))


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
        validate_model_endpoint(
            endpoint,
            allow_local=os.environ.get("ZHOUXU_ALLOW_LOCAL_MODEL_HOSTS") == "1",
        )
    except EndpointPolicyError as exc:
        # Keep the existing config loader's ValueError boundary while exposing
        # the concrete policy reason to diagnostics.
        raise ValueError(str(exc)) from exc
    return RelayConfig(endpoint=endpoint, key=SecretStr(key), source=source)


__all__ = ["RelayConfig", "load_relay_config", "relay_endpoint_for_protocol"]
