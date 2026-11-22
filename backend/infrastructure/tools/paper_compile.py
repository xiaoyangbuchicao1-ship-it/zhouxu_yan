"""Fixed compiler bridge for the csl-law-paper-generator Skill.

The model supplies only a relative ``.typ`` path inside the current run
workspace. The Skill directory and final artifact directory are deployment
configuration, never model-controlled paths.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path

from backend.config.settings import BACKEND_ROOT, Settings
from backend.contracts.modules.loop import ActionResult, ToolSpec
from backend.contracts.schemas.core import ModuleRequest, PortError
from backend.infrastructure.execution.native import NativeProvider
from backend.infrastructure.execution.workspace import _safe_part


def _workspace_root(settings: Settings, request: ModuleRequest) -> Path:
    configured = Path(settings.execution.workspace_root)
    root = configured if configured.is_absolute() else BACKEND_ROOT.parent / configured
    context = request.context or {}
    account = _safe_part(str(context.get("account_id") or "anonymous"))
    conversation = _safe_part(str(context.get("conversation_id") or request.run_id))
    return root.resolve() / account / conversation


def _skill_dir(settings: Settings) -> Path:
    configured = settings.research_cards.skill_dir
    if configured:
        path = Path(configured)
        return (
            path.resolve()
            if path.is_absolute()
            else (BACKEND_ROOT.parent / path).resolve()
        )
    return (BACKEND_ROOT / "skills" / "csl-law-paper-generator").resolve()


class PaperCompileTool:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.execution = NativeProvider(settings)

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
        output_dir = (workspace / "papers" / _safe_part(request.run_id)).resolve()
        if workspace not in output_dir.parents:
            raise PortError(self.settings.error("execution_invalid_path"))
        try:
            with _output_directory(workspace, output_dir) as output_fd:
                return await self._compile(
                    request, relative, workspace, resolved_source, skill_dir, output_dir, output_fd,
                )
        except OSError as exc:
            raise PortError(self.settings.error("execution_invalid_path")) from exc

    async def _compile(self, request, relative, workspace, resolved_source, skill_dir, output_dir, output_fd):
        compiler = skill_dir / "scripts/compile_paper.py"
        pdf_path = output_dir / f"{resolved_source.stem}.pdf"
        docx_path = output_dir / f"{resolved_source.stem}.docx"
        for path in (pdf_path, docx_path):
            existing = _output_stat(path, output_fd)
            if existing is not None and not stat.S_ISREG(existing.st_mode):
                raise PortError(self.settings.error("execution_invalid_path"))
        runtime = self.execution._create_runtime(request.run_id, workspace)
        sandbox_source = Path("/workspace") / resolved_source.relative_to(workspace)
        sandbox_output = Path("/workspace") / output_dir.relative_to(workspace)
        # A dedicated invocation shares the same mandatory sandbox as bash.
        # Cancellation closes its namespace without stopping other Run tools.
        task = asyncio.create_task(asyncio.to_thread(
            runtime.exec,
            [sys.executable, str(compiler), str(sandbox_source), str(sandbox_output)],
            timeout=self.settings.runtime.compile_timeout_seconds,
            check=False,
        ))
        try:
            process = await asyncio.shield(task)
        except asyncio.CancelledError:
            runtime.close()
            await asyncio.gather(task, return_exceptions=True)
            _remove_outputs((pdf_path, docx_path), output_fd)
            raise
        except (TimeoutError, OSError, RuntimeError) as exc:
            _remove_outputs((pdf_path, docx_path), output_fd)
            raise PortError(
                self.settings.error("paper_compile_failed", retryable=True)
            ) from exc
        finally:
            runtime.close()
        stdout, stderr = process.stdout, process.stderr
        files = [_output_stat(path, output_fd) for path in (pdf_path, docx_path)]
        if process.returncode != 0 or not _same_directory(output_dir, output_fd) or not all(
            item is not None and stat.S_ISREG(item.st_mode) and item.st_size > 0 for item in files
        ):
            # A previous/partial successful side must never be exposed as a
            # successful dual delivery after this compilation failed.
            removed_artifacts = _remove_outputs((pdf_path, docx_path), output_fd)
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
                _artifact(pdf_path, workspace, output_fd),
                _artifact(docx_path, workspace, output_fd),
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


@contextmanager
def _output_directory(workspace: Path, output: Path):
    """Pin the actual directory before compiling; never follow replaced links."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    with ExitStack() as stack:
        descriptor = os.open(workspace, flags)
        stack.callback(os.close, descriptor)
        for part in output.relative_to(workspace).parts:
            try:
                os.mkdir(part, dir_fd=descriptor)
            except FileExistsError:
                pass  # The following O_NOFOLLOW directory open validates it.
            descriptor = os.open(part, flags, dir_fd=descriptor)
            stack.callback(os.close, descriptor)
        yield descriptor


def _output_stat(path: Path, descriptor: int):
    try:
        return os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _same_directory(path: Path, descriptor: int) -> bool:
    try:
        current = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return False
    opened = os.fstat(descriptor)
    return (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino)


def _remove_outputs(paths: tuple[Path, Path], descriptor: int) -> list[str]:
    removed = []
    for path in paths:
        try:
            os.unlink(path.name, dir_fd=descriptor)
            removed.append(path.name)
        except FileNotFoundError:
            continue
    return removed


def _artifact(path: Path, workspace: Path, descriptor: int) -> dict[str, str | int]:
    file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
    with os.fdopen(file_fd, "rb") as stream:
        properties = os.fstat(stream.fileno())
        if not stat.S_ISREG(properties.st_mode):
            raise OSError("compiler output must be a regular file")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "path": path.relative_to(workspace).as_posix(),
        "bytes": properties.st_size,
        "sha256": digest,
    }
