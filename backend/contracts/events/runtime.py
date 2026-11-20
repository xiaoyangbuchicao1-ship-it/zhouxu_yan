from datetime import datetime, timezone
from typing import Literal

from pydantic import Field

from backend.contracts.schemas.core import Contract


class RunEvent(Contract):
    run_id: str
    sequence: int = Field(default=0, ge=0)
    server_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    call_id: str | None = None
    module: str
    phase: Literal["started", "finished", "retrying", "model"]
    event_type: Literal["lifecycle", "node", "attempt", "model", "diagnostic"] | None = None
    node_id: str | None = None
    attempt_id: str | None = None
    status: str | None = None
    elapsed_seconds: float | None = None
    elapsed_ms: int | None = Field(default=None, ge=0)
    total_tokens: int | None = None
    error_code: str | None = None
    payload_ref: str | None = None
    diagnostics: dict | None = None
