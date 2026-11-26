"""Live hardware, concurrency, HTTP/model latency, and elastic storage telemetry."""

from __future__ import annotations

import base64
import ctypes
import json
import math
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from sqlalchemy import select

from backend.config.settings import BACKEND_ROOT
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import ModelCallMetricModel


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return round(sorted_values[0], 1)
    idx = (len(sorted_values) - 1) * pct
    low = math.floor(idx)
    high = math.ceil(idx)
    if low == high:
        return round(sorted_values[low], 1)
    frac = idx - low
    return round(sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac, 1)


def _read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _parse_int(raw: str | None) -> int | None:
    if not raw or raw == "max":
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    # cgroup v1 expresses unlimited as a huge 2^63-ish integer
    if value >= (1 << 50):
        return None
    return value


class HttpLatencyTracker:
    """Thread-safe bounded ring buffer for recent non-streaming HTTP request timings."""

    def __init__(self, *, max_samples: int = 2000):
        self._samples: deque[tuple[float, float, int]] = deque(maxlen=max_samples)
        self._lock = threading.Lock()
        self._inflight = 0

    def enter(self) -> None:
        with self._lock:
            self._inflight += 1

    def leave(self) -> None:
        with self._lock:
            self._inflight = max(0, self._inflight - 1)

    def record(self, duration_ms: float, status_code: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._samples.append((now, max(0.0, float(duration_ms)), int(status_code)))

    def snapshot(self, *, window_seconds: int = 300) -> dict[str, Any]:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            inflight = self._inflight
            recent = [item for item in self._samples if item[0] >= cutoff]
        count = len(recent)
        if not count:
            return {
                "window_seconds": window_seconds,
                "inflight_requests": inflight,
                "request_count": 0,
                "rpm": 0.0,
                "p50_ms": None,
                "p95_ms": None,
                "error_count": 0,
                "error_rate": 0.0,
            }
        durations = sorted(item[1] for item in recent)
        errors = sum(1 for item in recent if item[2] >= 500)
        span_seconds = max(10.0, min(float(window_seconds), now - recent[0][0] + 1.0))
        return {
            "window_seconds": window_seconds,
            "inflight_requests": inflight,
            "request_count": count,
            "rpm": round(count / (span_seconds / 60.0), 1),
            "p50_ms": _percentile(durations, 0.50),
            "p95_ms": _percentile(durations, 0.95),
            "error_count": errors,
            "error_rate": round(errors / count, 4),
        }


class SystemMetricsService:
    """Collects live machine hardware, concurrency, latency, and storage telemetry."""

    def __init__(self, *, settings: Any, database: Database | None = None):
        self.settings = settings
        self.database = database
        self._cpu_lock = threading.Lock()
        self._prev_cpu_sample: tuple[float, int] | None = None  # (monotonic_s, usage_usec)

    def _read_cpu_usage_percent(self, cpu_cores: float) -> float | None:
        # cgroup v2: /sys/fs/cgroup/cpu.stat -> usage_usec
        raw_stat = _read_text("/sys/fs/cgroup/cpu.stat")
        if raw_stat:
            usage_usec = None
            for line in raw_stat.splitlines():
                if line.startswith("usage_usec "):
                    usage_usec = _parse_int(line.split()[1])
                    break
            if usage_usec is not None:
                now = time.monotonic()
                with self._cpu_lock:
                    prev = self._prev_cpu_sample
                    self._prev_cpu_sample = (now, usage_usec)
                if prev is not None and now > prev[0]:
                    delta_wall_us = (now - prev[0]) * 1_000_000.0
                    delta_cpu_us = max(0, usage_usec - prev[1])
                    pct = (delta_cpu_us / (delta_wall_us * max(0.25, cpu_cores))) * 100.0
                    return round(min(100.0, max(0.0, pct)), 1)

        if hasattr(os, "getloadavg"):
            try:
                load1 = os.getloadavg()[0]
                return round(min(100.0, max(0.0, (load1 / max(0.5, cpu_cores)) * 100.0)), 1)
            except OSError:
                pass
        return None

    def _read_compute_metrics(self) -> dict[str, Any]:
        # CPU quota from cgroup v2 / v1, falling back to host cpu_count
        cpu_cores = float(os.cpu_count() or 1)
        cpu_max = _read_text("/sys/fs/cgroup/cpu.max")
        if cpu_max:
            parts = cpu_max.split()
            if len(parts) == 2 and parts[0] != "max":
                quota = _parse_int(parts[0])
                period = _parse_int(parts[1])
                if quota and period:
                    cpu_cores = round(quota / period, 2)
        else:
            q_v1 = _parse_int(_read_text("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"))
            p_v1 = _parse_int(_read_text("/sys/fs/cgroup/cpu/cpu.cfs_period_us"))
            if q_v1 and p_v1 and q_v1 > 0:
                cpu_cores = round(q_v1 / p_v1, 2)

        load_1m: float | None = None
        if hasattr(os, "getloadavg"):
            try:
                load_1m = round(os.getloadavg()[0], 2)
            except OSError:
                load_1m = None

        # Host memory (/proc/meminfo on Linux/Docker VM, GlobalMemoryStatusEx on Windows)
        host_total: int | None = None
        host_avail: int | None = None
        meminfo = _read_text("/proc/meminfo")
        if meminfo:
            info: dict[str, int] = {}
            for line in meminfo.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0].endswith(":"):
                    val = _parse_int(parts[1])
                    if val is not None:
                        info[parts[0][:-1]] = val * 1024
            host_total = info.get("MemTotal")
            host_avail = info.get("MemAvailable")
        elif os.name == "nt":
            try:
                class _MEMSTATUS(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]

                stat = _MEMSTATUS()
                stat.dwLength = ctypes.sizeof(_MEMSTATUS)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                    host_total = int(stat.ullTotalPhys)
                    host_avail = int(stat.ullAvailPhys)
            except Exception:
                pass

        # Cgroup container memory
        cgroup_used = _parse_int(_read_text("/sys/fs/cgroup/memory.current")) or _parse_int(
            _read_text("/sys/fs/cgroup/memory/memory.usage_in_bytes")
        )
        cgroup_limit = _parse_int(_read_text("/sys/fs/cgroup/memory.max")) or _parse_int(
            _read_text("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        )
        effective_limit = (
            min(cgroup_limit, host_total)
            if cgroup_limit and host_total
            else cgroup_limit or host_total
        )

        # Process RSS from /proc/self/status
        process_rss: int | None = None
        proc_status = _read_text("/proc/self/status")
        if proc_status:
            for line in proc_status.splitlines():
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        kb = _parse_int(parts[1])
                        if kb is not None:
                            process_rss = kb * 1024
                    break

        effective_used = cgroup_used if cgroup_used is not None else (
            (host_total - host_avail) if (host_total is not None and host_avail is not None) else process_rss
        )
        mem_percent = (
            round((effective_used / effective_limit) * 100.0, 1)
            if effective_used is not None and effective_limit
            else None
        )

        return {
            "cpu_cores": cpu_cores,
            "cpu_percent": self._read_cpu_usage_percent(cpu_cores),
            "load_1m": load_1m,
            "memory_used_bytes": effective_used,
            "memory_limit_bytes": effective_limit,
            "memory_percent": mem_percent,
            "process_rss_bytes": process_rss,
            "host_memory_total_bytes": host_total,
            "host_memory_available_bytes": host_avail,
            "in_container": bool(cgroup_used is not None or Path("/.dockerenv").exists()),
        }

    def _resolve_opensearch_password(self) -> str:
        rag = getattr(self.settings, "rag", None)
        if rag is None:
            return ""
        pw_name = getattr(rag, "password_name", "OPENSEARCH_INITIAL_ADMIN_PASSWORD")
        env_val = os.environ.get(pw_name, "").strip() or os.environ.get(
            "OPENSEARCH_INITIAL_ADMIN_PASSWORD", ""
        ).strip()
        if env_val:
            return env_val
        cred_file = getattr(rag, "credential_file", "")
        if cred_file:
            path = Path(cred_file)
            if not path.is_absolute():
                path = BACKEND_ROOT / path
            if path.is_file():
                values = dotenv_values(
                    stream=StringIO(path.read_text(encoding="utf-8-sig")),
                    interpolate=False,
                )
                return str(values.get(pw_name) or values.get("OPENSEARCH_INITIAL_ADMIN_PASSWORD") or "").strip()
        return ""

    def _probe_opensearch(self) -> dict[str, Any]:
        rag = getattr(self.settings, "rag", None)
        endpoint = str(getattr(rag, "endpoint", "") or "").strip().rstrip("/")
        if not endpoint:
            return {"status": "unconfigured"}
        url = f"{endpoint}/_nodes/stats/jvm,os"
        req = urllib.request.Request(url, method="GET")
        username = str(getattr(rag, "username", "admin") or "admin")
        password = self._resolve_opensearch_password()
        if password:
            token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            req.add_header("Authorization", f"Basic {token}")
        ctx = ssl.create_default_context()
        if getattr(rag, "local_dev_certificate", False) or os.environ.get("ZHOUXU_OPENSEARCH_INSECURE", "").strip() == "1":
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        elif getattr(rag, "ca_file", None):
            try:
                ctx.load_verify_locations(cafile=str(rag.ca_file))
            except OSError:
                pass
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=1.5, context=ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            latency_ms = round((time.monotonic() - started) * 1000.0, 1)
            nodes = list((data.get("nodes") or {}).values())
            if not nodes:
                return {"status": "degraded", "latency_ms": latency_ms}
            node = nodes[0]
            jvm_mem = (node.get("jvm") or {}).get("mem") or {}
            os_cpu = ((node.get("os") or {}).get("cpu") or {})
            heap_used = jvm_mem.get("heap_used_in_bytes")
            heap_max = jvm_mem.get("heap_max_in_bytes")
            heap_pct = jvm_mem.get("heap_used_percent")
            return {
                "status": "ready",
                "latency_ms": latency_ms,
                "heap_used_bytes": int(heap_used) if heap_used is not None else None,
                "heap_max_bytes": int(heap_max) if heap_max is not None else None,
                "heap_used_percent": int(heap_pct) if heap_pct is not None else None,
                "cpu_percent": os_cpu.get("percent"),
            }
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            return {"status": "unavailable", "reason": type(exc).__name__}

    def _query_model_metrics(self) -> dict[str, Any]:
        if self.database is None:
            return {
                "window_minutes": 15,
                "recent_calls": 0,
                "recent_errors": 0,
                "recent_p50_ms": None,
                "recent_p95_ms": None,
                "calls_24h": 0,
                "errors_24h": 0,
                "p50_24h_ms": None,
                "p95_24h_ms": None,
            }
        now = datetime.now(timezone.utc)
        cutoff_24h = now - timedelta(hours=24)
        cutoff_15m = now - timedelta(minutes=15)
        with self.database.session() as session:
            rows = session.execute(
                select(
                    ModelCallMetricModel.status,
                    ModelCallMetricModel.latency_ms,
                    ModelCallMetricModel.created_at,
                )
                .where(ModelCallMetricModel.created_at >= cutoff_24h)
                .order_by(ModelCallMetricModel.created_at.desc())
                .limit(2000)
            ).all()

        lat_24h: list[float] = []
        lat_15m: list[float] = []
        errors_24h = 0
        recent_calls = 0
        recent_errors = 0
        for status, latency_ms, created_at in rows:
            is_err = status != "succeeded"
            if is_err:
                errors_24h += 1
            if latency_ms is not None:
                lat_24h.append(float(latency_ms))
            ts = created_at.replace(tzinfo=timezone.utc) if created_at.tzinfo is None else created_at
            if ts >= cutoff_15m:
                recent_calls += 1
                if is_err:
                    recent_errors += 1
                if latency_ms is not None:
                    lat_15m.append(float(latency_ms))

        lat_24h.sort()
        lat_15m.sort()
        return {
            "window_minutes": 15,
            "recent_calls": recent_calls,
            "recent_errors": recent_errors,
            "recent_p50_ms": _percentile(lat_15m, 0.50),
            "recent_p95_ms": _percentile(lat_15m, 0.95),
            "calls_24h": len(rows),
            "errors_24h": errors_24h,
            "p50_24h_ms": _percentile(lat_24h, 0.50),
            "p95_24h_ms": _percentile(lat_24h, 0.95),
        }

    @staticmethod
    def _compute_capacity(
        *,
        compute: dict[str, Any],
        concurrency: dict[str, Any],
        http: dict[str, Any],
        storage: dict[str, Any],
    ) -> dict[str, Any]:
        limit_bytes = compute.get("memory_limit_bytes") or (4 * 1024**3)
        cpu_cores = float(compute.get("cpu_cores") or 2.0)
        headroom_gb = max(0.5, (limit_bytes / (1024**3)) - 2.0)
        cap_by_mem = max(2, int(headroom_gb / 0.25))
        cap_by_cpu = max(2, int(cpu_cores * 4))
        max_runs = int(concurrency.get("max_concurrent_runs") or 8)
        recommended_concurrent_runs = min(max_runs or cap_by_mem, cap_by_mem, cap_by_cpu)
        recommended_online_users = max(
            recommended_concurrent_runs * 3,
            min(80, int(headroom_gb * 10)),
        )

        mem_pct = compute.get("memory_percent") or 0.0
        cpu_pct = compute.get("cpu_percent") or 0.0
        queued = int(concurrency.get("queued_runs") or 0)
        err_rate = float(http.get("error_rate") or 0.0)
        disk_status = storage.get("status", "ok")

        level = "normal"
        reasons: list[str] = []
        if mem_pct >= 90 or cpu_pct >= 90 or (max_runs > 0 and queued >= max_runs) or err_rate >= 0.05 or disk_status == "critical":
            level = "overloaded"
        elif mem_pct >= 75 or cpu_pct >= 75 or queued > 0 or disk_status == "warn":
            level = "busy"

        if mem_pct >= 80:
            reasons.append(f"容器内存占用 {mem_pct:.0f}%")
        if cpu_pct >= 80:
            reasons.append(f"CPU 负载 {cpu_pct:.0f}%")
        if queued > 0:
            reasons.append(f"有 {queued} 个任务正在排队")
        if err_rate >= 0.05:
            reasons.append(f"近 5 分钟接口异常率 {err_rate * 100:.1f}%")
        if disk_status == "critical":
            reasons.append("磁盘剩余不足 5 GB，已暂停新上传")
        elif disk_status == "warn":
            reasons.append("磁盘剩余不足 20 GB，已触发弹性缩容")

        return {
            "load_level": level,
            "recommended_concurrent_runs": recommended_concurrent_runs,
            "recommended_online_users": recommended_online_users,
            "reasons": reasons,
        }

    def collect(
        self,
        *,
        scheduler_stats: dict[str, int] | None = None,
        active_sse_streams: int = 0,
        http_metrics: dict[str, Any] | None = None,
        host_storage: dict[str, Any] | None = None,
        elastic_storage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        compute = self._read_compute_metrics()
        opensearch = self._probe_opensearch()
        model_calls = self._query_model_metrics()
        sched = scheduler_stats or {
            "active_runs": 0,
            "waiting_for_slot": 0,
            "session_queued": 0,
            "queued_runs": 0,
            "active_sessions": 0,
            "max_concurrent_runs": 8,
        }
        concurrency = {
            **sched,
            "active_sse_streams": max(0, int(active_sse_streams)),
        }
        http = http_metrics or {
            "window_seconds": 300,
            "inflight_requests": 0,
            "request_count": 0,
            "rpm": 0.0,
            "p50_ms": None,
            "p95_ms": None,
            "error_count": 0,
            "error_rate": 0.0,
        }
        storage = {
            **(host_storage or {"total_bytes": 0, "used_bytes": 0, "free_bytes": 0, "status": "ok"}),
            **(elastic_storage or {}),
        }
        capacity = self._compute_capacity(
            compute=compute,
            concurrency=concurrency,
            http=http,
            storage=storage,
        )
        return {
            "collected_at": datetime.now(timezone.utc),
            "compute": compute,
            "opensearch": opensearch,
            "concurrency": concurrency,
            "http": http,
            "model_calls": model_calls,
            "storage": storage,
            "capacity": capacity,
        }


_ELASTIC_WARN_CAP_MB = 256


def elastic_storage_status(base_quota_mb: int, host_storage: dict[str, Any] | None) -> dict[str, Any]:
    """Compute deployment-wide elastic quota posture from current free disk status."""
    status = (host_storage or {}).get("status", "ok")
    base_mb = max(0, int(base_quota_mb))
    if status == "critical":
        return {
            "policy": "paused_critical",
            "base_quota_mb": base_mb,
            "effective_default_quota_mb": 0,
            "uploads_paused": True,
        }
    if status == "warn":
        effective = min(base_mb, _ELASTIC_WARN_CAP_MB) if base_mb > 0 else _ELASTIC_WARN_CAP_MB
        return {
            "policy": "tightened_warn",
            "base_quota_mb": base_mb,
            "effective_default_quota_mb": effective,
            "uploads_paused": False,
        }
    return {
        "policy": "normal",
        "base_quota_mb": base_mb,
        "effective_default_quota_mb": base_mb,
        "uploads_paused": False,
    }


def resolve_elastic_storage_policy(
    *,
    base_quota_mb: int,
    host_storage: dict[str, Any] | None,
    account_quota_mb: int | None = None,
) -> dict[str, Any]:
    """Resolve effective upload byte quota for an account under current disk headroom."""
    elastic = elastic_storage_status(base_quota_mb, host_storage)
    if elastic["uploads_paused"]:
        return {
            **elastic,
            "effective_quota_mb": 0,
            "quota_bytes": 0,
        }
    if account_quota_mb is not None:
        resolved_mb = max(0, int(account_quota_mb))
        if elastic["policy"] == "tightened_warn" and (resolved_mb == 0 or resolved_mb > _ELASTIC_WARN_CAP_MB):
            resolved_mb = _ELASTIC_WARN_CAP_MB
    else:
        resolved_mb = elastic["effective_default_quota_mb"]
    return {
        **elastic,
        "effective_quota_mb": resolved_mb,
        "quota_bytes": (resolved_mb * 1024 * 1024) if resolved_mb > 0 else None,
    }

