"""Docker process isolation for the shared workspace tools."""

from __future__ import annotations

import shlex
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from backend.config.settings import Settings
from backend.infrastructure.execution.workspace import (
    CommandRuntime,
    WorkspaceExecutionProvider,
    _decode,
    _safe_part,
)


@dataclass
class _DockerContainer:
    name: str
    workspace: Path
    settings: Settings
    packages_root: Path | None = None
    started: bool = False
    _closing: bool = field(default=False, init=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)

    def _run(
        self,
        args: list[str],
        *,
        timeout: float = 30,
        check: bool = True,
        input: bytes | None = None,
    ):
        try:
            result = subprocess.run(
                args, capture_output=True, timeout=timeout, check=False, input=input
            )
        except FileNotFoundError as exc:
            raise RuntimeError("execution_unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError from exc
        if check and result.returncode != 0:
            raise RuntimeError(
                _decode(result.stderr).strip() or "docker_command_failed"
            )
        return result

    def start(self):
        with self._lock:
            if self._closing:
                raise RuntimeError("runtime_closed")
            if self.started:
                return
            self.workspace.mkdir(parents=True, exist_ok=True)
            try:
                self.workspace.chmod(0o777)
            except OSError:
                pass
            cfg = self.settings.execution
            mounts = []
            if self.packages_root is not None:
                self.packages_root.mkdir(parents=True, exist_ok=True)
                mounts = ["--mount", f"type=bind,source={self.packages_root},target=/workspace/.skills,readonly"]
            self._run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    self.name,
                    "--user",
                    "65534:65534",
                    "--network",
                    "none",
                    "--read-only",
                    "--tmpfs",
                    f"/tmp:rw,noexec,nosuid,size={cfg.tmpfs_size}",
                    "--tmpfs",
                    "/run:rw,noexec,nosuid,size=16m",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--pids-limit",
                    str(cfg.pids_limit),
                    "--memory",
                    cfg.memory,
                    "--cpus",
                    cfg.cpus,
                    "--env",
                    "HOME=/tmp/home",
                    "--env",
                    "TMPDIR=/tmp",
                    "--env",
                    "PYTHONUNBUFFERED=1",
                    "--env",
                    "PYTHONIOENCODING=utf-8",
                    "--mount",
                    f"type=bind,source={self.workspace},target=/workspace",
                    *mounts,
                    "--workdir",
                    "/workspace",
                    cfg.image,
                    "sleep",
                    "86400",
                ],
                timeout=45,
            )
            self.started = True

    def exec(
        self,
        args: list[str],
        *,
        timeout: float | None = None,
        check: bool = True,
        input: bytes | None = None,
    ):
        self.start()
        return self._run(
            [
                "docker",
                "exec",
                *(["-i"] if input is not None else []),
                "--user",
                "65534:65534",
                "--workdir",
                "/workspace",
                self.name,
                *args,
            ],
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
    ):
        return self.exec(
            ["python", "-c", code, *(args or [])],
            timeout=timeout,
            check=check,
            input=input,
        )

    def bash(self, command: str, *, timeout: float | None = None, check: bool = True):
        seconds = max(
            1, int(timeout or self.settings.execution.command_timeout_seconds)
        )
        wrapped = f"timeout --signal=KILL {seconds}s bash -lc {shlex.quote(command)}"
        return self.exec(["bash", "-lc", wrapped], timeout=seconds + 8, check=check)

    def close(self):
        with self._lock:
            self._closing = True
            started = self.started
            self.started = False
        if not started:
            return
        self._run(["docker", "rm", "-f", self.name], timeout=30, check=False)


class DockerProvider(WorkspaceExecutionProvider):
    """Keep each Run in a disposable container mounting only its session files."""

    def _create_runtime(self, run_id: str, root: Path) -> CommandRuntime:
        return _DockerContainer(
            name=f"zhouxu-work-{_safe_part(run_id)[:48]}",
            workspace=root,
            settings=self.settings,
            packages_root=self.package_root_for(root),
        )


# Preserve the original import name for existing local callers.
WorkspaceExecutionTools = DockerProvider
