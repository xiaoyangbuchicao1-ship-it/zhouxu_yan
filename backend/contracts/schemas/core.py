from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Contract(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Error(Contract):
    code: str
    message: str
    retryable: bool = False
    category: Literal["system", "user", "unknown"] = "unknown"


class PortError(Exception):
    def __init__(self, error: Error):
        self.error = error
        super().__init__(error.code)


class ModuleRequest(Contract, Generic[T]):
    run_id: str = Field(min_length=1)
    call_id: str = Field(min_length=1)
    input: T
    turn_id: str | None = None
    context: dict | None = None


class ModuleResponse(Contract, Generic[T]):
    status: Literal["ok", "needs_input", "failed", "cancelled"]
    output: T | None = None
    error: Error | None = None
