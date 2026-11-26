import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from backend.application.accounts.service import AccountService
from backend.application.workspace.conversations import WorkspaceService
from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database import Database
from backend.infrastructure.database.models import ConversationMaterialModel, MaterialModel


class MaterialConversationCountTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = DatabaseSettings(url=f"sqlite:///{Path(self.tmp.name) / 'm.sqlite3'}")
        self.database = Database(settings)
        self.database.initialize()
        accounts = AccountService(self.database, settings, AuthSettings(password_min_length=8))
        self.owner = accounts.register(email="owner@example.com", password="a-long-owner-password").account
        self.other = accounts.register(email="other@example.com", password="a-long-other-password").account
        self.workspace = WorkspaceService(self.database)

    def tearDown(self):
        self.database.close()
        self.tmp.cleanup()

    def _material(self, material_id: str, account_id: str):
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            session.add(MaterialModel(
                id=material_id, account_id=account_id, session_id="s", filename=f"{material_id}.txt",
                media_type="text/plain", size_bytes=1, checksum="c" * 64, storage_key=f"key-{material_id}",
                status="ready", created_at=now, updated_at=now,
            ))
            session.commit()

    def test_counts_the_conversations_each_file_is_attached_to(self):
        self._material("m1", self.owner.id)
        self._material("m2", self.owner.id)
        self._material("foreign", self.other.id)
        first = self.workspace.create(account_id=self.owner.id, title="一", mode="chat")
        second = self.workspace.create(account_id=self.owner.id, title="二", mode="chat")
        with self.database.session() as session:
            session.add_all([
                ConversationMaterialModel(conversation_id=first.id, material_id="m1"),
                ConversationMaterialModel(conversation_id=second.id, material_id="m1"),
            ])
            session.commit()
        counts = self.workspace.material_conversation_counts(account_id=self.owner.id)
        self.assertEqual(counts, {"m1": 2})
        self.assertEqual(counts.get("m2", 0), 0)
        self.assertNotIn("foreign", counts)


    def test_replaying_an_unchanged_run_does_not_move_the_conversation(self):
        conversation = self.workspace.create(account_id=self.owner.id, title="回放", mode="chat")
        kwargs = dict(
            account_id=self.owner.id, conversation_id=conversation.id, run_id="r1",
            question="问题", status="succeeded", answer="答案",
        )
        self.workspace.record_run(**kwargs)
        with self.database.session() as session:
            from backend.infrastructure.database.models import ConversationModel

            row = session.get(ConversationModel, conversation.id)
            row.updated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
            session.commit()
        self.workspace.record_run(**kwargs)  # what a restart does for every old Run
        with self.database.session() as session:
            self.assertEqual(session.get(ConversationModel, conversation.id).updated_at.year, 2026)
            self.assertEqual(session.get(ConversationModel, conversation.id).updated_at.month, 1)
        self.workspace.record_run(**{**kwargs, "status": "failed", "answer": None})
        with self.database.session() as session:
            self.assertGreater(session.get(ConversationModel, conversation.id).updated_at.year, 2026 - 1)
            self.assertNotEqual(session.get(ConversationModel, conversation.id).updated_at.month, 1)


if __name__ == "__main__":
    unittest.main()
