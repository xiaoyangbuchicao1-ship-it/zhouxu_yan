"""Mandatory Bubblewrap isolation for Linux workspace commands.

Only the current workspace is writable. System libraries and approved Skill
packages are read-only. PID/network namespaces and an inner seccomp filter
remain mandatory; a host without those features fails instead of falling back.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from backend.config.settings import BACKEND_ROOT, Settings
from backend.infrastructure.execution.workspace import (
    CommandRuntime, WorkspaceExecutionProvider, _decode,
)


def _parse_bytes(value: str) -> int:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([kmg]?)", value.strip().lower())
    if not match:
        raise ValueError("execution memory limit is invalid")
    amount = int(float(match[1]) * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[match[2]])
    if amount <= 0:
        raise ValueError("execution memory limit must be positive")
    return amount


@dataclass
class _NativeProcess:
    workspace: Path
    settings: Settings
    readonly_paths: tuple[Path, ...] = ()
    packages_root: Path | None = None
    started: bool = False
    _closing: bool = field(default=False, init=False)
    _active: set[subprocess.Popen] = field(default_factory=set, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)

    def start(self) -> None:
        with self._lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if self._closing:
            raise RuntimeError("runtime_closed")
        if self.started:
            return
        if sys.platform != "linux" or not shutil.which("bwrap"):
            raise RuntimeError("execution_unavailable")
        self.workspace = self.workspace.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.workspace.chmod(0o700)
        package_path = self.packages_root
        if package_path is None:
            raise ValueError("trusted Skill package root is required")
        package_path = package_path.absolute()
        if package_path.exists() and package_path.is_symlink():
            raise ValueError("trusted Skill package root cannot be a symlink")
        package_path.mkdir(parents=True, exist_ok=True)
        if package_path.resolve() != package_path:
            raise ValueError("trusted Skill package root cannot traverse a symlink")
        self.packages_root = package_path
        self.started = True

    @staticmethod
    def _environment() -> dict[str, str]:
        return {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/tmp/home", "TMPDIR": "/tmp", "TEMP": "/tmp", "TMP": "/tmp",
            "XDG_CACHE_HOME": "/tmp/home/.cache", "XDG_CONFIG_HOME": "/tmp/home/.config",
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def _sandbox_args(self, args: list[str]) -> list[str]:
        if (self.workspace / ".skills").is_symlink():
            raise ValueError("Skill package mount cannot be a symlink")
        command = [
            "bwrap", "--die-with-parent", "--new-session",
            "--unshare-user", "--unshare-ipc", "--unshare-pid",
            "--unshare-net", "--unshare-uts", "--unshare-cgroup",
            "--uid", "65534", "--gid", "65534", "--cap-drop", "ALL",
            "--proc", "/proc", "--dev", "/dev",
            "--size", str(_parse_bytes(self.settings.execution.tmpfs_size)),
            "--tmpfs", "/tmp", "--dir", "/tmp/home",
        ]
        for path in ("/usr", "/bin", "/lib", "/lib64", "/etc/fonts", "/etc/ld.so.cache"):
            if Path(path).exists():
                command.extend(["--ro-bind", path, path])
        command.extend(["--bind", str(self.workspace), "/workspace", "--dir", "/workspace/.skills"])
        command.extend(["--ro-bind", str(self.packages_root), "/workspace/.skills"])
        configured = self.settings.research_cards.skill_dir
        builtin = Path(configured) if configured else BACKEND_ROOT / "skills/csl-law-paper-generator"
        if not builtin.is_absolute():
            builtin = BACKEND_ROOT.parent / builtin
        for path in (builtin, *self.readonly_paths):
            source = path.resolve(strict=True)
            if source == self.workspace or self.workspace in source.parents:
                destination = Path("/workspace") / source.relative_to(self.workspace)
            else:
                destination = source
            command.extend(["--ro-bind", str(source), str(destination)])
        command.extend(["--chdir", "/workspace", "--", *args])
        return command

    @staticmethod
    def _kill(process: subprocess.Popen) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            # Linux kills all namespace members when its PID 1 exits,
            # including detached grandchildren.
            return

    def _run(self, args: list[str], *, timeout: float = 30, check: bool = True,
             input: bytes | None = None) -> subprocess.CompletedProcess:
        self.start()
        if timeout <= 0:
            raise ValueError("execution timeout must be positive")
        cfg = self.settings.execution
        launcher = Path(__file__).with_name("sandbox_launcher.py")
        command = [sys.executable, "-I", "-B", str(launcher),
                   str(_parse_bytes(cfg.memory)), str(max(1, math.ceil(timeout))),
                   str(cfg.pids_limit), str(cfg.max_file_bytes),
                   *self._sandbox_args(args)]
        # File-backed capture bounds API memory and avoids descendants keeping
        # a PIPE open. The command's RLIMIT_FSIZE bounds each output file.
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            with self._lock:
                if self._closing:
                    raise RuntimeError("runtime_closed")
                process = subprocess.Popen(
                    command, cwd=self.workspace, env=self._environment(),
                    stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                    stdout=stdout_file, stderr=stderr_file, start_new_session=True,
                    close_fds=True,
                )
                self._active.add(process)
            try:
                try:
                    process.communicate(input=input, timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    self._kill(process)
                    process.communicate(timeout=3)
                    raise TimeoutError from exc
            finally:
                with self._lock:
                    self._active.discard(process)
            stdout_file.seek(0)
            stderr_file.seek(0)
            maximum = cfg.max_output_chars * 4 + cfg.max_read_chars * 6 + 4096
            stdout, stderr = stdout_file.read(maximum + 1), stderr_file.read(maximum + 1)
            if len(stdout) > maximum or len(stderr) > maximum:
                raise RuntimeError("execution_output_limit")
        result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        if check and result.returncode != 0:
            raise RuntimeError(_decode(result.stderr).strip() or "sandbox_command_failed")
        return result

    def exec(self, args: list[str], *, timeout: float | None = None, check: bool = True,
             input: bytes | None = None) -> subprocess.CompletedProcess:
        return self._run(args, timeout=timeout if timeout is not None else self.settings.execution.command_timeout_seconds,
                         check=check, input=input)

    def python(self, code: str, args: list[str] | None = None, *, timeout: float | None = None,
               check: bool = True, input: bytes | None = None) -> subprocess.CompletedProcess:
        executable = self.settings.execution.native_python or sys.executable
        return self.exec([executable, "-c", code, *(args or [])], timeout=timeout, check=check, input=input)

    def bash(self, command: str, *, timeout: float | None = None, check: bool = True) -> subprocess.CompletedProcess:
        executable = self.settings.execution.native_bash or "/bin/bash"
        return self.exec([executable, "--noprofile", "--norc", "-c", command], timeout=timeout, check=check)

    def close(self) -> None:
        with self._lock:
            self._closing = True
            active = list(self._active)
        for process in active:
            self._kill(process)
        self.started = False


class NativeProvider(WorkspaceExecutionProvider):
    def _create_runtime(self, run_id: str, root: Path) -> CommandRuntime:
        candidate = root.absolute()
        resolved = root.resolve(strict=False)
        provider_root = self.root.resolve(strict=False)
        if candidate != resolved or provider_root not in resolved.parents:
            raise ValueError("workspace path must remain inside the provider root")
        return _NativeProcess(root, self.settings, packages_root=self.package_root_for(root))


NativeExecutionTools = NativeProvider
