from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import httpx

from backend.application.accounts.service import AccountService
from backend.config.settings import AuthSettings, DatabaseSettings, load_settings
from backend.contracts.harness.execution import ExecutionState
from backend.contracts.modules.loop import ActionResult, DecisionProposal, RunInput
from backend.harness.lifecycle.recovery import recovery_for_state
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.migrate_model_groups import MIGRATION_ID, migrate
from backend.infrastructure.model.deepseek import is_quota_or_balance_error
from backend.infrastructure.sources import (
    annotate_used_sources,
    merge_source_refs,
    source_refs_from_action,
)
from backend.interfaces.api.app import create_app


class SourcesProjectionGoldenTests(unittest.TestCase):
    def test_web_search_web_read_merge_and_used_in_answer_distinction(self) -> None:
        search_result = ActionResult(
            tool_name="web_search",
            action_id="a1",
            outcome="ok",
            result={
                "status": "ok",
                "results": [
                    {
                        "title": "最高人民法院关于审理海上货运代理纠纷案件若干问题的规定",
                        "url": "https://www.court.gov.cn/zixun/12345.html",
                        "snippet": "司法解释原文与适用说明。",
                        "date": "2025-04-10",
                        "site_name": "最高人民法院",
                    },
                    {
                        "title": "海事案例旁证资料",
                        "url": "https://example.org/unused-ref",
                        "snippet": "仅作背景检索参考。",
                        "site_name": "海商法观察",
                    },
                    {
                        "title": "危险链接应被过滤",
                        "url": "javascript:alert(1)",
                        "snippet": "invalid",
                    },
                ],
            },
        )
        web_refs = source_refs_from_action(search_result)
        self.assertEqual(len(web_refs), 2)
        self.assertEqual(web_refs[0]["kind"], "web")
        self.assertEqual(web_refs[0]["url"], "https://www.court.gov.cn/zixun/12345.html")
        self.assertEqual(web_refs[0]["domain"], "最高人民法院")
        self.assertFalse(web_refs[0]["verified_read"])

        read_result = ActionResult(
            tool_name="web_read",
            action_id="a2",
            outcome="ok",
            result={
                "status": "ok",
                "url": "https://www.court.gov.cn/zixun/12345.html",
                "title": "最高人民法院关于审理海上货运代理纠纷案件若干问题的规定",
                "content": "为正确审理海上货运代理纠纷案件，结合审判实践制定本规定第一条至第十三条。",
            },
        )
        law_result = ActionResult(
            tool_name="dynamic_rag_search",
            action_id="a3",
            outcome="ok",
            result={
                "items": [
                    {
                        "document_name": "中华人民共和国海商法（2025年修订）",
                        "article_no": "第八十七条",
                        "version_date": "2025-11-01",
                        "is_current": True,
                    },
                    {
                        "document_name": "中华人民共和国海商法（2025年修订）",
                        "article_no": "第五十六条",
                        "version_date": "2025-11-01",
                        "is_current": True,
                    },
                ]
            },
        )
        case_result = ActionResult(
            tool_name="search_cases",
            action_id="a4",
            outcome="ok",
            result={
                "items": [
                    {"case_id": "c1", "title": "（2024）沪72民初101号 某兰德公司诉地中海公司", "snapshot_id": "s1"},
                    {"case_id": "c2", "title": "（2023）津72民初88号 未引用对比案", "snapshot_id": "s1"},
                ]
            },
        )
        paper_result = ActionResult(
            tool_name="information_card_search",
            action_id="a5",
            outcome="ok",
            result={
                "items": [
                    {"card_id": "card_1", "title": "电子提单转让规则比较研究", "source_authors": "张明"},
                    {"card_id": "card_2", "title": "船舶油污损害赔偿基金制度反思", "source_authors": "李华"},
                ]
            },
        )

        merged = merge_source_refs([], web_refs)
        merged = merge_source_refs(merged, source_refs_from_action(read_result))
        merged = merge_source_refs(merged, source_refs_from_action(law_result))
        merged = merge_source_refs(merged, source_refs_from_action(case_result))
        merged = merge_source_refs(merged, source_refs_from_action(paper_result))

        court_web = next(item for item in merged if item.get("url") == "https://www.court.gov.cn/zixun/12345.html")
        self.assertTrue(court_web["verified_read"])
        self.assertIn("第一条至第十三条", court_web["snippet"])

        answer = (
            "依据《海商法》第87条以及最高人民法院发布的"
            "https://www.court.gov.cn/zixun/12345.html ，"
            "同时参考（2024）沪72民初101号裁判与《电子提单转让规则比较研究》一文。"
        )
        annotated = annotate_used_sources(merged, answer)
        used_titles = {item["title"] for item in annotated if item.get("used_in_answer") is True}
        unused_titles = {item["title"] for item in annotated if item.get("used_in_answer") is False}

        self.assertEqual(
            used_titles,
            {
                "最高人民法院关于审理海上货运代理纠纷案件若干问题的规定",
                "中华人民共和国海商法（2025年修订）",
                "（2024）沪72民初101号 某兰德公司诉地中海公司",
                "电子提单转让规则比较研究",
            },
        )
        self.assertIn("海事案例旁证资料", unused_titles)
        self.assertIn("（2023）津72民初88号 未引用对比案", unused_titles)
        self.assertIn("船舶油污损害赔偿基金制度反思", unused_titles)


