from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import httpx

from backend.application.accounts.service import AccountService
from backend.application.workspace.materials import MaterialService
from backend.config.settings import AuthSettings, DatabaseSettings, load_settings
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.migrate_ui_ux_v2 import MIGRATION_ID, migrate_ui_ux_v2
from backend.infrastructure.database.models import ConversationRunModel
from backend.interfaces.api.app import create_app


TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class UiUxV2MigrationGoldenTests(unittest.TestCase):
    def test_migration_creates_alias_table_and_sound_column_idempotently(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "pre_v2.db"
            backup_path = Path(tmp) / "backup.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE accounts (
                        id VARCHAR(32) PRIMARY KEY,
                        email VARCHAR(320) NOT NULL UNIQUE
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE user_preferences (
                        account_id VARCHAR(32) PRIMARY KEY,
                        default_mode VARCHAR(16) NOT NULL DEFAULT 'chat',
                        default_thinking_mode VARCHAR(16) NOT NULL DEFAULT 'none',
                        concise_process BOOLEAN NOT NULL DEFAULT 0,
                        compact_density BOOLEAN NOT NULL DEFAULT 0,
                        reduced_motion BOOLEAN NOT NULL DEFAULT 0,
                        memory_enabled BOOLEAN NOT NULL DEFAULT 0,
                        memory_text TEXT NOT NULL DEFAULT '',
                        commands_seeded BOOLEAN NOT NULL DEFAULT 0,
                        updated_at DATETIME NOT NULL
                    )
                    """
                )
                conn.execute("INSERT INTO accounts (id, email) VALUES ('u1', 'u1@example.com')")
                conn.execute(
                    "INSERT INTO user_preferences (account_id, updated_at) VALUES ('u1', '2026-11-26T00:00:00Z')"
                )
                conn.execute(
                    "CREATE TABLE schema_versions (version TEXT PRIMARY KEY, applied_at DATETIME NOT NULL)"
                )
                conn.commit()

            dry = migrate_ui_ux_v2(db_path, apply=False)
            self.assertFalse(dry["already_migrated"])

            applied = migrate_ui_ux_v2(db_path, apply=True, backup=backup_path)
            self.assertTrue(applied["applied"])
            self.assertTrue(backup_path.is_file())

            with closing(sqlite3.connect(db_path)) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertIn("admin_account_aliases", tables)
                cols = {row[1] for row in conn.execute("PRAGMA table_info(user_preferences)")}
                self.assertIn("completion_sound_enabled", cols)
                sound_val = conn.execute(
                    "SELECT completion_sound_enabled FROM user_preferences WHERE account_id='u1'"
                ).fetchone()
                self.assertEqual(sound_val, (1,))
                ver = conn.execute("SELECT 1 FROM schema_versions WHERE version=?", (MIGRATION_ID,)).fetchone()
                self.assertIsNotNone(ver)

            again = migrate_ui_ux_v2(db_path, apply=False)
            self.assertTrue(again["already_migrated"])


class UiUxEnhancementsApiGoldenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        db_settings = DatabaseSettings(url=f"sqlite:///{root / 'ui_ux_v2.sqlite3'}")
        self.database = Database(db_settings)
        self.database.initialize()
        self.accounts = AccountService(self.database, db_settings, AuthSettings(password_min_length=8))
        self.admin1 = self.accounts.ensure_admin(email="admin1@example.com", password="a-long-admin-password")
        self.admin2 = self.accounts.ensure_admin(email="admin2@example.com", password="a-long-admin-password")
        self.user_reg = self.accounts.register(
            email="sailor@example.com",
            password="a-long-user-password",
            display_name="船长原本称呼",
        )
        self.other_reg = self.accounts.register(
            email="other@example.com",
            password="a-long-user-password",
            display_name="其他用户",
        )
        self.user_id = self.user_reg.account.id
        self.other_id = self.other_reg.account.id
        self.admin1_token = self.accounts.login(email="admin1@example.com", password="a-long-admin-password").token
        self.admin2_token = self.accounts.login(email="admin2@example.com", password="a-long-admin-password").token
        self.user_token = self.accounts.login(email="sailor@example.com", password="a-long-user-password").token
        self.other_token = self.accounts.login(email="other@example.com", password="a-long-user-password").token

        settings = load_settings().model_copy(deep=True)
        settings.database = db_settings
        settings.materials.storage_root = str(root / "uploads")
        settings.application["office_store_path"] = str(root / "office.sqlite3")
        mat_service = MaterialService(self.database, settings)
        self.app = create_app(account_service=self.accounts, material_service=mat_service, settings=settings)
        self.app.state.material_service = mat_service
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.database.engine.dispose()
        self.tmp.cleanup()

    async def test_per_admin_aliases_isolated_and_searchable(self) -> None:
        r1 = await self.client.put(
            f"/admin/accounts/{self.user_id}/alias",
            headers={"Authorization": f"Bearer {self.admin1_token}"},
            json={"alias": "张三·船务课题", "note": "海商法研究联系人"},
        )
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json()["alias"], "张三·船务课题")
        self.assertEqual(r1.json()["display_name"], "船长原本称呼")

        r2 = await self.client.put(
            f"/admin/accounts/{self.user_id}/alias",
            headers={"Authorization": f"Bearer {self.admin2_token}"},
            json={"alias": "李四同事", "note": "测试组"},
        )
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["alias"], "李四同事")

        # Admin 1 searches by their own alias and sees only their alias.
        search1 = await self.client.get(
            "/admin/accounts?q=船务课题",
            headers={"Authorization": f"Bearer {self.admin1_token}"},
        )
        self.assertEqual(search1.status_code, 200)
        items1 = search1.json()["accounts"]
        self.assertEqual([a["id"] for a in items1], [self.user_id])
        self.assertEqual(items1[0]["alias"], "张三·船务课题")

        # Admin 2 does not match Admin 1's alias, and sees Admin 2's own alias.
        search2_miss = await self.client.get(
            "/admin/accounts?q=船务课题",
            headers={"Authorization": f"Bearer {self.admin2_token}"},
        )
        self.assertEqual(search2_miss.status_code, 200)
        self.assertEqual(search2_miss.json()["accounts"], [])

        search2_hit = await self.client.get(
            "/admin/accounts?q=李四同事",
            headers={"Authorization": f"Bearer {self.admin2_token}"},
        )
        self.assertEqual(search2_hit.status_code, 200)
        self.assertEqual(search2_hit.json()["accounts"][0]["alias"], "李四同事")

        # Clearing Admin 1 alias does not disturb Admin 2.
        cleared = await self.client.put(
            f"/admin/accounts/{self.user_id}/alias",
            headers={"Authorization": f"Bearer {self.admin1_token}"},
            json={"alias": "", "note": ""},
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.json()["alias"])
        still_2 = await self.client.get(
            "/admin/accounts?q=李四同事",
            headers={"Authorization": f"Bearer {self.admin2_token}"},
        )
        self.assertEqual(len(still_2.json()["accounts"]), 1)

    async def test_preferences_completion_sound_toggle_and_persistence(self) -> None:
        initial = await self.client.get(
            "/account/preferences",
            headers={"Authorization": f"Bearer {self.user_token}"},
        )
        self.assertEqual(initial.status_code, 200)
        self.assertTrue(initial.json()["completion_sound_enabled"])

        patched = await self.client.patch(
            "/account/preferences",
            headers={"Authorization": f"Bearer {self.user_token}"},
            json={"completion_sound_enabled": False},
        )
        self.assertEqual(patched.status_code, 200)
        self.assertFalse(patched.json()["completion_sound_enabled"])

        reread = await self.client.get(
            "/account/preferences",
            headers={"Authorization": f"Bearer {self.user_token}"},
        )
        self.assertFalse(reread.json()["completion_sound_enabled"])

    async def test_workspace_conversations_expose_live_run_activity(self) -> None:
        created = await self.client.post(
            "/workspace/conversations",
            headers={"Authorization": f"Bearer {self.user_token}"},
            json={"title": "并发测试会话", "mode": "chat"},
        )
        self.assertEqual(created.status_code, 201)
        cid = created.json()["conversation"]["conversation_id"]

        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            session.add(
                ConversationRunModel(
                    run_id="run-live-1",
                    conversation_id=cid,
                    question="请查海商法",
                    answer="",
                    status="running",
                    revision=1,
                    created_at=now,
                )
            )
            session.commit()

        listed = await self.client.get(
            "/workspace/conversations",
            headers={"Authorization": f"Bearer {self.user_token}"},
        )
        self.assertEqual(listed.status_code, 200)
        conv = next(c for c in listed.json()["conversations"] if c["conversation_id"] == cid)
        self.assertEqual(conv["active_run_count"], 1)
        self.assertEqual(conv["latest_run_id"], "run-live-1")
        self.assertEqual(conv["latest_run_status"], "running")

    async def test_admin_and_workspace_inline_image_download_routes(self) -> None:
        upload = await self.client.post(
            "/workspace/materials",
            headers={"Authorization": f"Bearer {self.user_token}"},
            files={"file": ("circuit.png", TINY_PNG, "image/png")},
        )
        self.assertEqual(upload.status_code, 201)
        mat_id = upload.json()["material"]["material_id"]

        user_inline = await self.client.get(
            f"/workspace/materials/{mat_id}/download?inline=1",
            headers={"Authorization": f"Bearer {self.user_token}"},
        )
        self.assertEqual(user_inline.status_code, 200)
        self.assertEqual(user_inline.content, TINY_PNG)
        self.assertTrue(user_inline.headers.get("content-type", "").startswith("image/png"))

        other_denied = await self.client.get(
            f"/workspace/materials/{mat_id}/download?inline=1",
            headers={"Authorization": f"Bearer {self.other_token}"},
        )
        self.assertEqual(other_denied.status_code, 404)

        admin_inline = await self.client.get(
            f"/admin/materials/{mat_id}/download?inline=1",
            headers={"Authorization": f"Bearer {self.admin1_token}"},
        )
        self.assertEqual(admin_inline.status_code, 200)
        self.assertEqual(admin_inline.content, TINY_PNG)
        self.assertNotIn("attachment", admin_inline.headers.get("content-disposition", "").lower())
