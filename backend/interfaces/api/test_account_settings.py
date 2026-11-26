from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx

from datetime import datetime, timezone

from backend.application.accounts.service import AccountService
from backend.application.maritime.service import MaritimeOffice
from backend.config.settings import AuthSettings, DatabaseSettings, load_settings
from backend.contracts.harness.lifecycle import RunEnvelope
from backend.infrastructure.database.connection import Database
from backend.interfaces.api.app import create_app


class _FakeControlService:
    def __init__(self):
        self.submitted: list[dict] = []

    async def submit(
        self,
        *,
        session_id,
        conversation_id=None,
        account_id=None,
        client_request_id=None,
        retry_of_run_id=None,
        graph=None,
        runtime_policy=None,
        payload=None,
    ):
        self.submitted.append(
            {
                "session_id": session_id,
                "conversation_id": conversation_id,
                "text": payload.text,
                "facts": dict(payload.facts),
            }
        )
        run = RunEnvelope(
            run_id="run-memory-test-1",
            session_id=session_id,
            conversation_id=conversation_id or session_id,
            account_id=account_id,
            status="queued",
            revision=1,
            payload=payload.model_dump(mode="json"),
            created_at=datetime.now(timezone.utc),
        )
        return None, run

    def list_runs(self, **_kwargs):
        return []


class AccountSettingsHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        db_settings = DatabaseSettings(url=f"sqlite:///{root / 'app.sqlite3'}")
        self.database = Database(db_settings)
        self.database.initialize()
        self.accounts = AccountService(
            self.database, db_settings, AuthSettings(password_min_length=12)
        )
        self.user = self.accounts.register(
            email="sailor@example.com",
            password="initial-password-1234",
            display_name="航运研究员",
        )
        self.other = self.accounts.register(
            email="other@example.com",
            password="other-password-1234",
            display_name="他人",
        )
        self.control_service = _FakeControlService()
        self.app = create_app(
            settings=load_settings(),
            account_service=self.accounts,
            control_service=self.control_service,
        )
        self.app.state.maritime_office = MaritimeOffice(root / "office.sqlite3")
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.database.close()
        self.tmp.cleanup()

    async def test_preferences_profile_password_commands_and_memory_injection(self):
        headers = {"Authorization": f"Bearer {self.user.token}"}

        # 1. Profile & change password
        profile = await self.client.patch(
            "/account/profile",
            headers=headers,
            json={"display_name": "高级海商律师"},
        )
        self.assertEqual(profile.status_code, 200, profile.text)
        self.assertEqual(profile.json()["display_name"], "高级海商律师")

        bad_pw = await self.client.post(
            "/account/change-password",
            headers=headers,
            json={"current_password": "wrong-password-1234", "new_password": "brand-new-password-99"},
        )
        self.assertEqual(bad_pw.status_code, 401)

        good_pw = await self.client.post(
            "/account/change-password",
            headers=headers,
            json={"current_password": "initial-password-1234", "new_password": "brand-new-password-99"},
        )
        self.assertEqual(good_pw.status_code, 200, good_pw.text)

        old_login = await self.client.post(
            "/auth/login",
            json={"email": "sailor@example.com", "password": "initial-password-1234"},
        )
        self.assertEqual(old_login.status_code, 401)
        new_login = await self.client.post(
            "/auth/login",
            json={"email": "sailor@example.com", "password": "brand-new-password-99"},
        )
        self.assertEqual(new_login.status_code, 200)

        # 2. Preferences persistence & validation
        prefs = await self.client.get("/account/preferences", headers=headers)
        self.assertEqual(prefs.status_code, 200)
        self.assertEqual(prefs.json()["default_mode"], "chat")

        updated_prefs = await self.client.patch(
            "/account/preferences",
            headers=headers,
            json={
                "default_mode": "work",
                "default_thinking_mode": "medium",
                "concise_process": True,
                "compact_density": True,
                "reduced_motion": True,
                "memory_enabled": True,
                "memory_text": "回答先给结论并逐条引用《海商法》法条。",
            },
        )
        self.assertEqual(updated_prefs.status_code, 200, updated_prefs.text)
        self.assertEqual(updated_prefs.json()["default_mode"], "work")
        self.assertTrue(updated_prefs.json()["concise_process"])
        self.assertTrue(updated_prefs.json()["memory_enabled"])

        # 3. Slash commands seeded + custom CRUD + isolation
        cmds = await self.client.get("/account/commands", headers=headers)
        self.assertEqual(cmds.status_code, 200)
        self.assertGreaterEqual(len(cmds.json()["commands"]), 3)

        created_cmd = await self.client.post(
            "/account/commands",
            headers=headers,
            json={
                "trigger": "/MyRule",
                "title": "己方免责抗辩核对",
                "content": "按《海商法》第五十一条梳理承运人免责抗辩。",
                "mode_hint": "work",
            },
        )
        self.assertEqual(created_cmd.status_code, 201, created_cmd.text)
        cmd_id = created_cmd.json()["id"]
        self.assertEqual(created_cmd.json()["trigger"], "myrule")

        dup_cmd = await self.client.post(
            "/account/commands",
            headers=headers,
            json={"trigger": "myrule", "title": "重复", "content": "重复内容"},
        )
        self.assertEqual(dup_cmd.status_code, 409)

        other_del = await self.client.delete(
            f"/account/commands/{cmd_id}",
            headers={"Authorization": f"Bearer {self.other.token}"},
        )
        self.assertEqual(other_del.status_code, 404)

        # 4. Run submission injects server user_memory & strips client spoofed user_memory
        conv = await self.client.post(
            "/workspace/conversations",
            headers=headers,
            json={"title": "长期记忆验证", "mode": "work"},
        )
        self.assertEqual(conv.status_code, 201)
        cid = conv.json()["conversation"]["conversation_id"]
        run_res = await self.client.post(
            f"/workspace/conversations/{cid}/runs",
            headers=headers,
            json={
                "conversation_id": cid,
                "text": "请分析货损免责条款",
                "model_profile_id": "deepseek-flash",
                "client_request_id": "req-memory-1",
                "facts": {"user_memory": "CLIENT SPOOFED SHOULD BE STRIPPED"},
            },
        )
        self.assertEqual(run_res.status_code, 202, run_res.text)
        self.assertEqual(
            self.control_service.submitted[-1]["facts"].get("user_memory"),
            "回答先给结论并逐条引用《海商法》法条。",
        )

        # 5. Summary endpoint & built-in skills status split without exposing builtin instructions
        summary = await self.client.get("/account/summary", headers=headers)
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertEqual(summary.json()["account"]["display_name"], "高级海商律师")
        self.assertEqual(summary.json()["usage"]["conversation_count"], 1)

        skills = await self.client.get("/maritime/skills", headers=headers)
        self.assertEqual(skills.status_code, 200, skills.text)
        builtin_rows = [row for row in skills.json()["skills"] if row["visibility"] == "builtin"]
        ready_ids = {row["id"] for row in builtin_rows if row.get("status") == "ready"}
        not_ready = [row["id"] for row in builtin_rows if row.get("status") != "ready"]
        self.assertEqual(
            ready_ids,
            {
                "maritime-case-research",
                "maritime-legal-research",
                "maritime-paper-research",
                "csl-law-paper-generator",
            },
        )
        modes = {row["id"]: row.get("mode_scope") for row in builtin_rows if row.get("status") == "ready"}
        self.assertEqual(
            modes,
            {
                "maritime-legal-research": "chat",
                "maritime-case-research": "chat",
                "maritime-paper-research": "chat",
                "csl-law-paper-generator": "work",
            },
        )
        self.assertEqual(not_ready, [], "没有“即将上线”这类占位技能")
        self.assertTrue(all(not row.get("instructions") for row in builtin_rows))


if __name__ == "__main__":
    unittest.main()
