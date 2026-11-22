"""One file-tool and artifact contract shared by all work execution runtimes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import tempfile
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
                read_only=True,
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
                description="在当前工作区创建或分段追加文本文件。默认不覆盖已有文件；长文件可传 append=true 分章追加写入，局部修改已有文本请使用 edit。",
                kind="execution",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "overwrite": {"type": "boolean"},
                        "append": {"type": "boolean"},
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

    def workspace_root_for(
        self,
        *,
        account_id: str | None = None,
        conversation_id: str | None = None,
        run_id: str,
    ) -> Path:
        account = _safe_part(str(account_id or "anonymous"))
        conversation = _safe_part(str(conversation_id or run_id))
        return self.root / account / conversation

    def snapshot_baseline(
        self,
        run_id: str,
        *,
        account_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, str]:
        return self._snapshot(
            self.workspace_root_for(
                account_id=account_id,
                conversation_id=conversation_id,
                run_id=run_id,
            )
        )

    def _workspace_for(self, run_id: str, context: dict | None = None) -> _RunWorkspace:
        existing = self._runs.get(run_id)
        if existing is not None:
            return existing
        context = context or {}
        root = self.workspace_root_for(
            account_id=context.get("account_id"),
            conversation_id=context.get("conversation_id"),
            run_id=run_id,
        )
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

    def package_root_for(self, workspace_root: Path) -> Path:
        """Private API-owned storage, exposed read-only inside the runtime."""
        relative = workspace_root.relative_to(self.root)
        return self.root / ".skill-packages" / relative

    async def install_skill_package(
        self, request: ModuleRequest, skill_id: str, version: int, files: dict[str, bytes]
    ) -> str:
        from backend.skills.packages import safe_resource_path

        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", skill_id) or type(version) is not int or version < 1:
            raise ValueError("invalid_package_identity")
        async with self._lock:
            workspace = self._workspace_for(request.run_id, request.context)
            package_root = self.package_root_for(workspace.root)
            parent = package_root / skill_id
            target = parent / str(version)

            def install():
                parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    existing = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}
                    if existing != files:
                        raise RuntimeError("skill_package_identity_changed")
                    return
                with tempfile.TemporaryDirectory(prefix=".install-", dir=parent) as temp:
                    staging = Path(temp) / "package"
                    staging.mkdir()
                    for relative, content in files.items():
                        safe_resource_path(relative)
                        output = staging / relative
                        output.parent.mkdir(parents=True, exist_ok=True)
                        output.write_bytes(content)
                        output.chmod(0o444)
                    os.replace(staging, target)

            await asyncio.to_thread(install)
        return f".skills/{skill_id}/{version}"

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
                or (relative.parts and relative.parts[0] == "work")
                or path.suffix.lower() not in _DELIVERABLE_EXTENSIONS
            ):
                continue
            result[relative.as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
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
                result = await asyncio.to_thread(self._write, workspace, arguments, request.input.action_id)
            elif name == "edit":
                result = await asyncio.to_thread(self._edit, workspace, arguments)
            elif name == "bash":
                result = await asyncio.to_thread(self._bash, workspace, arguments)
            else:
                raise ValueError("tool")
            failed = result.get("status") == "failed"
            return ActionResult(
                tool_name=request.input.tool_name,
                action_id=request.input.action_id,
                result=result,
                outcome="error" if failed else "ok",
                error=self.settings.error("tool_failed") if failed else None,
                session_summary={
                    "status": "failed" if failed else "ok",
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
        for marker in ("conflict", "exact_match_required", "file_too_large"):
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

    def _write(self, workspace: _RunWorkspace, arguments: dict, action_id: str) -> dict:
        relative = self._relative(arguments)
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("content")
        data = content.encode("utf-8")
        max_bytes = self.settings.execution.max_file_bytes
        if len(data) > max_bytes:
            raise RuntimeError("file_too_large")
        expected = str(arguments.get("expected_sha256") or "")
        overwrite = bool(arguments.get("overwrite", False))
        append = bool(arguments.get("append", False))
        code = """
import hashlib,json,os,pathlib,sqlite3,sys
root=pathlib.Path.cwd().resolve()
p=root/sys.argv[1]
resolved=p.resolve(strict=False)
if root != resolved and root not in resolved.parents: raise SystemExit('outside_workspace')
expected,overwrite,append,max_bytes=sys.argv[2],sys.argv[3]=='true',sys.argv[4]=='true',int(sys.argv[5])
incoming=sys.stdin.buffer.read()
identity=sys.argv[6]
fingerprint=hashlib.sha256(json.dumps(sys.argv[1:6]).encode()+incoming).hexdigest()
journal=root/'.zhouxu-writes.sqlite3'
db=sqlite3.connect(journal)
db.execute('CREATE TABLE IF NOT EXISTS writes (id TEXT PRIMARY KEY, fingerprint TEXT, before_hash TEXT, after_hash TEXT, result TEXT, done INTEGER)')
db.execute('BEGIN IMMEDIATE')
prior=db.execute('SELECT fingerprint,before_hash,after_hash,result,done FROM writes WHERE id=?',(identity,)).fetchone()
existing=p.read_bytes() if p.exists() else None
current_hash=hashlib.sha256(existing).hexdigest() if existing is not None else None
if prior:
    if prior[0]!=fingerprint: raise SystemExit('conflict')
    if prior[4] or current_hash==prior[2]:
        db.execute('UPDATE writes SET done=1 WHERE id=?',(identity,)); db.commit()
        print(prior[3]); raise SystemExit
    if current_hash!=prior[1]: raise SystemExit('conflict')
