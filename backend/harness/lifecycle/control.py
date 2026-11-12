import asyncio
from asyncio import Event
from typing import Literal


class StopRequested(Exception):
    pass


class RunControl:
    """Cooperative stop: no new work after an in-flight call returns."""

    def __init__(self):
        self._stop = Event()
        self._reason: Literal["interrupt", "stop"] | None = None
        self.checkpoint = None

    def cancel(self, reason: Literal["interrupt", "stop"] = "interrupt") -> None:
        """Request cooperative cancellation of the current execution."""
        self._stop.set()
        self._reason = reason

    def stop(self) -> None:
        """Mark cancellation as terminal for the current Run."""
        self.cancel("stop")

    @property
    def cancelled(self) -> bool:
        return self._stop.is_set()

    @property
    def reason(self) -> Literal["interrupt", "stop"] | None:
        return self._reason

    async def read(self, operation):
        """Cancel model reads promptly; never use this for side-effecting tools."""
        task = asyncio.ensure_future(operation)
        stopped = asyncio.create_task(self._stop.wait())
        try:
            await asyncio.wait({task, stopped}, return_when=asyncio.FIRST_COMPLETED)
            if self.cancelled:
                raise StopRequested
            return await task
        finally:
            for pending in (task, stopped):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(task, stopped, return_exceptions=True)
