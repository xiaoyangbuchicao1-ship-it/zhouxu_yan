from typing import Literal

from backend.contracts.schemas.core import Contract


class RunEvent(Contract):
    run_id: str
    call_id: str | None = None
    module: str
    phase: Literal["started", "finished", "retrying", "model"]
    status: str | None = None
    elapsed_seconds: float | None = None
    total_tokens: int | None = None
    error_code: str | None = None
    diagnostics: dict | None = None
