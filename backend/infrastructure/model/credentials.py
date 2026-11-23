"""Credential encryption vault and deployment env-ref whitelist."""

from __future__ import annotations

import base64
import hashlib
import os
import re

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

ALLOWED_ENV_REF_NAMES = frozenset({
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_KEY",
    "ZHOUXU_RELAY_API_KEY",
    "RELAY_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "SILICONFLOW_API_KEY",
    "VOLCENGINE_API_KEY",
    "ARK_API_KEY",
    "OPENROUTER_API_KEY",
    "ZHIPU_API_KEY",
    "MOONSHOT_API_KEY",
    "KIMI_API_KEY",
    "QWEN_API_KEY",
    "DASHSCOPE_API_KEY",
})
ALLOWED_ENV_REF_PREFIXES = ("ZHOUXU_MODEL_KEY_", "ZHOUXU_PROVIDER_KEY_")
_FORBIDDEN_ENV_PREFIXES = (
    "OPENSEARCH_",
    "ZHOUXU_ADMIN_",
    "ZHOUXU_MASTER_",
    "ZHOUXU_DB_",
    "ZHOUXU_SESSION_",
)
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,119}$")


def _extra_allowed_env_refs() -> set[str]:
    raw = os.environ.get("ZHOUXU_ALLOWED_MODEL_ENV_REFS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def is_allowed_env_ref(name: str) -> bool:
    """Return True only when an env variable name belongs to the model key whitelist."""

    clean = name.strip()
    if not _ENV_NAME_RE.fullmatch(clean):
        return False
    upper = clean.upper()
    if upper.startswith(_FORBIDDEN_ENV_PREFIXES):
        return False
    if clean in ALLOWED_ENV_REF_NAMES or clean in _extra_allowed_env_refs():
        return True
    return clean.startswith(ALLOWED_ENV_REF_PREFIXES)


def _master_key_bytes() -> bytes:
    raw = os.environ.get("ZHOUXU_ADMIN_CREDENTIAL_KEY", "").strip()
    if not raw:
        raise ValueError("credential_encryption_unconfigured")
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
        if len(decoded) == 32 and len(raw) == 44:
            return raw.encode("ascii")
    except (ValueError, UnicodeEncodeError):
        pass
    if len(raw) < 16:
        raise ValueError("credential_master_key_invalid")
    digest = hashlib.sha256(f"zhouxu-model-credential-v1:{raw}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def credential_encryption_ready() -> bool:
    """True when ZHOUXU_ADMIN_CREDENTIAL_KEY is validly configured in the deployment."""

    try:
        _master_key_bytes()
        return True
    except ValueError:
        return False


def mask_api_key(plaintext: str) -> str:
    """Produce a short irreversible hint such as ``sk-****3f9a`` or ``AIza****9876``."""

    secret = plaintext.strip()
    if not secret:
        raise ValueError("api_key_required")
    if len(secret) <= 4:
        return "****"
    tail = secret[-4:]
    if secret.startswith("sk-") and len(secret) >= 8:
        return f"sk-****{tail}"
    if len(secret) >= 12:
        return f"{secret[:4]}****{tail}"
    return f"****{tail}"


def encrypt_api_key(plaintext: str) -> tuple[str, str]:
    """Authenticate-encrypt a provider API key with Fernet and return (ciphertext, hint)."""

    secret = plaintext.strip()
    if not secret:
        raise ValueError("api_key_required")
    fernet = Fernet(_master_key_bytes())
    token = fernet.encrypt(secret.encode("utf-8")).decode("ascii")
    return token, mask_api_key(secret)


def decrypt_api_key(ciphertext: str) -> SecretStr:
    """Decrypt a persisted API key; fail loudly when the master key or payload is invalid."""

    if not ciphertext:
        raise ValueError("credential_missing")
    fernet = Fernet(_master_key_bytes())
    try:
        value = fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise ValueError("credential_decrypt_failed") from exc
    return SecretStr(value)


__all__ = [
    "ALLOWED_ENV_REF_NAMES",
    "ALLOWED_ENV_REF_PREFIXES",
    "credential_encryption_ready",
    "decrypt_api_key",
    "encrypt_api_key",
    "is_allowed_env_ref",
    "mask_api_key",
]
