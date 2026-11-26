import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.application.admin_operations import AdminOperationsService
from backend.application.ops_settings import OpsSettingsService
from backend.application.system_metrics import (
    HttpLatencyTracker,
    SystemMetricsService,
    elastic_storage_status,
    resolve_elastic_storage_policy,
)
from backend.config.settings import load_settings
from backend.contracts.modules.loop import RunInput, RunResult
from backend.harness.lifecycle.scheduler import InMemoryRunScheduler
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import (
    AccountModel,
    ModelCallMetricModel,
)
from backend.interfaces.api.app import create_app


class _ConcurrencyTrackingExecutor:
    def __init__(self, delay: float = 0.08):
        self.delay = delay
        self.current = 0
        self.peak = 0

    async def execute(self, run, control):
        self.current += 1
        self.peak = max(self.peak, self.current)
        try:
            await asyncio.sleep(self.delay)
            return RunResult(run_id=run.run_id, status="succeeded", answer="ok", elapsed_seconds=self.delay)
        finally:
            self.current -= 1


class SystemMetricsAndElasticQuotaTests(unittest.TestCase):
    def test_elastic_storage_policy_tiers(self) -> None:
        ok_hs = {"status": "ok", "free_bytes": 50 * 1024**3}
        warn_hs = {"status": "warn", "free_bytes": 12 * 1024**3}
        crit_hs = {"status": "critical", "free_bytes": 2 * 1024**3}

        self.assertEqual(elastic_storage_status(1024, ok_hs)["effective_default_quota_mb"], 1024)
        self.assertEqual(elastic_storage_status(1024, warn_hs)["effective_default_quota_mb"], 256)
        self.assertTrue(elastic_storage_status(1024, crit_hs)["uploads_paused"])

        p_ok = resolve_elastic_storage_policy(base_quota_mb=1024, host_storage=ok_hs, account_quota_mb=512)
        self.assertEqual(p_ok["quota_bytes"], 512 * 1024 * 1024)

        p_unlim = resolve_elastic_storage_policy(base_quota_mb=1024, host_storage=ok_hs, account_quota_mb=0)
        self.assertIsNone(p_unlim["quota_bytes"])

        p_warn_custom = resolve_elastic_storage_policy(base_quota_mb=1024, host_storage=warn_hs, account_quota_mb=2048)
        self.assertEqual(p_warn_custom["effective_quota_mb"], 256)

        p_crit = resolve_elastic_storage_policy(base_quota_mb=1024, host_storage=crit_hs, account_quota_mb=2048)
        self.assertTrue(p_crit["uploads_paused"])

    def test_http_latency_tracker_percentiles_and_errors(self) -> None:
        tracker = HttpLatencyTracker(max_samples=100)
        for ms in (10.0, 20.0, 30.0, 40.0, 100.0):
            tracker.record(ms, 200)
        tracker.record(200.0, 502)
        snap = tracker.snapshot(window_seconds=60)
        self.assertEqual(snap["request_count"], 6)
        self.assertEqual(snap["error_count"], 1)
        self.assertGreaterEqual(snap["p50_ms"], 30.0)
        self.assertGreaterEqual(snap["p95_ms"], 100.0)

    def test_scheduler_global_concurrency_cap_and_runtime_stats(self) -> None:
        async def _run():
            executor = _ConcurrencyTrackingExecutor(delay=0.06)
            scheduler = InMemoryRunScheduler(executor, max_concurrent_runs=2)
            tasks = [
                await scheduler.submit(
                    session_id=f"s{i}",
                    payload=RunInput(text="hi"),
                )
                for i in range(5)
            ]
            await asyncio.sleep(0.02)
            stats = scheduler.runtime_stats()
            self.assertLessEqual(stats["active_runs"], 2)
            self.assertGreaterEqual(stats["waiting_for_slot"], 1)
            await scheduler.shutdown() if False else None
            # Wait for all runs to finish
            for _ in range(40):
                st = scheduler.runtime_stats()
                if st["active_runs"] == 0 and st["queued_runs"] == 0:
                    break
                await asyncio.sleep(0.02)
            self.assertEqual(executor.peak, 2)
            self.assertEqual(len(tasks), 5)

        asyncio.run(_run())

    def test_admin_system_metrics_and_per_user_quota_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db = Database(f"sqlite:///{db_path.as_posix()}")
            try:
                db.initialize()
                now = datetime.now(timezone.utc)
                with db.session() as session:
                    admin = AccountModel(
                        id="adm_1",
                        email="admin@example.com",
                        display_name="Admin",
                        password_hash="x",
                        role="admin",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    )
                    user = AccountModel(
                        id="usr_1",
                        email="user@example.com",
                        display_name="User",
                        password_hash="x",
                        role="user",
                        status="active",
                        created_at=now,
                        updated_at=now,
                    )
                    session.add_all([admin, user])
                    session.add(
                        ModelCallMetricModel(
                            id="mc_1",
                            run_id="r1",
                            status="succeeded",
                            latency_ms=420,
                            created_at=now,
                        )
                    )
                    session.commit()

                ops = AdminOperationsService(db, load_settings())
                updated = ops.update_capability(
                    actor_id="adm_1", account_id="usr_1", material_quota_mb=256
                )
                self.assertEqual(updated["material_quota_mb"], 256)
                self.assertEqual(ops.account_grant("usr_1")["material_quota_mb"], 256)

                cleared = ops.update_capability(
                    actor_id="adm_1", account_id="usr_1", clear_material_quota=True
                )
                self.assertIsNone(cleared["material_quota_mb"])

                metrics_svc = SystemMetricsService(settings=load_settings(), database=db)
                live_compute = metrics_svc._read_compute_metrics()
                self.assertGreater(live_compute["cpu_cores"], 0)
                self.assertGreater(live_compute["memory_limit_bytes"], 0)
                with (
                    patch.object(metrics_svc, "_probe_opensearch", return_value={"status": "ready", "heap_used_percent": 32}),
                    patch.object(
                        metrics_svc,
                        "_read_compute_metrics",
                        return_value={
                            "cpu_cores": 2.0,
                            "cpu_percent": 18.0,
                            "load_1m": 0.3,
                            "memory_used_bytes": 2 * 1024**3,
                            "memory_limit_bytes": 6 * 1024**3,
                            "memory_percent": 33.3,
                            "process_rss_bytes": 800 * 1024**2,
                            "host_memory_total_bytes": 16 * 1024**3,
                            "host_memory_available_bytes": 10 * 1024**3,
                            "in_container": True,
                        },
                    ),
                ):
                    snap = metrics_svc.collect(
                        scheduler_stats={"active_runs": 1, "waiting_for_slot": 0, "session_queued": 0, "queued_runs": 0, "active_sessions": 1, "max_concurrent_runs": 8},
                        active_sse_streams=2,
                        http_metrics={"window_seconds": 300, "inflight_requests": 1, "request_count": 10, "rpm": 12.0, "p50_ms": 15.0, "p95_ms": 42.0, "error_count": 0, "error_rate": 0.0},
                        host_storage={"total_bytes": 100 * 1024**3, "used_bytes": 40 * 1024**3, "free_bytes": 60 * 1024**3, "status": "ok"},
                    )
                self.assertEqual(snap["concurrency"]["active_sse_streams"], 2)
                self.assertEqual(snap["model_calls"]["recent_calls"], 1)
                self.assertEqual(snap["capacity"]["load_level"], "normal")
            finally:
                db.close()

    def test_pure_asgi_middleware_tracks_http_and_skips_events(self) -> None:
        settings = load_settings()
        app = create_app(settings=settings)
        with TestClient(app) as client:
            r = client.get("/healthz")
            self.assertIn(r.status_code, (200, 503))
            snap = app.state.http_latency_tracker.snapshot()
            self.assertEqual(snap["request_count"], 0)
            r2 = client.get("/does-not-exist-route")
            self.assertEqual(r2.status_code, 404)
            snap2 = app.state.http_latency_tracker.snapshot()
            self.assertEqual(snap2["request_count"], 1)


if __name__ == "__main__":
    unittest.main()
