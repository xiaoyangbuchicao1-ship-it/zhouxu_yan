import tempfile
import unittest
from pathlib import Path

from backend.application.maritime.service import MaritimeOffice
from backend.application.maritime.test_skill_packages import skill_zip
from backend.config.settings import load_settings
from backend.contracts.modules.loop import ActionRequest
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.execution.native import NativeProvider
from backend.infrastructure.tools.skills import SkillReadTool
from backend.skills.registry import default_skill_registry


class SkillActivationPackageTests(unittest.IsolatedAsyncioTestCase):
    async def test_script_failure_is_an_error_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = load_settings()
            settings.execution.workspace_root = tmp
            provider = NativeProvider(settings)
            self.addAsyncCleanup(provider.close_all)
            result = await provider.execute("bash", ModuleRequest(
                run_id="failed-script", call_id="execute",
                context={"account_id": "owner", "conversation_id": "failure"},
                input=ActionRequest(action_id="execute", tool_name="bash", arguments={"command": "printf 'fixture-script-failed' >&2; exit 7"}),
            ))
            self.assertEqual(result.result["exit_code"], 7)
            self.assertEqual(result.outcome, "error")
            self.assertEqual(result.error.code, "tool_failed")
            self.assertIn("fixture-script-failed", result.result["stderr"])

    async def test_activation_reads_resources_and_stages_identical_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            office = MaritimeOffice(Path(tmp) / "office.sqlite3")
            selected = office.import_skill_package("owner", "expense.zip", skill_zip())
            settings = load_settings()
            settings.execution.workspace_root = str(Path(tmp) / "workspaces")
            provider = NativeProvider(settings)

            def loader(account_id, skill_id, version=None):
                value = office.skill(account_id, skill_id, version)
                return {**value, "_package_files": office.package_files(account_id, skill_id, version)}

            tool = SkillReadTool(
                default_skill_registry(), settings, account_skill_loader=loader,
                package_installer=provider.install_skill_package,
            )
            context = {"account_id": "owner", "conversation_id": "conversation"}
            async def read(**arguments):
                return await tool.execute_context(ModuleRequest(
                    run_id="run", call_id="read", context=context,
                    input=ActionRequest(action_id="read", tool_name="read_skill", arguments=arguments),
                ))
            result = await read(skill_id=selected["id"])
            self.assertIn("scripts/sum.py", result.result["resources"])
            self.assertEqual(result.result["package_path"], f".skills/{selected['id']}/1")
            staged = provider.package_root_for(provider.workspace_root_for(run_id="run", **context)) / selected["id"] / "1"
            self.assertEqual((staged / "scripts/sum.py").read_bytes(), b"print('ok')\n")
            resource = await read(skill_id=selected["id"], resource="references/rules.md")
            self.assertEqual(resource.result["content"], "合计前先检查币种。\n")
            self.assertIn(".skills/", result.result["runtime_instructions"])


if __name__ == "__main__":
    unittest.main()
