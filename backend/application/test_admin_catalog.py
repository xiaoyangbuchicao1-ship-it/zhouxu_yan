import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from backend.application.accounts.service import AccountError, AccountService
from backend.application.admin import AdminService
from backend.application.admin_catalog import AdminCatalogService
from backend.config.settings import AuthSettings, DatabaseSettings, load_settings
from backend.contracts.modules.loop import ToolSpec
from backend.infrastructure.database import Database
from backend.infrastructure.telemetry.metrics import MetricsStore, source_of


def _spec(name, *, read_only=True, description="检索资料。附加说明。"):
    return ToolSpec(
        name=name,
        description=description,
        parameters={"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]},
        read_only=read_only,
    )


class MetricsStoreTest(unittest.TestCase):
    def test_summary_counts_outcomes_and_keeps_only_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MetricsStore(Path(tmp) / "t.sqlite3")
            store.record_tool(tool="search_cases", outcome="ok", duration_ms=100, run_id="r1")
            store.record_tool(tool="search_cases", outcome="empty", duration_ms=300, run_id="r2")
            store.record_tool(tool="search_cases", outcome="error", duration_ms=50, error_code="timeout")
            store.record_tool(tool="bash", outcome="ok", duration_ms=10)
            summary = store.tool_summary()
            self.assertEqual(summary["search_cases"]["calls"], 3)
            self.assertEqual(summary["search_cases"]["errors"], 1)
            self.assertEqual(summary["search_cases"]["empty"], 1)
            self.assertEqual(summary["search_cases"]["avg_ms"], 150)
            self.assertEqual(summary["search_cases"]["last_error_code"], "timeout")
            by_source = store.source_summary()
            self.assertEqual(by_source["cases"]["calls"], 3)
            self.assertEqual(by_source["sandbox"]["calls"], 1)
            self.assertEqual(len(store.recent_calls("search_cases")), 3)
            self.assertEqual(source_of("never-heard-of-it"), "other")


class RelativePathTest(unittest.TestCase):
    def test_a_relative_store_path_still_reads_back(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                store = MetricsStore(Path("rel") / "t.sqlite3")
                store.record_tool(tool="read", outcome="ok", duration_ms=5)
                self.assertEqual(store.tool_summary()["read"]["calls"], 1)
            finally:
                os.chdir(previous)


class CatalogServiceTest(unittest.TestCase):
    def test_tools_come_from_the_registry_with_modes_health_and_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MetricsStore(Path(tmp) / "t.sqlite3")
            store.record_tool(tool="search_cases", outcome="ok", duration_ms=80)
            harness = SimpleNamespace(
                tools=[_spec("search_cases"), _spec("bash", read_only=False)],
                material_tool=None,
                material_search_tool=None,
                modules={},
            )
            service = AdminCatalogService(load_settings(), None, Path(tmp))
            result = service.tools(harness, store, {"cases": {"status": "degraded", "reason": "索引离线"}, "sandbox": {"status": "ready"}})
            by_name = {row["name"]: row for row in result["tools"]}
            self.assertEqual(result["summary"]["total"], 2)
            self.assertTrue(by_name["search_cases"]["chat"] and by_name["search_cases"]["work"])
            self.assertFalse(by_name["bash"]["chat"])
            self.assertTrue(by_name["bash"]["work"])
            self.assertEqual(by_name["search_cases"]["status"], "degraded")
            self.assertEqual(by_name["search_cases"]["calls"], 1)
            self.assertEqual(by_name["bash"]["calls"], 0)
            self.assertEqual(by_name["search_cases"]["summary"], "检索资料。")
            self.assertEqual([p["name"] for p in by_name["search_cases"]["parameters"]], ["query", "limit"])
            self.assertTrue(by_name["search_cases"]["parameters"][0]["required"])

    def test_data_lists_real_files_tables_and_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            with closing(sqlite3.connect(runtime / "runs.sqlite3")) as db:
                db.execute("CREATE TABLE run_sessions (id TEXT)")
                db.execute("INSERT INTO run_sessions VALUES ('a')")
                db.commit()
            (runtime / "backups").mkdir()
            (runtime / "backups" / "old.sqlite3").write_bytes(b"x" * 10)
            service = AdminCatalogService(load_settings(), None, runtime)
            result = service.data(
                {"sources": {"laws": {"status": "ready", "count": 7}, "cases": {"status": "unavailable", "reason": "离线"}}},
                None,
            )
            store = next(item for item in result["stores"] if item["file"] == "runs.sqlite3")
            self.assertEqual(store["label"], "运行库")
            self.assertEqual(store["tables"], [{"name": "run_sessions", "rows": 1}])
            self.assertEqual(result["backups"][0]["name"], "old.sqlite3")
            laws = next(item for item in result["corpus"] if item["key"] == "laws")
            self.assertEqual(laws["count"], 7)
            self.assertFalse(result["recording"])


class AuditSecurityTest(unittest.TestCase):
    def test_failed_sign_ins_are_recorded_and_summarised(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = DatabaseSettings(url=f"sqlite:///{Path(tmp) / 'a.sqlite3'}")
            database = Database(settings)
            database.initialize()
            accounts = AccountService(database, settings, AuthSettings(password_min_length=8))
            accounts.register(email="user@example.com", password="a-long-user-password")
            accounts.ensure_admin(email="admin@example.com", password="a-long-admin-password")
            with self.assertRaises(AccountError):
                accounts.login(email="user@example.com", password="wrong-password-123")
            with self.assertRaises(AccountError):
                accounts.admin_login(email="user@example.com", password="a-long-user-password")
            accounts.login(email="user@example.com", password="a-long-user-password")
            summary = AdminService(database).audit_summary()
            self.assertEqual(summary["login_failures_24h"], 2)
            self.assertEqual(summary["logins_24h"], 1)
            self.assertEqual(summary["operations_24h"], 0)
            failed = [e for e in AdminService(database).list_audit() if e["action"] == "auth.login_failed"]
            self.assertEqual(len(failed), 2)
            self.assertTrue(all(e["outcome"] == "failed" for e in failed))
            database.close()


if __name__ == "__main__":
    unittest.main()
