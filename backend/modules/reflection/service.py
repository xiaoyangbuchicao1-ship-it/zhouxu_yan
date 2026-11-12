from backend.config.settings import Settings
from backend.contracts.modules.loop import ReflectionInput, ReflectionResult
from backend.contracts.schemas.core import ModuleRequest, ModuleResponse


class RuleReflection:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def handle(
        self, request: ModuleRequest[ReflectionInput]
    ) -> ModuleResponse[ReflectionResult]:
        value = request.input
        proposed = value.proposed
        if proposed.kind == "needs_input":
            review = ReflectionResult(
                decision="needs_input", issues=proposed.missing_inputs
            )
        elif proposed.kind == "answer":
            review = (
                ReflectionResult(decision="complete")
                if (proposed.answer or "").strip()
                else ReflectionResult(
                    decision="retry",
                    feedback=self.settings.reflection["retry_feedback"],
                )
            )
        else:
            expected = {action.action_id for action in proposed.action_requests}
            actual = {
                action.action_id
                for action in value.actions
                if action.result is not None
            }
            if expected and expected == actual:
                review = ReflectionResult(
                    decision="continue",
                    feedback=self.settings.reflection["continue_feedback"],
                )
            else:
                review = ReflectionResult(
                    decision="fail", issues=[self.settings.messages["missing_output"]]
                )
        # Structural checks only. Domain correctness requires a separate evaluation policy.
        return ModuleResponse(status="ok", output=review)
