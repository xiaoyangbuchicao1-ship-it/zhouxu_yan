"""Real Linux process-boundary checks; fixtures never contain credentials."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.config.settings import load_settings
from backend.infrastructure.execution.native import NativeProvider, _NativeProcess


@unittest.skipUnless(sys.platform == "linux", "production execution runs on Linux")
class NativeIsolationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.workspace = self.root / "current-workspace"
        self.packages = self.root / "trusted-packages"
        self.runtime = _NativeProcess(self.workspace, load_settings(), packages_root=self.packages)
        self.addCleanup(self.runtime.close)

    def test_python_and_subprocess_work_with_real_resource_limits(self):
        result = self.runtime.python(
            "import json,pathlib,resource,subprocess; "
            "from docx import Document; "
            "p=pathlib.Path('result.txt'); p.write_text('saved'); "
            "child=subprocess.check_output(['/bin/bash','-c','printf child-ok']); "
            "print(json.dumps({'text':p.read_text(),'child':child.decode(), "
            "'memory':resource.getrlimit(resource.RLIMIT_AS)[0], "
            "'cpu':resource.getrlimit(resource.RLIMIT_CPU)[0]}))",
            timeout=10,
        )
        self.assertEqual(json.loads(result.stdout), {
            "text": "saved", "child": "child-ok", "memory": 1024**3, "cpu": 10,
        })
        self.assertEqual((self.workspace / "result.txt").read_text(), "saved")

    def test_provider_rejects_workspace_symlink_escape(self):
        settings = load_settings()
        settings.execution.workspace_root = str(self.root / "provider-root")
        provider = NativeProvider(settings)
        outside = self.root / "other-account"
        outside.mkdir()
        account = Path(settings.execution.workspace_root) / "account"
        account.mkdir(parents=True)
        (account / "conversation").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            provider._create_runtime("run", account / "conversation")

    def test_command_cannot_read_another_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            other = parent / "other-account"
            other.mkdir()
            sentinel = other / "public-test-sentinel.txt"
            sentinel.write_text("ISOLATION_TEST_ONLY", encoding="utf-8")
            runtime = _NativeProcess(
                parent / "current-workspace", load_settings(),
                packages_root=parent / "trusted-packages",
            )
            try:
                result = runtime.python(
                    "from pathlib import Path; import sys; "
                    "print(Path(sys.argv[1]).read_text())",
                    [str(sentinel)], check=False,
                )
                self.assertNotEqual(result.returncode, 0, "another account's file was readable")
                self.assertNotIn(b"ISOLATION_TEST_ONLY", result.stdout)
                self.assertIn(b"FileNotFoundError", result.stderr)
            finally:
                runtime.close()

        self.runtime.start()
        sentinel = self.root / "public-test-sentinel.txt"
        sentinel.write_text("OUTSIDE_TEST_ONLY")
        (self.workspace / "escape").symlink_to(sentinel)
        with patch.dict(os.environ, {"SANDBOX_PARENT_TEST": "PARENT_TEST_ONLY"}):
            peer = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                    env=dict(os.environ), stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            self.addCleanup(peer.wait)
            self.addCleanup(peer.kill)
            result = self.runtime.python("""
import os,pathlib,sys
visible=[]
for item in pathlib.Path('/proc').glob('[0-9]*/environ'):
    try: visible.append(item.read_bytes())
    except (FileNotFoundError, PermissionError, ProcessLookupError): pass
assert not any(b'PARENT_TEST_ONLY' in content for content in visible)
assert 'SANDBOX_PARENT_TEST' not in os.environ
for p in [pathlib.Path('escape'), pathlib.Path('/proc/1/root') / sys.argv[1].lstrip('/'), pathlib.Path('/proc') / sys.argv[2] / 'environ', pathlib.Path('/app/runtime.env'), pathlib.Path('/root')]:
    assert not p.exists(), str(p)
print('private')
""", [str(sentinel), str(peer.pid)])
        self.assertEqual(result.stdout.strip(), b"private")

    def test_skill_package_is_executable_but_cannot_be_changed(self):
        self.runtime.start()
        script = self.packages / "hello.py"
        script.write_text("print('package-ok')")
        result = self.runtime.bash("python .skills/hello.py")
        self.assertEqual(result.stdout.strip(), b"package-ok")
        result = self.runtime.python("""
from pathlib import Path
import errno
try: Path('.skills/hello.py').write_text('changed')
except OSError as error: assert error.errno == errno.EROFS
else: raise AssertionError('package writable')
try: Path('.skills').rename('moved-package')
except OSError as error: assert error.errno in (errno.EBUSY,errno.EROFS)
else: raise AssertionError('package mount movable')
print('readonly')
""")
        self.assertEqual(result.stdout.strip(), b"readonly")
        self.assertEqual(script.read_text(), "print('package-ok')")

    def test_network_and_new_namespaces_are_denied(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            result = self.runtime.python("""
import socket,subprocess,sys
for family in (socket.AF_INET,socket.AF_INET6):
    try: socket.socket(family,socket.SOCK_STREAM)
    except PermissionError: pass
    else: raise AssertionError('network socket allowed')
probe=subprocess.run(['unshare','--user','--map-root-user','true'],capture_output=True)
assert probe.returncode != 0
assert b'Operation not permitted' in probe.stderr
print('blocked')
""", [str(server.getsockname()[1])])
        self.assertEqual(result.stdout.strip(), b"blocked")

    def test_timeout_kills_detached_descendants(self):
        child = "import pathlib,time\np=pathlib.Path('heartbeat')\nwhile True:\n p.write_text(str(time.time_ns()))\n time.sleep(.02)"
        code = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',{child!r}],start_new_session=True); "
            "time.sleep(30)"
        )
        with self.assertRaises(TimeoutError):
            self.runtime.python(code, timeout=3)
        heartbeat = self.workspace / "heartbeat"
        self.assertTrue(heartbeat.is_file(), "descendant never started")
        # Detached children would keep changing the heartbeat if only the
        # command leader/process group had been killed.
        time.sleep(.15)
        stopped = heartbeat.read_text()
        time.sleep(.25)
        self.assertEqual(heartbeat.read_text(), stopped)
        # A cancelled API thread may reach execution after close() returned.
        # Closing is terminal: it cannot start a fresh namespace afterwards.
        self.runtime.close()
        with self.assertRaisesRegex(RuntimeError, "runtime_closed"):
            self.runtime.python("open('late-command','w').write('ran')")
        self.assertFalse((self.workspace / "late-command").exists())