class ModelGroupsMigrationGoldenTests(unittest.TestCase):
    def test_migration_adds_group_table_and_column_with_backup_and_idempotency(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "legacy.db"
            backup_path = Path(tmp) / "backup.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE account_capability_grants (
                        account_id VARCHAR(32) PRIMARY KEY,
                        model_policy VARCHAR(24) NOT NULL DEFAULT 'default',
                        allowed_models_json TEXT,
                        skill_policy VARCHAR(24) NOT NULL DEFAULT 'default',
                        can_upload_skill INTEGER NOT NULL DEFAULT 1,
                        material_quota_mb INTEGER,
                        updated_at DATETIME NOT NULL
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO account_capability_grants (account_id, model_policy, skill_policy, can_upload_skill, updated_at) "
                    "VALUES ('u1', 'default', 'default', 1, '2026-11-26T00:00:00Z')"
                )
                conn.execute("CREATE TABLE schema_versions (version TEXT PRIMARY KEY, applied_at DATETIME NOT NULL)")
                conn.commit()

            dry = migrate(db_path, apply=False)
            self.assertFalse(dry["already_migrated"])
            self.assertFalse(dry["applied"])

            applied = migrate(db_path, apply=True, backup=backup_path)
            self.assertTrue(applied["applied"])
            self.assertTrue(backup_path.is_file())

            with closing(sqlite3.connect(db_path)) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertIn("model_access_groups", tables)
                cols = {row[1] for row in conn.execute("PRAGMA table_info(account_capability_grants)")}
                self.assertIn("group_id", cols)
                row = conn.execute("SELECT account_id, group_id FROM account_capability_grants WHERE account_id='u1'").fetchone()
                self.assertEqual(row, ("u1", None))
                ver = conn.execute("SELECT 1 FROM schema_versions WHERE version=?", (MIGRATION_ID,)).fetchone()
                self.assertIsNotNone(ver)

            again = migrate(db_path, apply=False)
            self.assertTrue(again["already_migrated"])


