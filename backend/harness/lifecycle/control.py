import asyncio
from asyncio import Event
from typing import Literal


class StopRequested(Exception):
    pass


class RunControl:
    """Cooperative cancellation at the execution boundary."""

    def __init__(self):
        self._cancel_requested = Event()
        self._reason: Literal["pause", "cancel", "steer"] | None = None
        self.checkpoint = None
        self.graph_progress = None

    def cancel(self, reason: Literal["pause", "cancel", "steer"] = "pause") -> None:
        """Request cooperative cancellation of the current execution."""
        self._cancel_requested.set()
        self._reason = reason

    @property
    def cancelled(self) -> bool:
        return self._cancel_requested.is_set()

    @property
    def reason(self) -> Literal["pause", "cancel", "steer"] | None:
        return self._reason

    async def read(self, operation):
        """Cancel model reads promptly; never use this for side-effecting tools."""
        task = asyncio.ensure_future(operation)
        cancelled = asyncio.create_task(self._cancel_requested.wait())
        try:
            await asyncio.wait({task, cancelled}, return_when=asyncio.FIRST_COMPLETED)
            if self.cancelled:
                raise StopRequested
            return await task
        finally:
            for pending in (task, cancelled):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(task, cancelled, return_exceptions=True)
