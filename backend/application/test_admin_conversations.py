from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.application.accounts.service import AccountService
from backend.application.admin import AdminService
from backend.application.admin_conversations import AdminConversationService
from backend.application.workspace.conversations import WorkspaceService
from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database.connection import Database


class AdminConversationServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = DatabaseSettings(url=f"sqlite:///{Path(self.tmp.name) / 'db.sqlite3'}")
        self.database = Database(settings)
        self.database.initialize()
        accounts = AccountService(self.database, settings, AuthSettings(password_min_length=8))
        self.admin = accounts.ensure_admin(email="admin@example.com", password="long-password")
        self.user = accounts.register(email="user@example.com", password="long-password").account
        workspace = WorkspaceService(self.database)
        self.conversation = workspace.create(account_id=self.user.id, title="测试会话", mode="work")
        started = datetime.now(timezone.utc) - timedelta(seconds=3)
        workspace.record_run(
            account_id=self.user.id,
            conversation_id=self.conversation.id,
            run_id="run-1",
            question="公开问题",
            answer="公开回答",
            status="succeeded",
            payload={
                "model_snapshot": {
                    "model_id": "gpt-test",
                    "provider": "provider",
                    "protocol": "openai_responses",
                    "thinking_mode": "low",
                },
                "system_prompt": "不应返回",
            },
            progress={
                "nodes": [{"id": "search", "label": "查找资料", "status": "succeeded"}],
                "tool_receipts": [{"secret": "不应返回"}],
            },
            created_at=started,
            completed_at=started + timedelta(seconds=2),
        )
        self.service = AdminConversationService(self.database)

    def tearDown(self):
        self.database.close()
        self.tmp.cleanup()

    def test_list_detail_and_audit_are_public_only(self):
        listed = self.service.list_conversations(account_id=self.user.id, mode="work")
        self.assertEqual(listed["conversations"][0]["run_count"], 1)
        self.assertNotIn("answer", listed["conversations"][0])

        detail = self.service.conversation_detail(
            actor_id=self.admin.id,
            account_id=self.user.id,
            conversation_id=self.conversation.id,
            limit=50,
            request_id="request-1",
        )
        run = detail["runs"][0]
        self.assertEqual(run["user_question"], "公开问题")
        self.assertEqual(run["public_answer"], "公开回答")
        self.assertEqual(run["model"], "gpt-test")
        self.assertEqual(run["nodes"], [{"id": "search", "label": "查找资料", "status": "succeeded"}])
        serialized = json.dumps(detail, ensure_ascii=False, default=str)
        self.assertNotIn("不应返回", serialized)

        audit = AdminService(self.database).list_audit(limit=10)[0]
        self.assertEqual(audit["action"], "conversation.log_viewed")
        self.assertEqual(audit["resource_type"], "conversation")
        self.assertEqual(audit["resource_id"], self.conversation.id)
        self.assertEqual(audit["details"]["request_id"], "request-1")
        self.assertNotIn("公开问题", json.dumps(audit, ensure_ascii=False, default=str))

    def test_cursor_pages_runs_without_duplicates(self):
        workspace = WorkspaceService(self.database)
        for index in range(4):
            workspace.record_run(
                account_id=self.user.id,
                conversation_id=self.conversation.id,
                run_id=f"run-page-{index}",
                question=f"问题{index}",
                answer=f"回答{index}",
                status="succeeded",
                created_at=datetime.now(timezone.utc) + timedelta(seconds=index + 1),
            )
        first = self.service.conversation_detail(
            actor_id=self.admin.id,
            account_id=self.user.id,
            conversation_id=self.conversation.id,
            limit=2,
        )
        second = self.service.conversation_detail(
            actor_id=self.admin.id,
            account_id=self.user.id,
            conversation_id=self.conversation.id,
            limit=2,
            cursor=first["next_cursor"],
        )
        ids = [item["run_id"] for item in first["runs"] + second["runs"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_source_author_lists_are_normalized_for_the_public_contract(self):
        workspace = WorkspaceService(self.database)
        workspace.record_run(
            account_id=self.user.id,
            conversation_id=self.conversation.id,
            run_id="run-source-authors",
            question="带作者列表的资料",
            answer="公开回答",
            status="succeeded",
            progress={
                "source_refs": [{
                    "source_id": "paper-1",
                    "kind": "paper",
                    "title": "资料标题",
                    "authors": ["甲", "乙"],
                }],
            },
        )
        detail = self.service.conversation_detail(
            actor_id=self.admin.id,
            account_id=self.user.id,
            conversation_id=self.conversation.id,
        )
        run = next(item for item in detail["runs"] if item["run_id"] == "run-source-authors")
        self.assertEqual(run["sources"][0]["authors"], "甲、乙")


if __name__ == "__main__":
    unittest.main()