class DomesticDefaultAndGroupPermissionsGoldenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
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
        db_settings = DatabaseSettings(url=f"sqlite:///{Path(self.tmp.name) / 'groups.sqlite3'}")
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

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.database.close()
        self.env_patch.stop()
        self.tmp.cleanup()

    async def test_domestic_default_and_group_permission_lifecycle(self) -> None:
        admin = {"Authorization": f"Bearer {self.admin_token}"}
        user = {"Authorization": f"Bearer {self.user_token}"}

        # 1. Creating an overseas model without explicit access_scope defaults to allowlist; domestic defaults to all.
        created_overseas = await self.client.post(
            "/admin/models",
            headers=admin,
            json={
                "provider_id": "relay",
                "upstream_model_id": "claude-sonnet-test",
                "label": "Claude Test",
            },
        )
        self.assertEqual(created_overseas.status_code, 201, created_overseas.text)
        self.assertEqual(created_overseas.json()["access_scope"], "allowlist")
        self.assertFalse(created_overseas.json()["is_domestic"])

        created_domestic = await self.client.post(
            "/admin/models",
            headers=admin,
            json={
                "provider_id": "relay",
                "upstream_model_id": "Qwen/Qwen2.5-72B-Instruct",
                "label": "Qwen Test",
            },
        )
        self.assertEqual(created_domestic.status_code, 201, created_domestic.text)
        self.assertEqual(created_domestic.json()["access_scope"], "all")
        self.assertTrue(created_domestic.json()["is_domestic"])

        # 2. Preview & apply domestic default policy: deepseek/glm open (all), relay:gpt-4o restricted (allowlist).
        preview = await self.client.post(
            "/admin/models/apply-domestic-default",
            headers=admin,
            json={"dry_run": True},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        preview_data = preview.json()
        self.assertTrue(preview_data["dry_run"])
        self.assertTrue(any(item["profile_id"] == "relay:gpt-4o" and item["target_scope"] == "allowlist" for item in preview_data["changes"]))

        applied = await self.client.post(
            "/admin/models/apply-domestic-default",
            headers=admin,
            json={"dry_run": False},
        )
        self.assertEqual(applied.status_code, 200, applied.text)
        applied_data = applied.json()
        self.assertFalse(applied_data["dry_run"])

        # Ungrouped user now sees domestic models, NOT relay:gpt-4o.
        user_catalog = (await self.client.get("/workspace/models", headers=user)).json()
        user_ids = {m["profile_id"] for g in user_catalog["groups"] for m in g["models"]}
        self.assertIn("deepseek-flash", user_ids)
        self.assertNotIn("relay:gpt-4o", user_ids)

        # 3. Create group granting relay:gpt-4o, assign user -> unlocked immediately.
        grp_res = await self.client.post(
            "/admin/model-groups",
            headers=admin,
            json={
                "name": "海外研究组",
                "description": "开放 GPT-4o",
                "allowed_models": ["relay:gpt-4o"],
            },
        )
        self.assertEqual(grp_res.status_code, 201, grp_res.text)
        group_id = grp_res.json()["id"]

        assign_res = await self.client.patch(
            f"/admin/capabilities/{self.user.account.id}",
            headers=admin,
            json={"group_id": group_id},
        )
        self.assertEqual(assign_res.status_code, 200, assign_res.text)
        self.assertEqual(assign_res.json()["group_id"], group_id)
        self.assertEqual(assign_res.json()["group_allowed_models"], ["relay:gpt-4o"])

        grouped_ids = {m["profile_id"] for g in (await self.client.get("/workspace/models", headers=user)).json()["groups"] for m in g["models"]}
        self.assertIn("deepseek-flash", grouped_ids)
        self.assertIn("relay:gpt-4o", grouped_ids)

        # 4. Delete group -> member cleanly cleared and relay:gpt-4o immediately blocked (403 on run).
        del_res = await self.client.delete(f"/admin/model-groups/{group_id}", headers=admin)
        self.assertEqual(del_res.status_code, 200, del_res.text)

        after_ids = {m["profile_id"] for g in (await self.client.get("/workspace/models", headers=user)).json()["groups"] for m in g["models"]}
        self.assertNotIn("relay:gpt-4o", after_ids)

        conv = (await self.client.post("/workspace/conversations", headers=user, json={"title": "分组撤权检查", "mode": "chat"})).json()["conversation"]
        conv_id = conv["conversation_id"]
        denied = await self.client.post(
            f"/workspace/conversations/{conv_id}/runs",
            headers=user,
            json={
                "conversation_id": conv_id,
                "text": "测试",
                "model_profile_id": "relay:gpt-4o",
                "client_request_id": "req-group-revoked-1",
            },
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["detail"], "model_not_permitted")


class TimeoutRoundContinuityAndQuotaGoldenTests(unittest.IsolatedAsyncioTestCase):
    def test_timeout_budgets_and_quota_message_updated(self) -> None:
        settings = load_settings()
        self.assertEqual(settings.runtime.run_timeout_seconds, 600.0)
        self.assertEqual(settings.runtime.work_run_timeout_seconds, 3600.0)
        self.assertEqual(settings.runtime.model_call_timeout_seconds, 360.0)
        self.assertIn("余额不足", settings.messages["model_quota_exhausted"])

    def test_quota_detection_and_recovery_message(self) -> None:
        self.assertTrue(is_quota_or_balance_error(402, ""))
        self.assertTrue(
            is_quota_or_balance_error(
                429,
                '{"error":{"code":"insufficient_quota","message":"You exceeded your current quota"}}',
            )
        )
        self.assertTrue(
            is_quota_or_balance_error(
                403,
                '{"error":{"message":"用户余额不足，请充值"}}',
            )
        )
        self.assertFalse(
            is_quota_or_balance_error(
                429,
                '{"error":{"code":"rate_limit_exceeded","message":"Too many requests"}}',
            )
        )

        rec_ckpt = recovery_for_state(
            status="failed",
            error_code="model_quota_exhausted",
            execution_state={"tool_results": [{"tool_name": "web_search"}]},
        )
        self.assertTrue(rec_ckpt.can_continue)
        self.assertTrue(rec_ckpt.can_retry)
        self.assertIn("余额不足", rec_ckpt.message)
        self.assertIn("管理员", rec_ckpt.message)

        rec_fresh = recovery_for_state(status="failed", error_code="model_quota_exhausted")
        self.assertFalse(rec_fresh.can_continue)
        self.assertTrue(rec_fresh.can_retry)
        self.assertIn("余额不足", rec_fresh.message)

    async def test_resume_continues_cumulative_round_number_from_checkpoint(self) -> None:
        from backend.contracts.modules.loop import PerceivedContext, ReflectionResult
        from backend.contracts.modules.memory import MemoryOutput
        from backend.contracts.modules.perception import ContextAssembly
        from backend.contracts.schemas.core import ModuleResponse
        from backend.harness.runtime.loop import Harness

        class _StubModule:
            def __init__(self, handler):
                self._handler = handler

            async def handle(self, request):
                return ModuleResponse(status="ok", output=self._handler(request))

        class _CaptureStream:
            def __init__(self):
                self.events = []

            def emit_stream(self, event):
                self.events.append(event)

            def visible(self, _run_id):
                return "", 0

        class _NoopEvents:
            def emit(self, _event):
                return None

        settings = load_settings().model_copy(deep=True)
        streams = _CaptureStream()
        harness = Harness(
            perception=_StubModule(
                lambda req: PerceivedContext(run_id=req.run_id, items=[], assembly=ContextAssembly())
            ),
            memory=_StubModule(
                lambda req: MemoryOutput(scope=req.run_id or "run", records=[], items=[], required_item_ids=[])
            ),
            reasoning=_StubModule(lambda _req: DecisionProposal(kind="answer", answer="继续完成回答")),
            action=_StubModule(lambda _req: None),
            reflection=_StubModule(lambda _req: ReflectionResult(decision="complete", feedback="ok")),
            settings=settings,
            tools=[],
            events=_NoopEvents(),
            streams=streams,
        )
        saved = ExecutionState(
            rounds_completed=3,
            tool_results=[{"tool_name": "dynamic_rag_search", "action_id": "a1", "outcome": "ok"}],
        ).model_dump(mode="json")

        result = await harness.run(
            RunInput(text="继续分析海商法问题", mode="chat"),
            recovery=saved,
        )
        self.assertEqual(result.status, "succeeded")
        labels = [e.label for e in streams.events if getattr(e, "stage", None) == "synthesizing"]
        self.assertTrue(any("第 4 轮" in (label or "") for label in labels), f"got labels={labels}")
        self.assertFalse(any("第 1 轮" in (label or "") for label in labels), f"got labels={labels}")


if __name__ == "__main__":
    unittest.main()
