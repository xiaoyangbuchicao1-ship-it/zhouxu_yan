"""Real local HTTP tests for stream access, with no model or production data.

The control service only exposes fixed, test-owned Run snapshots. It never
executes work; uvicorn, account authentication and both HTTP streams are real.
"""

from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import uvicorn
from sqlalchemy import select

from backend.application.accounts.service import AccountService
from backend.application.admin import AdminService
from backend.application.workspace.conversations import WorkspaceService
from backend.config.settings import AuthSettings, DatabaseSettings
from backend.contracts.events.runtime import RunEvent
from backend.contracts.events.streaming import StreamEvent
from backend.contracts.harness.lifecycle import RunEnvelope, SessionSnapshot
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import SessionModel
from backend.interfaces.api.app import create_app
from backend.interfaces.api.control_plane import SchedulerCommandError


class StaticStreamControl:
    """Test fixture, not a production executor or an execution success claim."""

    def __init__(self, run: RunEnvelope):
        self.snapshot = SessionSnapshot(session_id=run.session_id, runs=[run])

    async def run(self, *, session_id: str, run_id: str) -> RunEnvelope:
        if session_id != self.snapshot.session_id:
            raise SchedulerCommandError("session not found")
        for run in self.snapshot.runs:
            if run.run_id == run_id:
                return run
        raise SchedulerCommandError("run not found")

    async def session(self, *, session_id: str) -> SessionSnapshot:
        if session_id != self.snapshot.session_id:
            raise SchedulerCommandError("session not found")
        return self.snapshot


class EventStreamAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = DatabaseSettings(url=f"sqlite:///{Path(self.tmp.name) / 'stream.sqlite3'}")
        self.database = Database(settings)
        self.database.initialize()
        self.accounts = AccountService(self.database, settings, AuthSettings(password_min_length=8))
        self.admin = self.accounts.ensure_admin(email="admin@example.com", password="stream-test-password")
        self.user = self.accounts.register(email="user@example.com", password="stream-test-password")
        workspace = WorkspaceService(self.database)
        conversation = workspace.create(account_id=self.user.account.id, title="Stream access test")
        self.run = RunEnvelope(
            run_id="own-run", session_id=conversation.id, conversation_id=conversation.id,
            account_id=self.user.account.id, payload={"text": "test-only"}, status="running",
        )
        self.control = StaticStreamControl(self.run)
        self.app = create_app(
            control_service=self.control, account_service=self.accounts,
            workspace_service=workspace,
        )
        # Projections are unrelated to stream access and are not execution evidence.
        self.app.state.project_run = lambda run: None
        self.hub = self.app.state.event_hub
        self.server = uvicorn.Server(uvicorn.Config(
            self.app, host="127.0.0.1", port=0, lifespan="off", access_log=False,
            log_level="error",
        ))
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(128)
        self.sock.setblocking(False)
        port = self.sock.getsockname()[1]
        self.server_task = asyncio.create_task(self.server.serve(sockets=[self.sock]))
        for _ in range(100):
            if self.server.started:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(self.server.started, "temporary HTTP server did not start")
        self.client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=3)

    async def asyncTearDown(self):
        await self.client.aclose()
        self.server.should_exit = True
        await asyncio.wait_for(self.server_task, timeout=3)
        self.sock.close()
        self.database.close()
        self.tmp.cleanup()

    def stream_path(self, kind: str) -> str:
        prefix = f"/workspace/conversations/{self.run.session_id}" if kind == "workbench" else f"/harness/sessions/{self.run.session_id}"
        return f"{prefix}/events?run_id={self.run.run_id}&timeout_seconds=2"

    async def assert_revoked_stream_stops(self, kind: str, revoke):
        headers = {"Authorization": f"Bearer {self.user.token}"}
        async with self.client.stream("GET", self.stream_path(kind), headers=headers) as response:
            self.assertEqual(response.status_code, 200)
            lines = response.aiter_lines()
            self.assertEqual(json.loads(await anext(lines))["type"], "lifecycle")
            revoke()
            self.hub.emit_stream(StreamEvent(run_id=self.run.run_id, type="delta", text="must-not-be-delivered"))
            with self.assertRaises(StopAsyncIteration):
                await asyncio.wait_for(anext(lines), timeout=0.8)
        self.assertEqual(self.run.status, "running", "revoking a stream must not cancel its background Run")

    async def test_disabled_account_loses_both_open_streams(self):
        for kind in ("workbench", "harness"):
            with self.subTest(stream=kind):
                self.hub.stream_events.clear()
                try:
                    await self.assert_revoked_stream_stops(kind, lambda: AdminService(self.database).update_account(
                        actor_id=self.admin.id, account_id=self.user.account.id, status="disabled",
                    ))
                finally:
                    AdminService(self.database).update_account(
                        actor_id=self.admin.id, account_id=self.user.account.id, status="active",
                    )

    async def test_logged_out_session_loses_both_open_streams(self):
        for kind in ("workbench", "harness"):
            with self.subTest(stream=kind):
                self.hub.stream_events.clear()
                self.user = self.accounts.login(email="user@example.com", password="stream-test-password")
                await self.assert_revoked_stream_stops(kind, lambda: self.accounts.logout(self.user.token))

    async def test_expired_session_loses_both_open_streams(self):
        def expire():
            with self.database.session() as session:
                row = session.scalar(select(SessionModel).where(
                    SessionModel.token_hash == self.accounts._hash_token(self.user.token),
                ))
                row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                session.commit()

        for kind in ("workbench", "harness"):
            with self.subTest(stream=kind):
                self.hub.stream_events.clear()
                self.user = self.accounts.login(email="user@example.com", password="stream-test-password")
                await self.assert_revoked_stream_stops(kind, expire)

    async def test_harness_session_stream_never_contains_other_sessions_events(self):
        self.run.status = "succeeded"
        for run_id in (self.run.run_id, "another-account-run"):
            self.hub.emit(RunEvent(run_id=run_id, module="test", phase="started"))
            self.hub.emit_stream(StreamEvent(run_id=run_id, type="delta", text=run_id))
        response = await self.client.get(
            f"/harness/sessions/{self.run.session_id}/events?timeout_seconds=1",
            headers={"Authorization": f"Bearer {self.user.token}"},
        )
        self.assertEqual(response.status_code, 200)
        rows = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual({row["event"]["run_id"] for row in rows if "event" in row}, {self.run.run_id})
        self.assertEqual({row["type"] for row in rows}, {"lifecycle", "runtime", "stream"})

    async def test_open_stream_rechecks_are_read_only(self):
        def last_seen():
            with self.database.session() as session:
                return session.scalar(select(SessionModel.last_seen_at).where(
                    SessionModel.token_hash == self.accounts._hash_token(self.user.token),
                ))

        for kind in ("workbench", "harness"):
            with self.subTest(stream=kind):
                self.hub.stream_events.clear()
                async with self.client.stream(
                    "GET", self.stream_path(kind),
                    headers={"Authorization": f"Bearer {self.user.token}"},
                ) as response:
                    self.assertEqual(response.status_code, 200)
                    lines = response.aiter_lines()
                    self.assertEqual(json.loads(await anext(lines))["type"], "lifecycle")
                    connected_at = last_seen()
                    self.hub.emit_stream(StreamEvent(run_id=self.run.run_id, type="delta", text="still-connected"))
                    delivered = json.loads(await asyncio.wait_for(anext(lines), timeout=0.8))
                    self.assertEqual(delivered["type"], "delta" if kind == "workbench" else "stream")
                    await asyncio.sleep(0.13)
                    self.assertEqual(last_seen(), connected_at, "stream checks must not write last_seen_at")
        before_regular_request = last_seen()
        self.accounts.authenticate(self.user.token)
        self.assertGreater(last_seen(), before_regular_request, "regular authentication keeps its existing activity update")


if __name__ == "__main__":
    unittest.main()
