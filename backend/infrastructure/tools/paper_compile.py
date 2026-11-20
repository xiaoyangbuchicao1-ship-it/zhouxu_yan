"""Fixed compiler bridge for the csl-law-paper-generator Skill.

The model supplies only a relative ``.typ`` path inside the current run
workspace. The Skill directory and final artifact directory are deployment
configuration, never model-controlled paths.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path

from backend.config.settings import BACKEND_ROOT, Settings
from backend.contracts.modules.loop import ActionResult, ToolSpec
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.infrastructure.execution.workspace import _safe_part


def _workspace_root(settings: Settings, request: ModuleRequest) -> Path:
    configured = Path(settings.execution.workspace_root)
    root = configured if configured.is_absolute() else BACKEND_ROOT.parent / configured
    context = request.context or {}
    account = _safe_part(str(context.get("account_id") or "anonymous"))
    conversation = _safe_part(str(context.get("conversation_id") or request.run_id))
    return (root.resolve() / account / conversation).resolve()


def _skill_dir(settings: Settings) -> Path:
    configured = settings.research_cards.skill_dir
    if configured:
        path = Path(configured)
        return (
            path.resolve()
            if path.is_absolute()
            else (BACKEND_ROOT.parent / path).resolve()
        )
    return (
        BACKEND_ROOT.parent.parent / "技能候选" / "csl-law-paper-generator"
    ).resolve()


class PaperCompileTool:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="paper_compile",
            description=self.settings.research_cards.paper_compile_description,
            parameters={
                "type": "object",
                "properties": {
                    "source_path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 240,
                        "description": "当前运行工作区内的相对 .typ 路径，例如 paper.typ",
                    }
                },
                "required": ["source_path"],
                "additionalProperties": False,
            },
            kind="execution",
            read_only=False,
            reuse_identical_results=False,
        )

    async def execute_context(self, request: ModuleRequest) -> ActionResult:
        arguments = request.input.arguments or {}
        source_value = arguments.get("source_path")
        if not isinstance(source_value, str) or not source_value.strip():
            raise PortError(self.settings.error("invalid_arguments"))
        relative = source_value.replace("\\", "/").strip()
        source_path = Path(relative)
        if (
            source_path.is_absolute()
            or any(part in {"", ".", ".."} for part in source_path.parts)
            or source_path.suffix.lower() != ".typ"
        ):
            raise PortError(self.settings.error("execution_invalid_path"))
        workspace = _workspace_root(self.settings, request)
        resolved_source = (workspace / source_path).resolve()
        if workspace != resolved_source and workspace not in resolved_source.parents:
            raise PortError(self.settings.error("execution_invalid_path"))
        if not resolved_source.is_file():
            raise PortError(self.settings.error("execution_file_not_found"))
        skill_dir = _skill_dir(self.settings)
        compiler = skill_dir / "scripts" / "compile_paper.py"
        if not compiler.is_file():
            raise PortError(self.settings.error("paper_compile_configuration"))
        # Use the existing workspace artifact collector and authenticated
        # download route. Files outside this root are invisible to the user.
        output_dir = workspace / "papers" / _safe_part(request.run_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        child_env = {
            key: os.environ[key]
            for key in ("PATH", "SystemRoot", "WINDIR", "LANG", "LC_ALL")
            if key in os.environ
        }
        child_env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "CSL_OUTPUT_DIR": str(output_dir),
                "HOME": str(workspace / ".native-home"),
            }
        )
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(compiler),
                str(resolved_source),
                str(output_dir),
                cwd=str(skill_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env,
            )
        except OSError as exc:
            raise PortError(
                self.settings.error("paper_compile_failed", retryable=True)
            ) from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.settings.runtime.action_timeout_seconds or 180,
            )
        except asyncio.CancelledError:
            process.kill()
            await process.communicate()
            raise
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            for suffix in (".pdf", ".docx"):
                (output_dir / f"{resolved_source.stem}{suffix}").unlink(missing_ok=True)
            raise PortError(
                self.settings.error("paper_compile_failed", retryable=True)
            ) from exc
        pdf_path = output_dir / f"{resolved_source.stem}.pdf"
        docx_path = output_dir / f"{resolved_source.stem}.docx"
        if process.returncode != 0 or not all(
            path.is_file() for path in (pdf_path, docx_path)
        ):
            # A previous/partial successful side must never be exposed as a
            # successful dual delivery after this compilation failed.
            removed_artifacts = [
                path.name for path in (pdf_path, docx_path) if path.is_file()
            ]
            for path in (pdf_path, docx_path):
                path.unlink(missing_ok=True)
            diagnostic = stderr.decode("utf-8", errors="replace")[-4000:]
            diagnostic = diagnostic.replace(str(skill_dir), "<Skill>").replace(
                str(workspace), "<workspace>"
            )
            error = self.settings.error("paper_compile_failed")
            return ActionResult(
                tool_name=request.input.tool_name,
                action_id=request.input.action_id,
                outcome="error",
                error=error,
                result={
                    "status": "failed",
                    "error": error.code,
                    "message": error.message,
                    "compiler_diagnostic": diagnostic,
                    "compiler_output": stdout.decode("utf-8", errors="replace")[-2000:]
                    .replace(str(skill_dir), "<Skill>")
                    .replace(str(workspace), "<workspace>"),
                    "removed_artifacts": removed_artifacts,
                    "delivery_policy": "任一端失败，本次两端文件均不交付；removed_artifacts 列出已删除的部分产物。",
                },
                session_summary={"status": "failed", "error": error.code},
            )
        result = {
            "status": "ok",
            "source_path": relative,
            "artifacts": [
                _artifact(pdf_path, workspace),
                _artifact(docx_path, workspace),
            ],
            "compiler": "csl-law-paper-generator/scripts/compile_paper.py",
            "warnings": stderr.decode("utf-8", errors="replace")[-2000:]
            .replace(str(workspace), "<workspace>")
            .replace(str(skill_dir), "<Skill>"),
        }
        return ActionResult(
            tool_name=request.input.tool_name,
            action_id=request.input.action_id,
            result=result,
            outcome="ok",
            session_summary={
                "status": "ok",
                "artifact_paths": [item["path"] for item in result["artifacts"]],
            },
        )


def _artifact(path: Path, workspace: Path) -> dict[str, str | int]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": path.relative_to(workspace).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": digest,
    }
