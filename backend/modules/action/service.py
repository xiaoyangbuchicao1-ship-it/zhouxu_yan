from backend.config.settings import Settings
from backend.contracts.modules.loop import ActionRequest, ActionResult
from backend.contracts.ports import ContextualToolExecutor, ToolExecutor
from backend.contracts.schemas.core import ModuleRequest, ModuleResponse, PortError


class RegisteredAction:
    def __init__(
        self,
        tools: dict[str, ToolExecutor | ContextualToolExecutor],
        settings: Settings,
    ):
        self.tools = dict(tools)
        self.settings = settings

    async def handle(
        self, request: ModuleRequest[ActionRequest]
    ) -> ModuleResponse[ActionResult]:
        executor = self.tools.get(request.input.tool_name)
        if executor is None:
            return self._failure(request.input, self.settings.error("unknown_tool"))
        try:
            if isinstance(executor, ContextualToolExecutor):
                result = await executor.execute_context(request)
            else:
                result = await executor.execute(request.input)
        except PortError as exc:
            return self._failure(request.input, exc.error, executor=executor)
        except Exception:
            return self._failure(
                request.input, self.settings.error("tool_failed"), executor=executor
            )
        if (
            result.action_id != request.input.action_id
            or result.tool_name != request.input.tool_name
        ):
            return ModuleResponse(
                status="failed", error=self.settings.error("missing_output")
            )
        return ModuleResponse(status="ok", output=result)

    def _failure(self, request, error, *, executor=None):
        spec = getattr(executor, "spec", None)
        recoverable = error.code in self.settings.runtime.recoverable_tool_errors or (
            bool(spec and spec.read_only)
            and error.code in self.settings.runtime.read_only_recoverable_errors
        )
        if not recoverable:
            # Scope/identity violations and unknown side effects remain fatal.
            return ModuleResponse(status="failed", error=error)
        # Only configured public diagnostics enter model context. An adapter's
        # exception message may contain paths or connection details.
        error = self.settings.error(error.code, retryable=error.retryable)
        return ModuleResponse(
            status="ok",
            output=ActionResult(
                tool_name=request.tool_name,
                action_id=request.action_id,
                outcome="error",
                error=error,
                result={
                    "status": "failed",
                    "error": error.code,
                    "message": error.message,
                },
                session_summary={"status": "failed", "error": error.code},
            ),
        )
