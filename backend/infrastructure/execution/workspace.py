"""One file-tool and artifact contract shared by all work execution runtimes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Protocol

from backend.config.settings import BACKEND_ROOT, Settings
from backend.contracts.modules.loop import ActionRequest, ActionResult, ToolSpec
from backend.contracts.ports import ContextualToolExecutor
from backend.contracts.schemas.core import ModuleRequest, PortError


class CommandRuntime(Protocol):
    """Execute in the session workspace, with byte streams and UTF-8 Python output.

    Missing executables raise RuntimeError("execution_unavailable"); expired
    commands raise TimeoutError. close() releases processes, retaining files.
    """

    def exec(
        self,
        args: list[str],
        *,
        timeout: float | None = None,
        check: bool = True,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess: ...

    def python(
        self,
        code: str,
        args: list[str] | None = None,
        *,
        timeout: float | None = None,
        check: bool = True,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess: ...

    def bash(
        self, command: str, *, timeout: float | None = None, check: bool = True
    ) -> subprocess.CompletedProcess: ...

    def close(self) -> None: ...


_SAFE_PART = re.compile(r"[^A-Za-z0-9._-]+")
_VERSION = re.compile(r"(?:[-_. ]v|版本)[ _-]?(\d+)", re.IGNORECASE)
_DELIVERABLE_EXTENSIONS = {
    ".docx",
    ".docm",
    ".pdf",
    ".xlsx",
    ".xlsm",
    ".pptx",
    ".pptm",
    ".csv",
    ".txt",
    ".md",
    ".json",
    ".png",
    ".jpg",
    ".jpeg",
}


def _safe_part(value: str) -> str:
    result = _SAFE_PART.sub("_", value).strip("._")
    return result[:96] or "workspace"


def _decode(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


def _port_error(settings: Settings, code: str, *, retryable: bool = False) -> PortError:
    return PortError(settings.error(code, retryable=retryable))


@dataclass
class _RunWorkspace:
    run_id: str
    root: Path
    runtime: CommandRuntime
    before: dict[str, str] = field(default_factory=dict)


class _WorkspaceTool(ContextualToolExecutor):
    def __init__(self, owner: "WorkspaceExecutionProvider", name: str, spec: ToolSpec):
        self.owner, self.name, self.spec = owner, name, spec

    async def execute_context(
        self, request: ModuleRequest[ActionRequest]
    ) -> ActionResult:
        return await self.owner.execute(self.name, request)


class WorkspaceExecutionProvider:
    """Shared tools, session files and Run lifecycle, independent of process isolation."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._runs: dict[str, _RunWorkspace] = {}
        self._lock = asyncio.Lock()
        root = Path(settings.execution.workspace_root)
        self.root = (
            (BACKEND_ROOT.parent / root).resolve()
            if not root.is_absolute()
            else root.resolve()
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self._specs = self._make_specs()

    def _make_specs(self) -> dict[str, ToolSpec]:
        return {
            "read": ToolSpec(
                name="read",
                description="读取当前工作区内的文本文件或目录。path只能是相对当前工作区的路径；二进制文档请用bash调用文档库读取。",
                kind="execution",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "max_chars": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 120000,
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            "write": ToolSpec(
                name="write",
                description="在当前工作区创建文本文件或脚本。默认不覆盖已有文件；需要修改已有文本时使用edit。",
                kind="execution",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "overwrite": {"type": "boolean"},
                        "expected_sha256": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            ),
            "edit": ToolSpec(
                name="edit",
                description="按精确唯一文本替换修改文件。可传expected_sha256；匹配不到或匹配多处时不会写入。二进制DOCX请用脚本和bash处理。",
                kind="execution",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old": {"type": "string"},
                        "new": {"type": "string"},
                        "expected_sha256": {"type": "string"},
                    },
                    "required": ["path", "old", "new"],
                    "additionalProperties": False,
                },
            ),
            "bash": ToolSpec(
                name="bash",
                description="在当前工作区的隔离执行环境内运行bash命令。命令只应操作本次会话工作区；首期文档生成可调用python-docx、lxml、LibreOffice和PDF检查工具。固定模板或复杂OOXML由部署运行时提供。",
                kind="execution",
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 16000,
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 120,
                        },
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
            ),
        }

    def registrations(self) -> dict[str, tuple[ToolSpec, _WorkspaceTool]]:
        return {
            name: (spec, _WorkspaceTool(self, name, spec))
            for name, spec in self._specs.items()
        }

    def _workspace_for(self, run_id: str, context: dict | None = None) -> _RunWorkspace:
        existing = self._runs.get(run_id)
        if existing is not None:
            return existing
        context = context or {}
        account = _safe_part(str(context.get("account_id") or "anonymous"))
        conversation = _safe_part(str(context.get("conversation_id") or run_id))
        root = self.root / account / conversation
        value = _RunWorkspace(
            run_id=run_id,
            root=root,
            runtime=self._create_runtime(run_id, root),
            before=self._snapshot(root),
        )
        self._runs[run_id] = value
        return value

    def _create_runtime(self, run_id: str, root: Path) -> CommandRuntime:
        raise NotImplementedError

    @staticmethod
    def _snapshot(root: Path) -> dict[str, str]:
        if not root.exists():
            return {}
        result: dict[str, str] = {}
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if (
                path.is_symlink()
                or not path.is_file()
                or any(part.startswith(".") for part in relative.parts)
                or path.suffix.lower() not in _DELIVERABLE_EXTENSIONS
            ):
                continue
            try:
                result[relative.as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
            except OSError:
                continue
        return result

    @staticmethod
    def _relative(arguments: dict) -> str:
        value = arguments.get("path")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("path")
        value = value.replace("\\", "/").strip()
        path = Path(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("path")
        return "/".join(path.parts)

    async def execute(
        self, name: str, request: ModuleRequest[ActionRequest]
    ) -> ActionResult:
        try:
            arguments = request.input.arguments or {}
            async with self._lock:
                workspace = self._workspace_for(request.run_id, request.context)
            if name == "read":
                result = await asyncio.to_thread(self._read, workspace, arguments)
            elif name == "write":
                result = await asyncio.to_thread(self._write, workspace, arguments)
            elif name == "edit":
                result = await asyncio.to_thread(self._edit, workspace, arguments)
            elif name == "bash":
                result = await asyncio.to_thread(self._bash, workspace, arguments)
            else:
                raise ValueError("tool")
            return ActionResult(
                tool_name=request.input.tool_name,
                action_id=request.input.action_id,
                result=result,
                outcome="ok",
                session_summary={
                    "status": "ok",
                    "tool": name,
                    "path": result.get("path") if isinstance(result, dict) else None,
                },
            )
        except PortError:
            raise
        except ValueError:
            raise _port_error(self.settings, "execution_invalid_path") from None
        except TimeoutError:
            raise _port_error(
                self.settings, "execution_timeout", retryable=True
            ) from None
        except FileNotFoundError:
            raise _port_error(self.settings, "execution_file_not_found") from None
        except RuntimeError as exc:
            message = str(exc)
            if message == "execution_unavailable":
                raise _port_error(
                    self.settings, "execution_unavailable", retryable=True
                ) from None
            if message == "file_too_large":
                raise _port_error(self.settings, "execution_file_too_large") from None
            if message == "conflict":
                raise _port_error(self.settings, "execution_conflict") from None
            if message == "exact_match_required":
                raise _port_error(self.settings, "execution_exact_match") from None
            raise _port_error(self.settings, "tool_failed", retryable=True) from None

    @staticmethod
    def _file_command(
        workspace: _RunWorkspace,
        code: str,
        args: list[str],
        *,
        input: bytes | None = None,
    ) -> str:
        result = workspace.runtime.python(code, args, check=False, input=input)
        if result.returncode == 0:
            return _decode(result.stdout)
        message = _decode(result.stderr)
        if "outside_workspace" in message:
            raise ValueError("path")
        if "FileNotFoundError" in message or "No such file" in message:
            raise FileNotFoundError
        for marker in ("conflict", "exact_match_required"):
            if marker in message:
                raise RuntimeError(marker)
        raise RuntimeError("file_command_failed")

    def _read(self, workspace: _RunWorkspace, arguments: dict) -> dict:
        relative = self._relative(arguments)
        max_chars = min(
            int(arguments.get("max_chars", self.settings.execution.max_read_chars)),
            self.settings.execution.max_read_chars,
        )
        code = """
import hashlib,json,pathlib,sys
root=pathlib.Path.cwd().resolve()
p=(root/sys.argv[1]).resolve()
if root != p and root not in p.parents: raise SystemExit('outside_workspace')
if p.is_dir():
    print(json.dumps({'status':'ok','path':sys.argv[1],'kind':'directory','entries':sorted(x.name for x in p.iterdir())[:500]},ensure_ascii=False))
    raise SystemExit
data=p.read_bytes()
print(json.dumps({'status':'ok','path':sys.argv[1],'kind':'file','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'text':data.decode('utf-8',errors='replace')[:int(sys.argv[2])]},ensure_ascii=False))
""".strip()
        return json.loads(
            self._file_command(workspace, code, [relative, str(max_chars)])
        )

    def _write(self, workspace: _RunWorkspace, arguments: dict) -> dict:
        relative = self._relative(arguments)
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("content")
        data = content.encode("utf-8")
        if len(data) > self.settings.execution.max_file_bytes:
            raise RuntimeError("file_too_large")
        expected = str(arguments.get("expected_sha256") or "")
        overwrite = bool(arguments.get("overwrite", False))
        code = """
import hashlib,pathlib,sys
root=pathlib.Path.cwd().resolve()
p=root/sys.argv[1]
resolved=p.resolve(strict=False)
if root != resolved and root not in resolved.parents: raise SystemExit('outside_workspace')
if p.exists() and sys.argv[2] and hashlib.sha256(p.read_bytes()).hexdigest()!=sys.argv[2]: raise SystemExit('conflict')
if p.exists() and not sys.argv[3].lower()=='true': raise SystemExit('conflict')
p.parent.mkdir(parents=True,exist_ok=True)
data=sys.stdin.buffer.read()
tmp=p.with_name('.'+p.name+'.tmp')
tmp.write_bytes(data)
tmp.replace(p)
print(hashlib.sha256(data).hexdigest())
""".strip()
        digest = self._file_command(
            workspace, code, [relative, expected, str(overwrite).lower()], input=data
        )
        return {
            "status": "ok",
            "path": relative,
            "bytes": len(data),
            "sha256": digest.strip(),
        }

    def _edit(self, workspace: _RunWorkspace, arguments: dict) -> dict:
        relative = self._relative(arguments)
        old, new = arguments.get("old"), arguments.get("new")
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            raise ValueError("edit")
        if len(new.encode("utf-8")) > self.settings.execution.max_file_bytes:
            raise RuntimeError("file_too_large")
        expected = str(arguments.get("expected_sha256") or "")
        code = """
import hashlib,json,pathlib,sys
root=pathlib.Path.cwd().resolve()
p=(root/sys.argv[1]).resolve()
if root != p and root not in p.parents: raise SystemExit('outside_workspace')
data=p.read_bytes()
if sys.argv[2] and hashlib.sha256(data).hexdigest()!=sys.argv[2]: raise SystemExit('conflict')
text=data.decode('utf-8')
patch=json.loads(sys.stdin.buffer.read())
old,new=patch['old'],patch['new']
if text.count(old)!=1: raise SystemExit('exact_match_required')
value=text.replace(old,new,1).encode('utf-8')
tmp=p.with_name('.'+p.name+'.tmp')
tmp.write_bytes(value)
tmp.replace(p)
print(hashlib.sha256(value).hexdigest())
""".strip()
        payload = json.dumps({"old": old, "new": new}).encode("utf-8")
        digest = self._file_command(
            workspace, code, [relative, expected], input=payload
        )
        return {
            "status": "ok",
            "path": relative,
            "replacements": 1,
            "sha256": digest.strip(),
        }

    def _bash(self, workspace: _RunWorkspace, arguments: dict) -> dict:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip() or len(command) > 16000:
            raise ValueError("command")
        timeout = min(
            int(
                arguments.get(
                    "timeout_seconds", self.settings.execution.command_timeout_seconds
                )
            ),
            120,
        )
        began = monotonic()
        result = workspace.runtime.bash(command, timeout=timeout, check=False)
        return {
            "status": "ok" if result.returncode == 0 else "failed",
            "exit_code": result.returncode,
            "stdout": _decode(result.stdout)[
                : self.settings.execution.max_output_chars
            ],
            "stderr": _decode(result.stderr)[
                : self.settings.execution.max_output_chars
            ],
            "duration_ms": round((monotonic() - began) * 1000),
        }

    def _ensure_preview(self, workspace: _RunWorkspace, relative: str):
        target = Path(relative).with_suffix(".pdf").as_posix()
        target_host = workspace.root / target
        if target_host.exists() and target_host.stat().st_size > 0:
            if workspace.runtime.exec(["pdfinfo", target], check=False).returncode == 0:
                return
        # Relative paths resolve inside the runtime's working directory. Neither
        # file tools nor previews need to know the container's mount location.
        code = """
import pathlib,shutil,subprocess,sys,tempfile
root=pathlib.Path.cwd().resolve()
source=(root/sys.argv[1]).resolve()
target=source.with_suffix('.pdf')
render=(root/'.zhouxu-render').resolve()
if any(root not in p.parents for p in (source,target,render)): raise SystemExit('outside_workspace')
render.mkdir(parents=True,exist_ok=True)
profile=pathlib.Path(tempfile.gettempdir())/('lo-profile-'+sys.argv[2])
profile.mkdir(parents=True,exist_ok=True)
pathlib.Path.home().mkdir(parents=True,exist_ok=True)
subprocess.run(['soffice','--headless','-env:UserInstallation='+profile.as_uri(),'--convert-to','pdf','--outdir',str(render),str(source)],check=True)
result=(render/(source.stem+'.pdf')).resolve()
if root not in result.parents or not result.is_file() or result.stat().st_size<=0: raise SystemExit('preview_missing')
shutil.copyfile(result,target)
""".strip()
        result = workspace.runtime.python(
            code, [relative, _safe_part(workspace.run_id)], timeout=120, check=False
        )
        if result.returncode != 0 or not target_host.exists():
            raise RuntimeError("preview_failed")

    def _render_previews(self, workspace: _RunWorkspace):
        for path in workspace.root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".docx", ".docm"}:
                relative = path.relative_to(workspace.root).as_posix()
                try:
                    check = workspace.runtime.python(
                        "from docx import Document; import sys; Document(sys.argv[1])",
                        [relative],
                        check=False,
                    )
                    if check.returncode == 0:
                        self._ensure_preview(workspace, relative)
                except (OSError, RuntimeError, TimeoutError):
                    # A missing office converter must not hide the original
                    # document.  The completed Run still exposes it for
                    # download; the API can report preview unavailable.
                    continue
        for path in workspace.root.rglob("*.pdf"):
            if path.is_file():
                relative = path.relative_to(workspace.root).as_posix()
                workspace.runtime.exec(["pdfinfo", relative], check=False)

    def _artifact_metadata(self, workspace: _RunWorkspace) -> list[dict]:
        self._render_previews(workspace)
        current = self._snapshot(workspace.root)
        changed = [
            path
            for path, digest in current.items()
            if workspace.before.get(path) != digest
        ]
        artifacts: list[dict] = []
        for relative in sorted(changed):
            path = workspace.root / relative
            digest = current[relative]
            suffix = path.suffix.lower()
            version_match = _VERSION.search(path.stem)
            version = int(version_match.group(1)) if version_match else 1
            kind = (
                "document"
                if suffix in {".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm"}
                else "file"
            )
            artifacts.append(
                {
                    "artifact_id": hashlib.sha256(
                        f"{relative}:{digest}".encode()
                    ).hexdigest()[:24],
                    "name": path.name,
                    "relative_path": relative,
                    "media_type": mimetypes.guess_type(path.name)[0]
                    or "application/octet-stream",
                    "size_bytes": path.stat().st_size,
                    "sha256": digest,
                    "version": version,
                    "kind": kind,
                }
            )
        for item in artifacts:
            if item["media_type"] == "application/pdf":
                document = next(
                    (
                        candidate
                        for candidate in artifacts
                        if candidate is not item
                        and candidate["kind"] == "document"
                        and Path(candidate["relative_path"]).with_suffix("").as_posix()
                        == Path(item["relative_path"]).with_suffix("").as_posix()
                    ),
                    None,
                )
                if document and document["kind"] == "document":
                    item["kind"] = "preview"
                    item["preview_of_artifact_id"] = document["artifact_id"]
                    document["preview_artifact_id"] = item["artifact_id"]
        return artifacts

    def collect_artifacts(self, run_id: str, *, completed: bool) -> list[dict]:
        workspace = self._runs.get(run_id)
        if workspace is None or not completed:
            return []
        try:
            return self._artifact_metadata(workspace)
        except (OSError, RuntimeError, TimeoutError):
            return []

    async def close_run(self, run_id: str):
        async with self._lock:
            workspace = self._runs.pop(run_id, None)
        if workspace is not None:
            await asyncio.to_thread(workspace.runtime.close)

    async def close_all(self):
        async with self._lock:
            ids = list(self._runs)
        for run_id in ids:
            await self.close_run(run_id)
