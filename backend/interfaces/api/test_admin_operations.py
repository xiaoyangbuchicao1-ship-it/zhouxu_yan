"""HTTP red tests for the combined business/operations administrator view.

The tests use the real FastAPI boundary and a temporary SQLite database.  They
do not call an upstream model or a live search service: this node proves the
projection and permission contract before the browser page is added.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from backend.application.accounts.service import AccountService
from backend.application.workspace.conversations import WorkspaceService
from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database.connection import Database
from backend.interfaces.api.app import create_app


class AdminOperationsHttpContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = DatabaseSettings(url=f"sqlite:///{Path(self.tmp.name) / 'operations.sqlite3'}")
        self.database = Database(settings)
        self.database.initialize()
        self.accounts = AccountService(
            self.database, settings, AuthSettings(password_min_length=8)
        )
        self.admin = self.accounts.ensure_admin(
            email="admin@example.com", password="a-long-admin-password"
        )
        self.user = self.accounts.register(
            email="user@example.com", password="a-long-user-password"
        )
        self.admin_login = self.accounts.login(
            email="admin@example.com", password="a-long-admin-password"
        )
        self.workspace = WorkspaceService(self.database)
        conversation = self.workspace.create(
            account_id=self.user.account.id, title="资料任务", mode="work"
        )
        self.workspace.record_run(
            account_id=self.user.account.id,
            conversation_id=conversation.id,
            run_id="operations-run-1",
            question="请查找海商法条文",
            status="succeeded",
            answer="已完成公开资料整理。",
        )
        self.app = create_app(
            account_service=self.accounts,
            workspace_service=self.workspace,
            settings=self._settings(settings),
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

    @staticmethod
    def _settings(database: DatabaseSettings):
        # create_app accepts the full Settings object in production.  The
        # default loader is used here by the app, while the database service
        # is injected above; keeping this helper explicit documents that no
        # test secret or external endpoint is needed.
        from backend.config.settings import load_settings

        value = load_settings()
        return value.model_copy(update={"database": database})

    async def asyncTearDown(self):
        await self.client.aclose()
        self.database.close()
        self.tmp.cleanup()

    async def test_admin_can_read_truthful_operations_projections(self):
        headers = {"Authorization": f"Bearer {self.admin_login.token}"}
        for path in (
            "/admin/capabilities",
            "/admin/skills",
            "/admin/resources/health",
            "/admin/tasks?limit=20",
            "/admin/runtime/health",
        ):
            response = await self.client.get(path, headers=headers)
            self.assertEqual(response.status_code, 200, (path, response.text))

        capabilities = (await self.client.get("/admin/capabilities", headers=headers)).json()
        user = next(item for item in capabilities["accounts"] if item["account_id"] == self.user.account.id)
        self.assertEqual(user["model_policy"], "default")
        self.assertTrue(user["can_upload_skill"])
        self.assertNotIn("password_hash", user)

        skills = (await self.client.get("/admin/skills", headers=headers)).json()
        self.assertGreaterEqual(skills["total"], 1)
        self.assertTrue(all("instructions" not in item for item in skills["skills"]))
        self.assertTrue(all("path" not in item for item in skills["skills"]))

        tasks = (await self.client.get("/admin/tasks?limit=20", headers=headers)).json()
        task = next(item for item in tasks["tasks"] if item["run_id"] == "operations-run-1")
        self.assertEqual(task["status"], "succeeded")
        self.assertEqual(task["account_email"], "user@example.com")
        self.assertEqual(task["answer_chars"], len("已完成公开资料整理。"))
        self.assertNotIn("payload_json", task)

        resource_health = (await self.client.get("/admin/resources/health", headers=headers)).json()
        self.assertIn(resource_health["status"], {"ready", "degraded", "unavailable"})
        self.assertIn("sources", resource_health)

        runtime = (await self.client.get("/admin/runtime/health", headers=headers)).json()
        self.assertIn(runtime["status"], {"ready", "degraded", "unavailable"})
        self.assertIn("components", runtime)
        self.assertNotIn("api_key", runtime)

    async def test_admin_can_update_account_capability_policy_and_user_cannot(self):
        admin_headers = {"Authorization": f"Bearer {self.admin_login.token}"}
        update = await self.client.patch(
            f"/admin/capabilities/{self.user.account.id}",
            headers=admin_headers,
            json={
                "model_policy": "allowlist",
                "allowed_models": ["deepseek-flash"],
                "skill_policy": "builtin_only",
                "can_upload_skill": False,
            },
        )
        self.assertEqual(update.status_code, 200, update.text)
        body = update.json()
        self.assertEqual(body["model_policy"], "allowlist")
        self.assertEqual(body["allowed_models"], ["deepseek-flash"])
        self.assertFalse(body["can_upload_skill"])

        user_login = self.accounts.login(
            email="user@example.com", password="a-long-user-password"
        )
        forbidden = await self.client.patch(
            f"/admin/capabilities/{self.user.account.id}",
            headers={"Authorization": f"Bearer {user_login.token}"},
            json={"model_policy": "default"},
        )
        self.assertEqual(forbidden.status_code, 403)

    async def test_resource_health_prefers_live_opensearch_and_does_not_fake_unavailable(self):
        from unittest.mock import patch

        headers = {"Authorization": f"Bearer {self.admin_login.token}"}
        with (
            patch(
                "backend.infrastructure.search.maritime_corpus.MaritimeCorpus.status",
                return_value={"indexed": False, "counts": {}},
            ),
            patch(
                "backend.infrastructure.search.maritime_corpus.MaritimeCorpus.opensearch_status",
                return_value={"available": True, "counts": {"laws": 93858, "cases": 3156}, "errors": {}},
            ),
        ):
            res = await self.client.get("/admin/resources/health", headers=headers)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["sources"]["laws"]["status"], "ready")
        self.assertEqual(body["sources"]["laws"]["count"], 93858)
        self.assertEqual(body["sources"]["cases"]["status"], "ready")
        self.assertEqual(body["sources"]["cases"]["count"], 3156)

        with (
            patch(
                "backend.infrastructure.search.maritime_corpus.MaritimeCorpus.status",
                return_value={"indexed": False, "counts": {}},
            ),
            patch(
                "backend.infrastructure.search.maritime_corpus.MaritimeCorpus.opensearch_status",
                return_value={"available": False, "counts": {}, "errors": {"cluster": "ConnectError"}},
            ),
        ):
            down = await self.client.get("/admin/resources/health", headers=headers)
        self.assertEqual(down.status_code, 200)
        down_body = down.json()
        self.assertEqual(down_body["sources"]["laws"]["status"], "unavailable")
        self.assertEqual(down_body["sources"]["cases"]["status"], "unavailable")

    async def test_admin_tasks_support_status_filter_and_reject_invalid_status(self):
        headers = {"Authorization": f"Bearer {self.admin_login.token}"}
        conv = self.workspace.create(
            account_id=self.user.account.id, title="失败任务排查", mode="work"
        )
        self.workspace.record_run(
            account_id=self.user.account.id,
            conversation_id=conv.id,
            run_id="operations-run-failed",
            question="触发失败测试",
            status="failed",
        )
        self.workspace.record_run(
            account_id=self.user.account.id,
            conversation_id=conv.id,
            run_id="operations-run-paused",
            question="暂停任务测试",
            status="paused",
        )

        failed_res = await self.client.get("/admin/tasks?status=failed", headers=headers)
        self.assertEqual(failed_res.status_code, 200, failed_res.text)
        failed_ids = {item["run_id"] for item in failed_res.json()["tasks"]}
        self.assertEqual(failed_ids, {"operations-run-failed"})

        active_res = await self.client.get("/admin/tasks?status=active", headers=headers)
        self.assertEqual(active_res.status_code, 200, active_res.text)
        active_ids = {item["run_id"] for item in active_res.json()["tasks"]}
        self.assertEqual(active_ids, {"operations-run-paused"})

        invalid_res = await self.client.get("/admin/tasks?status=bogus", headers=headers)
        self.assertEqual(invalid_res.status_code, 422)
        self.assertEqual(invalid_res.json()["detail"], "invalid_task_status")


if __name__ == "__main__":
    unittest.main()
