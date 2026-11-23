from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.config.settings import DatabaseSettings
from backend.infrastructure.database.connection import Database
from backend.infrastructure.persistence.migrate_dynamic_admin import migrate


class DynamicAdminMigrationTests(unittest.TestCase):
    def test_preflight_is_read_only_and_apply_preserves_business_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, backup = root / "source.sqlite3", root / "backup.sqlite3"
            with closing(sqlite3.connect(source)) as db:
                db.executescript(
                    """
                    PRAGMA foreign_keys=ON;
                    CREATE TABLE accounts(id TEXT PRIMARY KEY);
                    CREATE TABLE conversations(id TEXT PRIMARY KEY, account_id TEXT REFERENCES accounts(id));
                    CREATE TABLE conversation_runs(run_id TEXT PRIMARY KEY, conversation_id TEXT REFERENCES conversations(id), question TEXT, answer TEXT);
                    CREATE TABLE business_audits(id TEXT PRIMARY KEY, actor_id TEXT REFERENCES accounts(id), action TEXT, target_account_id TEXT, details_json TEXT, created_at DATETIME);
                    INSERT INTO accounts VALUES('account-1');
                    INSERT INTO conversations VALUES('conversation-1','account-1');
                    INSERT INTO conversation_runs VALUES('run-1','conversation-1','原问题','原回答');
                    """
                )
            before = source.read_bytes()
            preflight = migrate(source)
            self.assertFalse(preflight["applied"])
            self.assertEqual(source.read_bytes(), before)

            applied = migrate(source, apply=True, backup=backup)
            self.assertTrue(applied["applied"])
            self.assertTrue(backup.is_file())
            with closing(sqlite3.connect(source)) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertIn("model_provider_revisions", tables)
                self.assertIn("model_profile_revisions", tables)
                self.assertIn("model_call_metrics", tables)
                self.assertEqual(db.execute("SELECT question,answer FROM conversation_runs").fetchone(), ("原问题", "原回答"))
                columns = {row[1] for row in db.execute("PRAGMA table_info(conversation_runs)")}
                self.assertIn("model_profile_revision_id", columns)
                profile_columns = {row[1] for row in db.execute("PRAGMA table_info(model_profiles)")}
                self.assertIn("archived_at", profile_columns)
                provider_columns = {row[1] for row in db.execute("PRAGMA table_info(model_providers)")}
                self.assertIn("notes", provider_columns)
                versions = {row[0] for row in db.execute("SELECT version FROM schema_versions")}
                self.assertEqual(versions, {"dynamic_admin_v1", "dynamic_admin_v2", "dynamic_admin_v3"})
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertIsNone(db.execute("PRAGMA foreign_key_check").fetchone())
            with closing(sqlite3.connect(backup)) as db:
                self.assertNotIn(
                    "model_providers",
                    {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                )

            again = migrate(source)
            self.assertTrue(again["already_migrated"])

    def test_apply_requires_unused_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite3"
            with closing(sqlite3.connect(source)) as db:
                db.execute("CREATE TABLE accounts(id TEXT PRIMARY KEY)")
            with self.assertRaisesRegex(ValueError, "apply_requires_new_backup_path"):
                migrate(source, apply=True)

    def test_runtime_gate_rejects_existing_unmigrated_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE accounts(id TEXT PRIMARY KEY)")
                db.commit()
            database = Database(DatabaseSettings(url=f"sqlite:///{path}"))
            try:
                with self.assertRaisesRegex(RuntimeError, "explicit_migration_required"):
                    database.initialize(require_explicit_migrations=True)
            finally:
                database.close()
            with closing(sqlite3.connect(path)) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertNotIn("model_profiles", tables)

    def test_v1_to_v2_upgrade_adds_archived_at_and_backfills_connectivity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, backup = root / "v1.sqlite3", root / "v1_backup.sqlite3"
            with closing(sqlite3.connect(source)) as db:
                db.executescript(
                    """
                    PRAGMA foreign_keys=ON;
                    CREATE TABLE schema_versions(version TEXT PRIMARY KEY, applied_at DATETIME NOT NULL);
                    INSERT INTO schema_versions VALUES('dynamic_admin_v1', '2026-11-21 00:00:00');
                    CREATE TABLE accounts(id TEXT PRIMARY KEY);
                    CREATE TABLE model_providers(
                        id VARCHAR(64) PRIMARY KEY,
                        name VARCHAR(120) NOT NULL,
                        preset_type VARCHAR(32) NOT NULL DEFAULT 'custom',
                        default_protocol VARCHAR(64) NOT NULL,
                        auth_style VARCHAR(32) NOT NULL DEFAULT 'bearer',
                        base_url TEXT NOT NULL,
                        extra_headers_json TEXT,
                        credential_mode VARCHAR(24) NOT NULL DEFAULT 'env_ref',
                        credential_ref VARCHAR(120),
                        api_key_encrypted TEXT,
                        api_key_hint VARCHAR(32),
                        enabled BOOLEAN NOT NULL DEFAULT 1,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        archived_at DATETIME
                    );
                    CREATE TABLE model_provider_revisions(
                        id VARCHAR(80) PRIMARY KEY,
                        provider_id VARCHAR(64) NOT NULL REFERENCES model_providers(id) ON DELETE CASCADE,
                        revision INTEGER NOT NULL,
                        default_protocol VARCHAR(64) NOT NULL,
                        auth_style VARCHAR(32) NOT NULL DEFAULT 'bearer',
                        base_url TEXT NOT NULL,
                        credential_mode VARCHAR(24) NOT NULL DEFAULT 'env_ref',
                        credential_ref VARCHAR(120),
                        config_json TEXT,
                        created_at DATETIME NOT NULL
                    );
                    CREATE TABLE model_profiles(
                        profile_id VARCHAR(160) PRIMARY KEY,
                        provider_id VARCHAR(64) NOT NULL REFERENCES model_providers(id) ON DELETE CASCADE,
                        upstream_model_id VARCHAR(200) NOT NULL,
                        label VARCHAR(160) NOT NULL,
                        family VARCHAR(32) NOT NULL,
                        protocol_override VARCHAR(64),
                        context_tokens INTEGER NOT NULL,
                        max_output_tokens INTEGER NOT NULL,
                        thinking_modes_json TEXT NOT NULL DEFAULT '["none"]',
                        default_thinking VARCHAR(16) NOT NULL DEFAULT 'none',
                        capabilities_json TEXT,
                        enabled BOOLEAN NOT NULL DEFAULT 1,
                        access_scope VARCHAR(24) NOT NULL DEFAULT 'all',
                        is_default BOOLEAN NOT NULL DEFAULT 0,
                        sort_order INTEGER NOT NULL DEFAULT 100,
                        verification_json TEXT,
                        current_revision INTEGER NOT NULL DEFAULT 1,
                        source VARCHAR(120) NOT NULL DEFAULT 'admin',
                        checked_on VARCHAR(40),
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL
                    );
                    INSERT INTO model_providers(id, name, default_protocol, base_url, created_at, updated_at)
                    VALUES('deepseek', 'DeepSeek 官方', 'deepseek_chat_completions', 'https://api.deepseek.com', '2026-11-21', '2026-11-21');
                    INSERT INTO model_provider_revisions
                    VALUES('deepseek:r1', 'deepseek', 1, 'deepseek_chat_completions', 'bearer', 'https://api.deepseek.com', 'env_ref', 'DEEPSEEK_API_KEY', NULL, '2026-11-21');
                    INSERT INTO model_profiles(
                        profile_id, provider_id, upstream_model_id, label, family, context_tokens, max_output_tokens, created_at, updated_at
                    ) VALUES(
                        'deepseek-flash', 'deepseek', 'deepseek-chat', 'DeepSeek 极速', 'deepseek', 128000, 8192, '2026-11-21', '2026-11-21'
                    );
                    """
                )
            database = Database(DatabaseSettings(url=f"sqlite:///{source}"))
            try:
                with self.assertRaisesRegex(RuntimeError, "explicit_migration_required"):
                    database.initialize(require_explicit_migrations=True)
            finally:
                database.close()

            applied = migrate(source, apply=True, backup=backup)
            self.assertTrue(applied["applied"])
            with closing(sqlite3.connect(source)) as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(model_profiles)")}
                self.assertIn("archived_at", columns)
                raw = db.execute("SELECT verification_json FROM model_profiles WHERE profile_id='deepseek-flash'").fetchone()[0]
                self.assertIsNotNone(raw)
                import json
                data = json.loads(raw)
                self.assertEqual(data["connectivity"]["status"], "verified")
                self.assertEqual(data["connectivity"]["provider_revision_id"], "deepseek:r1")
                provider_columns = {row[1] for row in db.execute("PRAGMA table_info(model_providers)")}
                self.assertIn("notes", provider_columns)
                versions = {row[0] for row in db.execute("SELECT version FROM schema_versions")}
                self.assertEqual(versions, {"dynamic_admin_v1", "dynamic_admin_v2", "dynamic_admin_v3"})


if __name__ == "__main__":
    unittest.main()