if existing is not None and expected and hashlib.sha256(existing).hexdigest()!=expected: raise SystemExit('conflict')
if existing is not None and not overwrite and not append: raise SystemExit('conflict')
final=(existing+incoming) if (existing is not None and append) else incoming
if len(final)>max_bytes: raise SystemExit('file_too_large')
result=json.dumps({'bytes':len(final),'sha256':hashlib.sha256(final).hexdigest()},ensure_ascii=False)
if not prior:
    db.execute('INSERT INTO writes VALUES (?,?,?,?,?,0)',(identity,fingerprint,current_hash,hashlib.sha256(final).hexdigest(),result))
db.commit()
p.parent.mkdir(parents=True,exist_ok=True)
tmp=p.with_name('.'+p.name+'.tmp')
with tmp.open('wb') as f:
    f.write(final); f.flush(); os.fsync(f.fileno())
tmp.replace(p)
directory=os.open(str(p.parent),os.O_RDONLY)
try: os.fsync(directory)
finally: os.close(directory)
db.execute('UPDATE writes SET done=1 WHERE id=?',(identity,)); db.commit(); db.close()
print(result)
""".strip()
        raw = self._file_command(
            workspace,
            code,
            [
                relative,
                expected,
                str(overwrite).lower(),
                str(append).lower(),
                str(max_bytes),
                workspace.run_id + ':' + action_id,
            ],
            input=data,
        )
        written = json.loads(raw)
        return {
            "status": "ok",
            "path": relative,
            "mode": "append" if append else "write",
            "bytes": written["bytes"],
            "appended_bytes": len(data) if append else None,
            "sha256": written["sha256"],
        }

    async def recover_write(self, request: ModuleRequest[ActionRequest]) -> ActionResult | None:
        context = request.context or {}
        root = self.workspace_root_for(account_id=context.get("account_id"),
            conversation_id=context.get("conversation_id"), run_id=request.run_id)
        journal = root / ".zhouxu-writes.sqlite3"
        if not journal.is_file():
            return None
        with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True) as db:
            exists = db.execute("SELECT 1 FROM writes WHERE id=?",
                (request.run_id + ':' + request.input.action_id,)).fetchone()
        if exists is None:
            return None
        return await self.execute("write", request)

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
        if target_host.is_file() and target_host.stat().st_size >= 5:
            with target_host.open("rb") as stream:
                if stream.read(5) == b"%PDF-":
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
            if not path.is_file():
                continue
            rel_path = path.relative_to(workspace.root)
            if any(part.startswith(".") for part in rel_path.parts) or (
                rel_path.parts and rel_path.parts[0] == "work"
            ):
                continue
            if path.suffix.lower() in {".docx", ".docm"}:
                relative = rel_path.as_posix()
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

    def _artifact_metadata(
        self,
        root: Path,
        baseline: dict[str, str],
        workspace: _RunWorkspace | None = None,
        owned_paths: tuple[str, ...] = (),
    ) -> list[dict]:
        if workspace is not None:
            self._render_previews(workspace)
        current = self._snapshot(root)
        changed = [
            path
            for path, digest in current.items()
            if baseline.get(path) != digest or path in owned_paths
        ]
        artifacts: list[dict] = []
        for relative in sorted(changed):
            path = root / relative
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
            rel_parts = Path(item["relative_path"]).parts
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
                    # A paper compiler's PDF is a real deliverable, so keep it
                    # in the file list while also making it the DOCX preview.
                    # Other generated PDFs remain preview-only as before.
                    if not rel_parts or rel_parts[0] != "papers":
                        item["kind"] = "preview"
                    item["preview_of_artifact_id"] = document["artifact_id"]
                    document["preview_artifact_id"] = item["artifact_id"]
        return artifacts

    def collect_artifacts(
        self,
        run_id: str,
        *,
        completed: bool,
        account_id: str | None = None,
        conversation_id: str | None = None,
        baseline: dict[str, str] | None = None,
        owned_paths: tuple[str, ...] = (),
    ) -> list[dict]:
        workspace = self._runs.get(run_id)
        if workspace is not None:
            root = workspace.root
            effective_baseline = baseline if baseline is not None else workspace.before
            return self._artifact_metadata(root, effective_baseline, workspace if completed else None, owned_paths)
        if baseline is None and account_id is None and conversation_id is None:
            return []
        root = self.workspace_root_for(
            account_id=account_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )
        if not root.exists():
            return []
        return self._artifact_metadata(root, baseline or {}, None, owned_paths)

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
