from __future__ import annotations

import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx

from backend.application.accounts.service import AccountService
from backend.config.settings import AuthSettings, DatabaseSettings, load_settings
from backend.contracts.modules.loop import ActionRequest
from backend.contracts.schemas.core import ModuleRequest
from backend.infrastructure.database.connection import Database
from backend.infrastructure.tools.skills import SkillReadTool
from backend.interfaces.api.app import create_app
from backend.skills.registry import default_skill_registry


def _make_skill_zip(name: str = "custom-test-skill") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            f"{name}/SKILL.md",
            f"---\nname: {name}\ndescription: test skill package\n---\n# {name}\nUse for testing.\n",
        )
    return buf.getvalue()


class CapabilityEnforcementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env_patch = patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "sk-deepseek-dummy-1234",
                "ZHOUXU_RELAY_ENDPOINT": "https://relay.example.test",
                "ZHOUXU_RELAY_API_KEY": "sk-relay-dummy-5678",
            },
            clear=False,
        )
        self.env_patch.start()
        db_settings = DatabaseSettings(url=f"sqlite:///{Path(self.tmp.name) / 'cap.sqlite3'}")
        self.database = Database(db_settings)
        self.database.initialize()
        self.accounts = AccountService(self.database, db_settings, AuthSettings(password_min_length=8))
        self.admin = self.accounts.ensure_admin(email="admin@example.com", password="a-long-admin-password")
        self.user = self.accounts.register(email="user@example.com", password="a-long-user-password")
        self.admin_token = self.accounts.login(email="admin@example.com", password="a-long-admin-password").token
        self.user_token = self.accounts.login(email="user@example.com", password="a-long-user-password").token

        settings = load_settings().model_copy(deep=True)
        settings.database = db_settings
        settings.application["office_store_path"] = str(Path(self.tmp.name) / "office.sqlite3")
        self.app = create_app(account_service=self.accounts, settings=settings)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.database.close()
        self.env_patch.stop()
        self.tmp.cleanup()

    async def test_model_allowlist_and_disabled_and_admin_only_scope_are_enforced(self):
        admin = {"Authorization": f"Bearer {self.admin_token}"}
        user = {"Authorization": f"Bearer {self.user_token}"}

        # Mark deepseek-v4-pro as admin_only.
        scoped = await self.client.patch(
            "/admin/models/deepseek-v4-pro",
            headers=admin,
            json={"access_scope": "admin_only"},
        )
        self.assertEqual(scoped.status_code, 200, scoped.text)

        # Admin still sees deepseek-v4-pro; user does not.
        admin_models = {m["profile_id"] for g in (await self.client.get("/workspace/models", headers=admin)).json()["groups"] for m in g["models"]}
        user_models = {m["profile_id"] for g in (await self.client.get("/workspace/models", headers=user)).json()["groups"] for m in g["models"]}
        self.assertIn("deepseek-v4-pro", admin_models)
        self.assertNotIn("deepseek-v4-pro", user_models)

        # User creates a conversation and tries to bypass UI to run deepseek-v4-pro.
        conv = (await self.client.post("/workspace/conversations", headers=user, json={"title": "权限测试", "mode": "chat"})).json()["conversation"]
        conv_id = conv["conversation_id"]
        denied_scope = await self.client.post(
            f"/workspace/conversations/{conv_id}/runs",
            headers=user,
            json={
                "conversation_id": conv_id,
                "text": "你好",
                "model_profile_id": "deepseek-v4-pro",
                "client_request_id": "req-scope-1",
            },
        )
        self.assertEqual(denied_scope.status_code, 403, denied_scope.text)
        self.assertEqual(denied_scope.json()["detail"], "model_not_permitted")

        # Restrict user to allowlist=["deepseek-flash"].
        await self.client.patch(
            f"/admin/capabilities/{self.user.account.id}",
            headers=admin,
            json={"model_policy": "allowlist", "allowed_models": ["deepseek-flash"]},
        )
        filtered = {m["profile_id"] for g in (await self.client.get("/workspace/models", headers=user)).json()["groups"] for m in g["models"]}
        self.assertEqual(filtered, {"deepseek-flash"})

        denied_other = await self.client.post(
            f"/workspace/conversations/{conv_id}/runs",
            headers=user,
            json={
                "conversation_id": conv_id,
                "text": "你好",
                "model_profile_id": "relay:gpt-4o",
                "client_request_id": "req-allow-1",
            },
        )
        self.assertEqual(denied_other.status_code, 403)
        self.assertEqual(denied_other.json()["detail"], "model_not_permitted")

        # Disable all models for user -> default submit is also rejected!
        await self.client.patch(
            f"/admin/capabilities/{self.user.account.id}",
            headers=admin,
            json={"model_policy": "disabled"},
        )
        empty_catalog = (await self.client.get("/workspace/models", headers=user)).json()
        self.assertEqual(empty_catalog["groups"], [])
        denied_default = await self.client.post(
            f"/workspace/conversations/{conv_id}/runs",
            headers=user,
            json={
                "conversation_id": conv_id,
                "text": "你好",
                "client_request_id": "req-disabled-1",
            },
        )
        self.assertEqual(denied_default.status_code, 403)
        self.assertEqual(denied_default.json()["detail"], "model_not_permitted")

    async def test_skill_upload_and_skill_policy_are_enforced_across_http_and_tool(self):
        admin = {"Authorization": f"Bearer {self.admin_token}"}
        user = {"Authorization": f"Bearer {self.user_token}"}

        # User uploads a custom skill initially when allowed.
        uploaded = await self.client.post(
            "/maritime/skills/import",
            headers=user,
            files={"file": ("custom.zip", _make_skill_zip(), "application/zip")},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        skill_id = uploaded.json()["id"]

        # Disable upload permission.
        await self.client.patch(
            f"/admin/capabilities/{self.user.account.id}",
            headers=admin,
            json={"can_upload_skill": False},
        )
        denied_upload = await self.client.post(
            "/maritime/skills/import",
            headers=user,
            files={"file": ("custom2.zip", _make_skill_zip("blocked-skill"), "application/zip")},
        )
        self.assertEqual(denied_upload.status_code, 403, denied_upload.text)
        self.assertEqual(denied_upload.json()["detail"], "skill_upload_forbidden")

        # Set skill_policy to builtin_only -> hides & blocks custom skill in list, detail, run facts, and SkillReadTool!
        await self.client.patch(
            f"/admin/capabilities/{self.user.account.id}",
            headers=admin,
            json={"skill_policy": "builtin_only"},
        )
        listed = (await self.client.get("/maritime/skills", headers=user)).json()["skills"]
        self.assertNotIn(skill_id, {item["id"] for item in listed})
        self.assertIn("maritime-legal-research", {item["id"] for item in listed})

        denied_get = await self.client.get(f"/maritime/skills/{skill_id}", headers=user)
        self.assertEqual(denied_get.status_code, 403)
        self.assertEqual(denied_get.json()["detail"], "skill_not_permitted")

        conv = (await self.client.post("/workspace/conversations", headers=user, json={"title": "技能权限", "mode": "work"})).json()["conversation"]
        conv_id = conv["conversation_id"]
        denied_run = await self.client.post(
            f"/workspace/conversations/{conv_id}/runs",
            headers=user,
            json={
                "conversation_id": conv_id,
                "text": "运行技能",
                "facts": {"task_mode": "work", "skill_id": skill_id},
                "client_request_id": "req-skill-1",
            },
        )
        self.assertEqual(denied_run.status_code, 403, denied_run.text)

        # Tool layer check: SkillReadTool blocks custom skill under builtin_only, and blocks builtin under disabled.
        checker = self.app.state.check_skill_permission
        tool = SkillReadTool(
            default_skill_registry(),
            load_settings(),
            account_skill_loader=self.app.state.maritime_skill_loader,
            skill_permission_checker=checker,
        )
        tool_denied = await tool.execute_context(
            ModuleRequest(
                run_id="r1",
                call_id="c1",
                input=ActionRequest(tool_name="read_skill", arguments={"skill_id": skill_id}),
                context={"account_id": self.user.account.id},
            )
        )
        self.assertEqual(tool_denied.outcome, "error")
        self.assertEqual(tool_denied.error.code, "skill_not_permitted")

        await self.client.patch(
            f"/admin/capabilities/{self.user.account.id}",
            headers=admin,
            json={"skill_policy": "disabled"},
        )
        builtin_denied = await tool.execute_context(
            ModuleRequest(
                run_id="r2",
                call_id="c2",
                input=ActionRequest(tool_name="read_skill", arguments={"skill_id": "maritime-legal-research"}),
                context={"account_id": self.user.account.id},
            )
        )
        self.assertEqual(builtin_denied.outcome, "error")
        self.assertEqual(builtin_denied.error.code, "skill_not_permitted")


if __name__ == "__main__":
    unittest.main()
