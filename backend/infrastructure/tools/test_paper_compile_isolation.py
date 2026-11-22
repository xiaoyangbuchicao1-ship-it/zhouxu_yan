"""Real compiler tests: fixture prose is not a deliverable academic paper."""

import shutil
import sys
import tempfile
import asyncio
import unittest
import zipfile
from pathlib import Path

from backend.config.settings import BACKEND_ROOT, load_settings
from backend.contracts.modules.loop import ActionRequest
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.infrastructure.tools.paper_compile import PaperCompileTool


def fixture_source() -> str:
    header = '#import "csl_template.typ": csl_paper\n#show: csl_paper.with(title: "编译隔离测试", length_tier: "compact")\n'
    paragraph = "这是用于验证工作区隔离与双格式编译的测试文字，仅供自动化测试核对文件内容与处理结果。" * 15
    return header + "\n\n".join(
        paragraph + f"#footnote[测试引注{index}，载《自动化测试》2026年第1期。]"
        for index in range(12)
    )


@unittest.skipUnless(sys.platform == "linux", "production compiler runs on Linux")
class PaperCompileIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = load_settings()
        self.settings.execution.workspace_root = str(self.root / "workspaces")
        self.workspace = self.root / "workspaces/account/conversation"
        self.workspace.mkdir(parents=True)
        self.source = self.workspace / "paper.typ"
        self.source.write_text(fixture_source(), encoding="utf-8")
        self.tool = PaperCompileTool(self.settings)

    def request(self, source="paper.typ"):
        return ModuleRequest(
            run_id="run", call_id="compile",
            input=ActionRequest(action_id="compile", tool_name="paper_compile", arguments={"source_path": source}),
            context={"account_id": "account", "conversation_id": "conversation"},
        )

    async def test_real_dual_compile_cannot_see_external_sentinel(self):
        sentinel = self.root / "outside-test-sentinel.txt"
        sentinel.write_text("PUBLIC_TEST_SENTINEL")
        skill = self.root / "trusted-compiler"
        shutil.copytree(BACKEND_ROOT / "skills/csl-law-paper-generator", skill)
        compiler = skill / "scripts/compile_paper.py"
        # Trusted instrumentation before the real, unmodified compiler body.
        # It makes the previous direct API-process launch go red.
        compiler.write_text(
            f"from pathlib import Path\nassert not Path({str(sentinel)!r}).exists(), 'compiler escaped workspace'\n"
            + compiler.read_text(encoding="utf-8"), encoding="utf-8",
        )
        self.settings.research_cards.skill_dir = str(skill)
        result = await self.tool.execute_context(self.request())
        self.assertEqual(result.outcome, "ok", result.result)
        self.assertEqual(len(result.result["artifacts"]), 2)
        pdf = self.workspace / "papers/run/paper.pdf"
        docx = self.workspace / "papers/run/paper.docx"
        self.assertTrue(pdf.read_bytes().startswith(b"%PDF-"))
        with zipfile.ZipFile(docx) as archive:
            self.assertIn("word/footnotes.xml", archive.namelist())
            self.assertIn("编译隔离测试", archive.read("word/document.xml").decode())
        self.assertEqual(sentinel.read_text(), "PUBLIC_TEST_SENTINEL")

    async def test_bundled_csl_skill_compiles_inside_sandbox(self):
        result = await self.tool.execute_context(self.request())
        self.assertEqual(result.outcome, "ok", result.result)
        self.assertEqual(
            {item["path"] for item in result.result["artifacts"]},
            {"papers/run/paper.pdf", "papers/run/paper.docx"},
        )

    async def test_failed_compile_removes_both_stale_outputs(self):
        output = self.workspace / "papers/run"
        output.mkdir(parents=True)
        for suffix in ("pdf", "docx"):
            (output / f"paper.{suffix}").write_bytes(b"old-file")
        self.source.write_text("= 参考文献\n无效稿件。", encoding="utf-8")
        result = await self.tool.execute_context(self.request())
        self.assertEqual(result.outcome, "error")
        self.assertIn("参考文献", result.result["compiler_diagnostic"])
        self.assertFalse((output / "paper.pdf").exists())
        self.assertFalse((output / "paper.docx").exists())
        self.assertEqual(set(result.result["removed_artifacts"]), {"paper.pdf", "paper.docx"})

    async def test_source_and_output_cannot_escape_through_symlinks(self):
        outside = self.root / "other-account"
        outside.mkdir()
        other_source = outside / "private.typ"
        other_source.write_text("PUBLIC_TEST_SENTINEL")
        (self.workspace / "escape.typ").symlink_to(other_source)
        with self.assertRaises(PortError):
            await self.tool.execute_context(self.request("escape.typ"))
        (self.workspace / "papers").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(PortError):
            await self.tool.execute_context(self.request())
        self.assertEqual(other_source.read_text(), "PUBLIC_TEST_SENTINEL")
        self.assertEqual(list(outside.iterdir()), [other_source])
        (self.workspace / "papers").unlink()
        for suffix in ("pdf", "docx"):
            (outside / f"paper.{suffix}").write_text("OTHER_ACCOUNT_FILE")
        skill = self.root / "swap-output-compiler"
        shutil.copytree(BACKEND_ROOT / "skills/csl-law-paper-generator", skill)
        (skill / "scripts/compile_paper.py").write_text(
            "import pathlib,sys\n"
            "out=pathlib.Path(sys.argv[2])\n"
            "out.rename(out.with_name('original-output'))\n"
            f"out.symlink_to({str(outside)!r}, target_is_directory=True)\n"
            "sys.exit(1)\n", encoding="utf-8",
        )
        self.settings.research_cards.skill_dir = str(skill)
        await self.tool.execute_context(self.request())
        for suffix in ("pdf", "docx"):
            self.assertEqual((outside / f"paper.{suffix}").read_text(), "OTHER_ACCOUNT_FILE")

    async def test_cancellation_kills_compiler_namespace_and_children(self):
        skill = self.root / "slow-compiler"
        shutil.copytree(BACKEND_ROOT / "skills/csl-law-paper-generator", skill)
        compiler = skill / "scripts/compile_paper.py"
        compiler.write_text(
            "import pathlib, subprocess, sys, time\n"
            "child = \"import pathlib,time; p=pathlib.Path('/workspace/compile-heartbeat'); [p.write_text(str(time.time_ns())) or time.sleep(.02) for _ in iter(int,1)]\"\n"
            "subprocess.Popen([sys.executable, '-c', child], start_new_session=True)\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        self.settings.research_cards.skill_dir = str(skill)
        task = asyncio.create_task(self.tool.execute_context(self.request()))
        await asyncio.sleep(0.5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        heartbeat = self.workspace / "compile-heartbeat"
        self.assertTrue(heartbeat.is_file(), "compiler child never started")
        stopped = heartbeat.read_text()
        await asyncio.sleep(0.25)
        self.assertEqual(heartbeat.read_text(), stopped)
