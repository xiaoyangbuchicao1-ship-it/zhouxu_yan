"""Native work-mode execution provider.

The provider deliberately reuses the work-mode tool contract and artifact
bookkeeping from :mod:`workspace`.  Only the process boundary changes: commands
are started directly in the per-account/conversation workspace, using a
minimal environment and OS resource limits.  This keeps the cloud deployment
free of a Docker dependency without changing Harness, Skills, or artifact
contracts.

The deployment service should run this provider under a dedicated Linux
service account.  The provider never exposes the application environment to
the command: credentials and the SQLite database are not copied into the
child environment.  This is a host-account boundary, not a Docker-equivalent
multi-tenant sandbox; an untrusted multi-tenant deployment must add a native
sandbox such as bubblewrap, systemd sandboxing, or a microVM around this
provider.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from backend.config.settings import Settings
from backend.infrastructure.execution.workspace import (
    CommandRuntime,
    WorkspaceExecutionProvider,
    _decode,
)


def _parse_bytes(value: str) -> int | None:
    """Parse the small memory syntax used by execution settings."""

    raw = str(value).strip().lower()
    units = {"k": 1024, "m": 1024**2, "g": 1024**3}
    for suffix, multiplier in units.items():
        if raw.endswith(suffix):
            try:
                return max(1, int(float(raw[:-1]) * multiplier))
            except ValueError:
                return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


@dataclass
class _NativeProcess:
    """Run commands in a workspace with a killable process group."""

    workspace: Path
    settings: Settings
    started: bool = False
    _active: set[subprocess.Popen] = field(default_factory=set, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)

    def start(self) -> None:
        if self.started:
            return
        self.workspace.mkdir(parents=True, exist_ok=True)
        # These directories replace the container's writable /tmp and HOME.
        (self.workspace / ".native-tmp").mkdir(exist_ok=True)
        (self.workspace / ".native-home").mkdir(exist_ok=True)
        try:
            self.workspace.chmod(0o700)
            (self.workspace / ".native-tmp").chmod(0o700)
            (self.workspace / ".native-home").chmod(0o700)
        except OSError:
            # Windows and filesystems without chmod still use the same path
            # checks.  The cloud service account provides the real boundary.
            pass
        self.started = True

    def _environment(self) -> dict[str, str]:
        """Return only non-secret runtime variables for child processes."""

        path = os.environ.get("PATH", "")
        env = {
            "PATH": path,
            "HOME": str(self.workspace / ".native-home"),
            "TMPDIR": str(self.workspace / ".native-tmp"),
            "TEMP": str(self.workspace / ".native-tmp"),
            "TMP": str(self.workspace / ".native-tmp"),
            "XDG_CACHE_HOME": str(self.workspace / ".native-home" / ".cache"),
            "XDG_CONFIG_HOME": str(self.workspace / ".native-home" / ".config"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        # Windows subprocesses need these values to locate the system runtime.
        for name in ("SystemRoot", "WINDIR", "PATHEXT"):
            if os.environ.get(name):
                env[name] = os.environ[name]
        return env

    def _preexec(self, timeout: float):
        if os.name != "posix":
            return None
        cfg = self.settings.execution
        cpu_seconds = max(1, int(timeout))
        memory = _parse_bytes(cfg.memory)
        pids = max(16, int(cfg.pids_limit))

        def apply_limits() -> None:
            import resource

            # The limits supplement the service's cgroup.  They are best
            # effort because some kernels/filesystems do not expose every
            # resource; failure to set one limit must not prevent the command.
            limits = [
                (resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1)),
                (resource.RLIMIT_NPROC, (pids, pids)),
            ]
            if memory is not None and hasattr(resource, "RLIMIT_AS"):
                limits.append((resource.RLIMIT_AS, (memory, memory)))
            for kind, value in limits:
                try:
                    resource.setrlimit(kind, value)
                except (OSError, ValueError):
                    continue

        return apply_limits

    @staticmethod
    def _kill(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (OSError, ProcessLookupError):
            pass

    def _run(
        self,
        args: list[str],
        *,
        timeout: float = 30,
        check: bool = True,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess:
        self.start()
        try:
            process = subprocess.Popen(
                args,
                cwd=self.workspace,
                env=self._environment(),
                stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=os.name == "posix",
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    if os.name == "nt"
                    else 0
                ),
                preexec_fn=self._preexec(timeout),
            )
        except FileNotFoundError as exc:
            raise RuntimeError("execution_unavailable") from exc
        with self._lock:
            self._active.add(process)
        try:
            try:
                stdout, stderr = process.communicate(
                    input=input, timeout=max(1, timeout)
                )
            except subprocess.TimeoutExpired as exc:
                self._kill(process)
                stdout, stderr = process.communicate(timeout=3)
                raise TimeoutError from exc
        finally:
            with self._lock:
                self._active.discard(process)
        result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        if check and result.returncode != 0:
            raise RuntimeError(
                _decode(result.stderr).strip() or "native_command_failed"
            )
        return result

    def exec(
        self,
        args: list[str],
        *,
        timeout: float | None = None,
        check: bool = True,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess:
        return self._run(
            args,
            timeout=timeout or self.settings.execution.command_timeout_seconds + 5,
            check=check,
            input=input,
        )

    def python(
        self,
        code: str,
        args: list[str] | None = None,
        *,
        timeout: float | None = None,
        check: bool = True,
        input: bytes | None = None,
    ) -> subprocess.CompletedProcess:
        executable = (
            getattr(self.settings.execution, "native_python", None) or sys.executable
        )
        return self.exec(
            [executable, "-c", code, *(args or [])],
            timeout=timeout,
            check=check,
            input=input,
        )

    def bash(
        self,
        command: str,
        *,
        timeout: float | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        executable = getattr(
            self.settings.execution, "native_bash", None
        ) or shutil.which("bash")
        if not executable:
            raise RuntimeError("execution_unavailable")
        seconds = max(
            1, int(timeout or self.settings.execution.command_timeout_seconds)
        )
        return self.exec([executable, "-lc", command], timeout=seconds, check=check)

    def close(self) -> None:
        with self._lock:
            active = list(self._active)
        for process in active:
            self._kill(process)
        self.started = False


class NativeProvider(WorkspaceExecutionProvider):
    """Run the shared tools under the host's dedicated application account."""

    def _create_runtime(self, run_id: str, root: Path) -> CommandRuntime:
        return _NativeProcess(root, self.settings)


NativeExecutionTools = NativeProvider
