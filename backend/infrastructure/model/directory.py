"""Runtime-neutral model directory contract.

The protocol keeps the model transport independent from SQLite.  The database
adapter can implement it without importing the Harness, and the checked-in
seed can implement it for tests and one-time migration input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, Sequence

from pydantic import SecretStr

from backend.contracts.model_profiles import ModelProfile

AuthStyle = Literal["bearer", "x-api-key", "x-goog-api-key", "none"]
CredentialMode = Literal["env_ref", "encrypted_db", "file_ref"]


@dataclass(frozen=True)
class RuntimeModelRevision:
    """Immutable connection and public model facts used by one Run."""

    profile: ModelProfile
    provider_revision_id: str
    profile_revision_id: str
    endpoint: str
    auth_style: AuthStyle
    credential_mode: CredentialMode
    credential_ref: str | None = None
    credential_file: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    access_scope: str = "all"
    encrypted_key: str | None = None

    @property
    def cache_key(self) -> tuple[str, str]:
        return self.provider_revision_id, self.profile_revision_id


class ModelDirectory(Protocol):
    """Storage port implemented by the explicit SQLite catalog migration."""

    def list_current(self) -> Sequence[RuntimeModelRevision]: ...

    def resolve(
        self, profile_id: str, *, profile_revision_id: str | None = None
    ) -> RuntimeModelRevision: ...

    def credential(self, revision: RuntimeModelRevision) -> SecretStr: ...


__all__ = [
    "AuthStyle",
    "CredentialMode",
    "ModelDirectory",
    "RuntimeModelRevision",
]
