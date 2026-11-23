"""Red tests for the administrator's account-scoped conversation log.

These tests deliberately exercise the HTTP boundary.  A service-level test is
not enough here: the important contract is that an administrator can inspect a
different account without receiving private payloads, and that a normal user
cannot bypass the UI and call the same endpoint.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from backend.application.accounts.service import AccountService
from backend.application.workspace.conversations import WorkspaceService
from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database.connection import Database
from backend.interfaces.api.app import create_app


FORBIDDEN_KEYS = {
    "attachments",
    "attachment_content",
    "disk_path",
    "relative_path",
    "storage_key",
    "payload_json",
    "system_prompt",
    "hidden_reasoning",
    "raw_frames",
    "tool_receipts",
    "stack",
    "secret",
    "api_key",
    "skill_source",
}


def _assert_no_forbidden_keys(test: unittest.TestCase, value) -> None:
    if isinstance(value, dict):
        test.assertTrue(FORBIDDEN_KEYS.isdisjoint(value), value)
        for child in value.values():
            _assert_no_forbidden_keys(test, child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_keys(test, child)


class AdminConversationHttpContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = DatabaseSettings(url=f"sqlite:///{Path(self.tmp.name) / 'admin.sqlite3'}")
        self.database = Database(settings)
        self.database.initialize()
        auth_settings = AuthSettings(password_min_length=8)
        self.accounts = AccountService(self.database, settings, auth_settings)
        self.admin = self.accounts.ensure_admin(
            email="admin@example.com", password="a-long-admin-password"
        )
        self.user_a = self.accounts.register(
            email="alice@example.com", password="a-long-user-password"
        )
        self.user_b = self.accounts.register(
            email="bob@example.com", password="a-long-user-password"
        )
        self.admin_login = self.accounts.login(
            email="admin@example.com", password="a-long-admin-password"
        )
        self.workspace = WorkspaceService(self.database)
        self.conversation_a = self.workspace.create(
            account_id=self.user_a.account.id, title="海事法规研究", mode="work"
        )
        self.conversation_b = self.workspace.create(
            account_id=self.user_b.account.id, title="另一账户的会话", mode="chat"
        )
        started = datetime.now(timezone.utc) - timedelta(seconds=4)
        finished = started + timedelta(seconds=2)
        self.workspace.record_run(
            account_id=self.user_a.account.id,
            conversation_id=self.conversation_a.id,
            run_id="run-alice-1",
            question="请查找现行海商法的相关条文",
            status="succeeded",
            answer="已找到公开条文，并列出来源。",
            created_at=started,
            completed_at=finished,
            first_event_at=started,
            first_delta_at=started + timedelta(milliseconds=450),
            last_event_at=finished,
            payload={
                "mode": "work",
                "model_snapshot": {
                    "profile_id": "relay:gpt-6.2-sol",
                    "provider": "relay",
                    "model_id": "gpt-6.2-sol",
                    "protocol": "openai_responses",
                    "thinking_mode": "low",
                    "context_tokens": 262144,
                    "capabilities": {},
                    "checked_on": "2026-11-22",
                },
                "facts": {"private_note": "must never leave the payload"},
                "attachments": [{"disk_path": "C:/private/alice.pdf"}],
                "system_prompt": "hidden prompt",
            },
            progress={
                "source_refs": [{"source_id": "law-22", "kind": "law", "title": "海商法"}],
                "artifacts": [{
                    "artifact_id": "report-pdf",
                    "name": "海事报告.pdf",
                    "relative_path": "private/report.pdf",
                    "media_type": "application/pdf",
                    "size_bytes": 24,
                    "sha256": "a" * 64,
                    "version": 1,
                    "kind": "document",
                }],
                "nodes": [{"id": "retrieve", "label": "查找资料", "status": "succeeded"}],
                "tool_receipts": [{"secret": "do-not-return"}],
            },
            error_code=None,
        )
        self.workspace.record_run(
            account_id=self.user_a.account.id,
            conversation_id=self.conversation_a.id,
            run_id="run-alice-2",
            question="继续整理重点",
            status="paused",
            partial_output="已经整理了第一部分。",
            created_at=finished + timedelta(seconds=1),
        )
        self.workspace.record_run(
            account_id=self.user_b.account.id,
            conversation_id=self.conversation_b.id,
            run_id="run-bob-1",
            question="这是另一账户的问题",
            status="succeeded",
            answer="另一账户的回答",
        )
        self.app = create_app(
            account_service=self.accounts,
            workspace_service=self.workspace,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.database.close()
        self.tmp.cleanup()

    async def test_admin_lists_and_opens_account_conversation_with_safe_projection(self):
        admin_headers = {"Authorization": f"Bearer {self.admin_login.token}"}
        listing = await self.client.get(
            f"/admin/accounts/{self.user_a.account.id}/conversations?mode=work&limit=20",
            headers=admin_headers,
        )
        self.assertEqual(listing.status_code, 200, listing.text)
        body = listing.json()
        self.assertEqual(len(body["conversations"]), 1)
        self.assertEqual(body["conversations"][0]["title"], "海事法规研究")
        self.assertEqual(body["conversations"][0]["run_count"], 2)
        self.assertNotIn("answer", body["conversations"][0])

        detail = await self.client.get(
            f"/admin/accounts/{self.user_a.account.id}/conversations/{self.conversation_a.id}?limit=50",
            headers=admin_headers,
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        detail_body = detail.json()
        self.assertEqual(detail_body["conversation"]["account_id"], self.user_a.account.id)
        self.assertEqual(len(detail_body["runs"]), 2)
        first = detail_body["runs"][0]
        self.assertEqual(first["user_question"], "请查找现行海商法的相关条文")
        self.assertEqual(first["public_answer"], "已找到公开条文，并列出来源。")
        self.assertEqual(first["model"], "gpt-6.2-sol")
        self.assertEqual(first["protocol"], "openai_responses")
        self.assertEqual(first["thinking_mode"], "low")
        self.assertGreaterEqual(first["elapsed_ms"], 0)
        self.assertEqual(first["nodes"][0]["label"], "查找资料")
        # The administrator needs the same public evidence trail as the user:
        # source cards and generated-file metadata.  Private tool receipts and
        # storage paths must remain absent.
        self.assertEqual(first["sources"][0]["source_id"], "law-22")
        self.assertEqual(first["sources"][0]["title"], "海商法")
        self.assertEqual(first["artifacts"][0]["artifact_id"], "report-pdf")
        self.assertEqual(first["artifacts"][0]["name"], "海事报告.pdf")
        _assert_no_forbidden_keys(self, detail_body)
        serialized = json.dumps(detail_body, ensure_ascii=False)
        self.assertNotIn("C:/private/alice.pdf", serialized)
        self.assertNotIn("hidden prompt", serialized)
        self.assertNotIn("do-not-return", serialized)

        audit = await self.client.get("/admin/audit?limit=20", headers=admin_headers)
        self.assertEqual(audit.status_code, 200, audit.text)
        viewed = [item for item in audit.json()["events"] if item["action"] == "conversation.log_viewed"]
        self.assertEqual(len(viewed), 1)
        self.assertEqual(viewed[0]["target_account_id"], self.user_a.account.id)
        self.assertEqual(viewed[0]["details"]["limit"], 50)
        self.assertNotIn("user_question", json.dumps(viewed[0], ensure_ascii=False))

    async def test_normal_user_is_forbidden_and_cross_account_conversation_is_not_found(self):
        user_headers = {"Authorization": f"Bearer {self.user_a.token}"}
        forbidden = await self.client.get(
            f"/admin/accounts/{self.user_a.account.id}/conversations",
            headers=user_headers,
        )
        self.assertEqual(forbidden.status_code, 403)

        admin_headers = {"Authorization": f"Bearer {self.admin_login.token}"}
        wrong_owner = await self.client.get(
            f"/admin/accounts/{self.user_a.account.id}/conversations/{self.conversation_b.id}",
            headers=admin_headers,
        )
        self.assertEqual(wrong_owner.status_code, 404)

    async def test_conversation_cursor_is_stable_and_does_not_duplicate_runs(self):
        for index in range(3):
            self.workspace.record_run(
                account_id=self.user_a.account.id,
                conversation_id=self.conversation_a.id,
                run_id=f"run-alice-page-{index}",
                question=f"分页问题 {index}",
                status="succeeded",
                answer=f"分页回答 {index}",
                created_at=datetime.now(timezone.utc) + timedelta(seconds=index + 5),
            )
        admin_headers = {"Authorization": f"Bearer {self.admin_login.token}"}
        first = await self.client.get(
            f"/admin/accounts/{self.user_a.account.id}/conversations/{self.conversation_a.id}?limit=2",
            headers=admin_headers,
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(len(first.json()["runs"]), 2)
        cursor = first.json()["next_cursor"]
        self.assertTrue(cursor)
        second = await self.client.get(
            f"/admin/accounts/{self.user_a.account.id}/conversations/{self.conversation_a.id}?limit=2&cursor={cursor}",
            headers=admin_headers,
        )
        self.assertEqual(second.status_code, 200, second.text)
        ids = [item["run_id"] for item in first.json()["runs"] + second.json()["runs"]]
        self.assertEqual(len(ids), len(set(ids)))

    async def test_admin_receives_complete_public_answer_and_partial_output(self):
        """The admin log must show the actual public text, not a short summary."""
        long_answer = "\n\n".join(
            f"第 {index} 段：这是模型已经交付给用户的公开回答正文。"
            for index in range(180)
        )
        partial = "\n\n".join(
            f"暂停前已生成的第 {index} 段公开正文。"
            for index in range(80)
        )
        self.workspace.record_run(
            account_id=self.user_a.account.id,
            conversation_id=self.conversation_a.id,
            run_id="run-alice-long-answer",
            question="请完整整理这份资料",
            status="succeeded",
            answer=long_answer,
        )
        self.workspace.record_run(
            account_id=self.user_a.account.id,
            conversation_id=self.conversation_a.id,
            run_id="run-alice-long-paused",
            question="继续整理并保留进度",
            status="paused",
            partial_output=partial,
        )
        admin_headers = {"Authorization": f"Bearer {self.admin_login.token}"}
        detail = await self.client.get(
            f"/admin/accounts/{self.user_a.account.id}/conversations/{self.conversation_a.id}?limit=50",
            headers=admin_headers,
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        runs = {item["run_id"]: item for item in detail.json()["runs"]}
        self.assertEqual(runs["run-alice-long-answer"]["public_answer"], long_answer)
        self.assertEqual(runs["run-alice-long-paused"]["public_answer"], partial)
        _assert_no_forbidden_keys(self, detail.json())

        # Read the same persisted rows through the ordinary user's HTTP
        # conversation endpoint.  This closes the real data path before the
        # administrator projection is checked.
        user_headers = {"Authorization": f"Bearer {self.user_a.token}"}
        user_detail = await self.client.get(
            f"/workspace/conversations/{self.conversation_a.id}", headers=user_headers
        )
        self.assertEqual(user_detail.status_code, 200, user_detail.text)
        assistant_messages = {
            message["run_id"]: message
            for message in user_detail.json()["messages"]
            if message["role"] == "assistant"
        }
        self.assertEqual(assistant_messages["run-alice-long-answer"]["content"], long_answer)
        self.assertEqual(assistant_messages["run-alice-long-paused"]["content"], partial)

    async def test_admin_conversation_detail_includes_retrieval_traces_materials_and_error(self):
        from backend.infrastructure.database.models import MaterialModel

        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            session.add(
                MaterialModel(
                    id="mat-alice-1",
                    account_id=self.user_a.account.id,
                    session_id=self.conversation_a.id,
                    filename="租船合同纠纷材料.pdf",
                    media_type="application/pdf",
                    size_bytes=4096,
                    checksum="b" * 64,
                    storage_key="C:/private/should-never-leak.pdf",
                    status="ready",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()

        self.workspace.record_run(
            account_id=self.user_a.account.id,
            conversation_id=self.conversation_a.id,
            run_id="run-alice-rich-trace",
            question="核查海商法船舶优先权条文",
            status="failed",
            error="上游服务暂时繁忙，请稍后重试。",
            error_code="upstream_busy",
            material_ids=["mat-alice-1"],
            created_at=now,
            first_event_at=now + timedelta(milliseconds=120),
            first_delta_at=now + timedelta(milliseconds=640),
            completed_at=now + timedelta(seconds=2),
            progress={
                "current_unit": "retrieval",
                "context_compaction_count": 1,
                "continuation_guidance": ["优先补充船舶登记时间"],
                "tool_traces": [
                    {
                        "tool_name": "dynamic_rag_search",
                        "tool_label": "海商法规检索",
                        "query": "海商法 船舶优先权",
                        "result_count": 5,
                        "status": "succeeded",
                        "summary": "命中 5 条",
                    }
                ],
                "source_refs": [
                    {
                        "source_id": "law-1",
                        "kind": "law",
                        "title": "中华人民共和国海商法",
                        "locator": "第二十二条",
                        "section": "第二章 船舶 / 第三节 船舶优先权",
                        "version_date": "2025-10-28",
                        "version_label": "现行版本",
                    },
                    {
                        "source_id": "case-1",
                        "kind": "case",
                        "title": "某轮扣押拍卖优先权案",
                        "locator": "案例原文",
                    },
                ],
            },
        )
        admin_headers = {"Authorization": f"Bearer {self.admin_login.token}"}
        detail = await self.client.get(
            f"/admin/accounts/{self.user_a.account.id}/conversations/{self.conversation_a.id}?limit=50",
            headers=admin_headers,
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        body = detail.json()
        self.assertEqual(body["account"]["email"], "alice@example.com")
        runs = {item["run_id"]: item for item in body["runs"]}
        rich = runs["run-alice-rich-trace"]
        self.assertEqual(rich["error_message"], "上游服务暂时繁忙，请稍后重试。")
        self.assertEqual(rich["first_delta_ms"], 640)
        self.assertEqual(rich["current_unit"], "retrieval")
        self.assertEqual(rich["context_compaction_count"], 1)
        self.assertEqual(rich["continuation_guidance"], ["优先补充船舶登记时间"])
        self.assertEqual(rich["source_summary"], {"total": 2, "law": 1, "case": 1})
        self.assertEqual(rich["materials"][0]["filename"], "租船合同纠纷材料.pdf")
        self.assertEqual(rich["tool_traces"][0]["tool_label"], "海商法规检索")
        self.assertEqual(rich["tool_traces"][0]["query"], "海商法 船舶优先权")
        self.assertEqual(rich["tool_traces"][0]["result_count"], 5)
        _assert_no_forbidden_keys(self, body)
        self.assertNotIn("should-never-leak.pdf", detail.text)

        missing_source = await self.client.get("/admin/sources/law-missing", headers=admin_headers)
        self.assertEqual(missing_source.status_code, 404)

    async def test_admin_can_filter_conversations_by_status_bucket_and_read_counts(self):
        headers = {"Authorization": f"Bearer {self.admin_login.token}"}
        t0 = datetime.now(timezone.utc) - timedelta(hours=3)
        c_ok = self.workspace.create(account_id=self.user_a.account.id, title="已完成会话", mode="chat")
        c_wait = self.workspace.create(account_id=self.user_a.account.id, title="待确认会话", mode="chat")
        c_pause = self.workspace.create(account_id=self.user_a.account.id, title="已暂停会话", mode="work")
        c_fail = self.workspace.create(account_id=self.user_a.account.id, title="失败会话", mode="work")
        c_empty = self.workspace.create(account_id=self.user_a.account.id, title="空白会话", mode="chat")

        self.workspace.record_run(
            account_id=self.user_a.account.id,
            conversation_id=c_ok.id,
            run_id="run-bucket-ok",
            question="已完成问题",
            status="succeeded",
            answer="已完成公开回答",
            created_at=t0 + timedelta(minutes=5),
        )
        self.workspace.record_run(
            account_id=self.user_a.account.id,
            conversation_id=c_wait.id,
            run_id="run-bucket-wait",
            question="等待补充确认",
            status="waiting_user",
            created_at=t0 + timedelta(minutes=10),
        )
        self.workspace.record_run(
            account_id=self.user_a.account.id,
            conversation_id=c_pause.id,
            run_id="run-bucket-pause",
            question="已暂停长任务",
            status="paused",
            created_at=t0 + timedelta(minutes=20),
        )
        self.workspace.record_run(
            account_id=self.user_a.account.id,
            conversation_id=c_fail.id,
            run_id="run-bucket-fail",
            question="超时失败任务",
            status="failed",
            created_at=t0 + timedelta(minutes=30),
        )

        overview_res = await self.client.get(
            f"/admin/accounts/{self.user_a.account.id}/conversations?limit=20",
            headers=headers,
        )
        self.assertEqual(overview_res.status_code, 200, overview_res.text)
        overview_body = overview_res.json()
        self.assertEqual(
            overview_body["status_counts"],
            {"all": 6, "succeeded": 1, "active": 3, "failed": 1, "no_runs": 1},
        )
        items_by_id = {row["id"]: row for row in overview_body["conversations"]}
        self.assertIsNotNone(items_by_id[c_fail.id]["latest_run_at"])
        self.assertIsNone(items_by_id[c_empty.id]["latest_run_at"])

        for status_filter, expected_ids in (
            ("succeeded", {c_ok.id}),
            ("active", {self.conversation_a.id, c_wait.id, c_pause.id}),
            ("failed", {c_fail.id}),
            ("no_runs", {c_empty.id}),
        ):
            res = await self.client.get(
                f"/admin/accounts/{self.user_a.account.id}/conversations?status={status_filter}",
                headers=headers,
            )
            self.assertEqual(res.status_code, 200, (status_filter, res.text))
            body = res.json()
            self.assertEqual({row["id"] for row in body["conversations"]}, expected_ids)
            self.assertEqual(body["status_counts"]["all"], 6)

        bad = await self.client.get(
            f"/admin/accounts/{self.user_a.account.id}/conversations?status=not_a_real_status",
            headers=headers,
        )
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(bad.json()["detail"], "invalid_conversation_status")


if __name__ == "__main__":
    unittest.main()
