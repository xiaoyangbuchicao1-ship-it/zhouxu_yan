from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
import unittest
import zipfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
from cryptography.fernet import Fernet

from backend.application.accounts.service import AccountService
from backend.application.maintenance import RuntimeMaintenanceService
from backend.application.ops_settings import OpsSettingsService
from backend.application.workspace.conversations import WorkspaceService
from backend.application.workspace.materials import MaterialService, MaterialServiceError
from backend.config.settings import AuthSettings, DatabaseSettings, load_settings
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import (
    BusinessAuditModel,
    ConversationModel,
    EmailVerificationCodeModel,
    SessionModel,
)
from backend.infrastructure.email.resend import EmailSendResult, send_resend_email
from backend.interfaces.api.app import create_app


class _FakeResendHandler(BaseHTTPRequestHandler):
    sent_payloads: list[dict] = []
    next_status: int = 200

    def log_message(self, format, *args):  # noqa: A002
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        type(self).sent_payloads.append(
            {"auth": self.headers.get("Authorization"), "body": body}
        )
        status = type(self).next_status
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if 200 <= status < 300:
            self.wfile.write(json.dumps({"id": f"re_msg_{len(type(self).sent_payloads)}"}).encode())
        else:
            self.wfile.write(json.dumps({"message": "simulated upstream error"}).encode())


class OpsMaintenanceAndEmailTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.prev_key = os.environ.get("ZHOUXU_ADMIN_CREDENTIAL_KEY")
        self.prev_endpoint = os.environ.get("ZHOUXU_RESEND_ENDPOINT")
        self.prev_captcha = os.environ.get("ZHOUXU_CAPTCHA_ENABLED")
        os.environ["ZHOUXU_CAPTCHA_ENABLED"] = "false"
        os.environ["ZHOUXU_ADMIN_CREDENTIAL_KEY"] = Fernet.generate_key().decode("ascii")

        _FakeResendHandler.sent_payloads = []
        _FakeResendHandler.next_status = 200
        self.httpd = HTTPServer(("127.0.0.1", 0), _FakeResendHandler)
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()
        os.environ["ZHOUXU_RESEND_ENDPOINT"] = f"http://127.0.0.1:{self.httpd.server_port}/emails"

        self.db_path = self.root / "zhouxu.db"
        self.runtime_dir = self.root / "runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_root = self.root / "workspaces"
        self.mat_storage = self.root / "materials_storage"
        self.mat_cache = self.root / "materials_cache"

        db_settings = DatabaseSettings(url=f"sqlite:///{self.db_path}")
        self.database = Database(db_settings)
        self.database.initialize()

        self.accounts = AccountService(
            self.database, db_settings, AuthSettings(password_min_length=12)
        )
        self.accounts.ensure_admin(
            email="admin@example.com",
            password="admin-password-1234",
            display_name="系统管理员",
        )
        self.admin = self.accounts.admin_login(
            email="admin@example.com",
            password="admin-password-1234",
        )
        self.user = self.accounts.register(
            email="user@example.com",
            password="user-password-1234",
            display_name="海事研究员",
        )

        settings = load_settings()
        settings = settings.model_copy(
            update={
                "execution": settings.execution.model_copy(
                    update={"workspace_root": str(self.workspace_root)}
                ),
                "materials": settings.materials.model_copy(
                    update={
                        "storage_root": str(self.mat_storage),
                        "cache_root": str(self.mat_cache),
                    }
                ),
            }
        )
        self.settings = settings
        self.materials = MaterialService(self.database, settings)
        self.maintenance = RuntimeMaintenanceService(
            database=self.database,
            settings=settings,
            database_path=self.db_path,
            runtime_dir=self.runtime_dir,
        )
        self.app = create_app(
            settings=settings,
            account_service=self.accounts,
            material_service=self.materials,
            control_service=SimpleNamespace(list_runs=lambda **_k: []),
        )
        self.app.state.maintenance_service = self.maintenance
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.database.close()
        if self.prev_key is None:
            os.environ.pop("ZHOUXU_ADMIN_CREDENTIAL_KEY", None)
        else:
            os.environ["ZHOUXU_ADMIN_CREDENTIAL_KEY"] = self.prev_key
        if self.prev_endpoint is None:
            os.environ.pop("ZHOUXU_RESEND_ENDPOINT", None)
        else:
            os.environ["ZHOUXU_RESEND_ENDPOINT"] = self.prev_endpoint
        if self.prev_captcha is None:
            os.environ.pop("ZHOUXU_CAPTCHA_ENABLED", None)
        else:
            os.environ["ZHOUXU_CAPTCHA_ENABLED"] = self.prev_captcha
        self.tmp.cleanup()

    def _admin_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.admin.token}"}

    def _user_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.user.token}"}

    @staticmethod
    def _extract_code(html_body: str) -> str:
        match = re.search(r">(\d{6})<", html_body)
        assert match is not None, f"6-digit code not found in {html_body!r}"
        return match.group(1)

    async def test_resend_transport_handles_success_and_auth_error(self):
        async def ok_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": "re_mock_123"})

        res = await send_resend_email(
            api_key="re_secret_123",
            from_email="noreply@example.com",
            from_name="舟序",
            to_email="dest@example.com",
            subject="测试",
            html_body="<p>hello</p>",
            text_body="hello",
            transport=httpx.MockTransport(ok_handler),
        )
        self.assertEqual(res, EmailSendResult(ok=True, message_id="re_mock_123"))

        async def fail_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "invalid api key"})

        res_fail = await send_resend_email(
            api_key="re_bad",
            from_email="noreply@example.com",
            from_name="舟序",
            to_email="dest@example.com",
            subject="测试",
            html_body="<p>hello</p>",
            text_body="hello",
            transport=httpx.MockTransport(fail_handler),
        )
        self.assertFalse(res_fail.ok)
        self.assertEqual(res_fail.error_code, "email_auth_failed")
        self.assertEqual(res_fail.detail, "invalid api key")

    async def test_ops_settings_email_verification_reset_and_rate_limits(self):
        # Cannot enable verification before Resend key + from_email are configured.
        fail_enable = await self.client.patch(
            "/admin/ops/settings",
            headers=self._admin_headers(),
            json={"email_verification_enabled": True},
        )
        self.assertEqual(fail_enable.status_code, 422)
        self.assertEqual(fail_enable.json()["detail"], "email_provider_unconfigured")

        # Configure Resend + enable verification.
        saved = await self.client.patch(
            "/admin/ops/settings",
            headers=self._admin_headers(),
            json={
                "resend_api_key": "re_test_99887766",
                "resend_from_email": "noreply@zhouxu.test",
                "resend_from_name": "舟序验证",
                "email_verification_enabled": True,
                "allow_password_reset": True,
                "material_quota_mb": 256,
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        body = saved.json()
        self.assertTrue(body["email_ready"])
        self.assertTrue(body["email_verification_enabled"])
        self.assertEqual(body["resend_key_source"], "encrypted_db")
        self.assertNotIn("re_test_99887766", json.dumps(body))

        pub = await self.client.get("/auth/config")
        self.assertEqual(pub.status_code, 200)
        self.assertEqual(
            pub.json(),
            {
                "email_verification_enabled": True,
                "allow_password_reset": True,
                "captcha_enabled": False,
            },
        )

        # Admin email test endpoint works and writes audit.
        test_mail = await self.client.post(
            "/admin/ops/email-test",
            headers=self._admin_headers(),
            json={"to_email": "check@zhouxu.test"},
        )
        self.assertEqual(test_mail.status_code, 200, test_mail.text)
        self.assertTrue(test_mail.json()["ok"])
        self.assertEqual(test_mail.json()["message_id"], "re_msg_1")

        # Upstream send failure cancels issued code so user is not cooldown-blocked.
        _FakeResendHandler.next_status = 500
        failed_send = await self.client.post(
            "/auth/send-code",
            json={"email": "newbie@zhouxu.test", "purpose": "register"},
        )
        self.assertEqual(failed_send.status_code, 502)

        _FakeResendHandler.next_status = 200
        sent = await self.client.post(
            "/auth/send-code",
            json={"email": "newbie@zhouxu.test", "purpose": "register"},
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        code = self._extract_code(_FakeResendHandler.sent_payloads[-1]["body"]["html"])

        # Immediate resend hits 429 cooldown.
        cooldown = await self.client.post(
            "/auth/send-code",
            json={"email": "newbie@zhouxu.test", "purpose": "register"},
        )
        self.assertEqual(cooldown.status_code, 429)
        self.assertEqual(cooldown.json()["detail"], "verification_cooldown")

        # Registration without code fails; wrong code 5 times exhausts attempts.
        no_code = await self.client.post(
            "/auth/register",
            json={"email": "newbie@zhouxu.test", "password": "strong-password-1234"},
        )
        self.assertEqual(no_code.status_code, 400)

        for i in range(5):
            wrong = await self.client.post(
                "/auth/register",
                json={
                    "email": "newbie@zhouxu.test",
                    "password": "strong-password-1234",
                    "verification_code": "000000" if code != "000000" else "999999",
                },
            )
            self.assertEqual(wrong.status_code, 400)
            self.assertEqual(wrong.json()["detail"], "verification_code_invalid")

        exhausted = await self.client.post(
            "/auth/register",
            json={
                "email": "newbie@zhouxu.test",
                "password": "strong-password-1234",
                "verification_code": code,
            },
        )
        self.assertEqual(exhausted.status_code, 400)
        self.assertEqual(exhausted.json()["detail"], "verification_code_exhausted")

        # Re-issue a fresh code (bypass cooldown timestamp in DB) and register cleanly.
        with self.database.session() as session:
            for row in session.query(EmailVerificationCodeModel).all():
                row.created_at = row.created_at - timedelta(minutes=2)
            session.commit()

        sent2 = await self.client.post(
            "/auth/send-code",
            json={"email": "newbie@zhouxu.test", "purpose": "register"},
        )
        self.assertEqual(sent2.status_code, 200)
        code2 = self._extract_code(_FakeResendHandler.sent_payloads[-1]["body"]["html"])

        reg_ok = await self.client.post(
            "/auth/register",
            json={
                "email": "newbie@zhouxu.test",
                "password": "strong-password-1234",
                "display_name": "新同学",
                "verification_code": code2,
            },
        )
        self.assertEqual(reg_ok.status_code, 201, reg_ok.text)
        old_token = reg_ok.json()["session"]["token"]

        # Password reset revokes previous token and issues a working new session.
        reset_send = await self.client.post(
            "/auth/send-code",
            json={"email": "newbie@zhouxu.test", "purpose": "reset_password"},
        )
        self.assertEqual(reset_send.status_code, 200, reset_send.text)
        reset_code = self._extract_code(_FakeResendHandler.sent_payloads[-1]["body"]["html"])

        reset_done = await self.client.post(
            "/auth/reset-password",
            json={
                "email": "newbie@zhouxu.test",
                "verification_code": reset_code,
                "new_password": "newer-password-5678",
            },
        )
        self.assertEqual(reset_done.status_code, 200, reset_done.text)
        me_old = await self.client.get(
            "/auth/me", headers={"Authorization": f"Bearer {old_token}"}
        )
        self.assertEqual(me_old.status_code, 401)
        me_new = await self.client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {reset_done.json()['session']['token']}"},
        )
        self.assertEqual(me_new.status_code, 200)

    async def test_material_quota_admin_preview_download_and_artifact_download(self):
        # Enforce strict quota on direct upload.
        with self.assertRaises(MaterialServiceError) as ctx:
            self.materials.upload(
                account_id=self.user.account.id,
                session_id="sess_quota_test",
                filename="too-big.txt",
                media_type="text/plain",
                content=b"a" * 64,
                quota_bytes=16,
            )
        self.assertEqual(ctx.exception.code, "material_quota_exceeded")

        # Normal HTTP upload succeeds and admin can view passages + download file.
        upload_res = await self.client.post(
            "/workspace/materials",
            headers=self._user_headers(),
            files={"file": ("charterparty.txt", "第一条 装卸时间计算方式\n共 5 个晴天工作日。".encode("utf-8"), "text/plain")},
        )
        self.assertEqual(upload_res.status_code, 201, upload_res.text)
        material_id = upload_res.json()["material"]["material_id"]

        # Non-admin denied.
        forbidden = await self.client.get(
            f"/admin/materials/{material_id}/download",
            headers=self._user_headers(),
        )
        self.assertEqual(forbidden.status_code, 403)

        passages = await self.client.get(
            f"/admin/materials/{material_id}/passages",
            headers=self._admin_headers(),
        )
        self.assertEqual(passages.status_code, 200, passages.text)
        self.assertIn("装卸时间", passages.json()["chunks"][0]["text"])

        downloaded = await self.client.get(
            f"/admin/materials/{material_id}/download",
            headers=self._admin_headers(),
        )
        self.assertEqual(downloaded.status_code, 200)
        self.assertIn("共 5 个晴天工作日", downloaded.content.decode("utf-8"))

        # Seed a conversation run with a deliverable PDF artifact and verify admin download.
        ws = WorkspaceService(self.database)
        conv = ws.create(account_id=self.user.account.id, title="租约报告", mode="work")
        conv_id = conv.id
        run_id = "run_deliverable_1"
        artifact_id = "art_pdf_1"
        pdf_bytes = b"%PDF-1.4 sample maritime deliverable bytes"
        pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
        conv_dir = self.workspace_root / self.user.account.id / conv_id
        deliver_file = conv_dir / "output" / "brief.pdf"
        deliver_file.parent.mkdir(parents=True, exist_ok=True)
        deliver_file.write_bytes(pdf_bytes)

        ws.record_run(
            account_id=self.user.account.id,
            conversation_id=conv_id,
            run_id=run_id,
            question="生成报告",
            answer="已完成",
            status="succeeded",
            progress={
                "artifacts": [
                    {
                        "artifact_id": artifact_id,
                        "kind": "document",
                        "name": "租约报告.pdf",
                        "media_type": "application/pdf",
                        "size_bytes": len(pdf_bytes),
                        "relative_path": "output/brief.pdf",
                        "sha256": pdf_sha,
                    }
                ]
            },
        )

        art_res = await self.client.get(
            f"/admin/accounts/{self.user.account.id}/conversations/{conv_id}/runs/{run_id}/artifacts/{artifact_id}",
            headers=self._admin_headers(),
        )
        self.assertEqual(art_res.status_code, 200, art_res.text)
        self.assertEqual(art_res.content, pdf_bytes)

        with self.database.session() as session:
            actions = {
                row.action for row in session.query(BusinessAuditModel).all()
            }
        self.assertTrue({"material.viewed", "material.downloaded", "artifact.downloaded"}.issubset(actions))

    async def test_safe_cleanup_and_hot_backup_download(self):
        # Create active conversation + active deliverable + active material + scratch + orphans.
        ws = WorkspaceService(self.database)
        conv = ws.create(account_id=self.user.account.id, title="活跃工作会话", mode="work")
        conv_id = conv.id
        live_dir = self.workspace_root / self.user.account.id / conv_id
        live_deliver = live_dir / "deliverable.docx"
        live_scratch = live_dir / ".zhouxu-render" / "tmp"
        orphan_ws = self.workspace_root / self.user.account.id / "conv_deleted_orphan"
        orphan_mat = self.mat_storage / self.user.account.id / "mat_deleted_orphan"

        live_dir.mkdir(parents=True, exist_ok=True)
        live_deliver.write_bytes(b"real docx deliverable")
        live_scratch.mkdir(parents=True, exist_ok=True)
        (live_scratch / "page.pdf").write_bytes(b"scratch render")
        orphan_ws.mkdir(parents=True, exist_ok=True)
        (orphan_ws / "temp.txt").write_bytes(b"orphan workspace")
        orphan_mat.mkdir(parents=True, exist_ok=True)
        (orphan_mat / "dead.bin").write_bytes(b"orphan material")

        # Age target dirs past 1 hour so safe-age filter selects them.
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=3)).timestamp()
        for path in (live_dir / ".zhouxu-render", orphan_ws, orphan_mat):
            os.utime(path, (old_ts, old_ts))

        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            session.add(
                SessionModel(
                    id="expired_sess_1",
                    account_id=self.user.account.id,
                    token_hash="expired_hash_1",
                    created_at=now - timedelta(days=30),
                    last_seen_at=now - timedelta(days=20),
                    expires_at=now - timedelta(days=10),
                )
            )
            session.commit()

        # Also create an extra runtime SQLite store to confirm hot backup captures both DBs.
        runs_db = self.runtime_dir / "runs.sqlite3"
        with closing(sqlite3.connect(runs_db)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO runs (id) VALUES ('r1')")
            conn.commit()

        data_res = await self.client.get("/admin/data", headers=self._admin_headers())
        self.assertEqual(data_res.status_code, 200, data_res.text)
        preview = data_res.json()["cleanup_preview"]
        self.assertGreater(preview["reclaimable_bytes"], 0)
        self.assertGreater(data_res.json()["host_storage"]["free_bytes"], 0)

        clean_res = await self.client.post("/admin/data/cleanup", headers=self._admin_headers())
        self.assertEqual(clean_res.status_code, 200, clean_res.text)
        self.assertGreaterEqual(clean_res.json()["cleaned_dirs"], 3)
        self.assertGreaterEqual(clean_res.json()["cleaned_rows"], 1)

        # Verify safety invariants: scratch & orphans gone, active deliverable & live DB rows intact.
        self.assertFalse((live_dir / ".zhouxu-render").exists())
        self.assertFalse(orphan_ws.exists())
        self.assertFalse(orphan_mat.exists())
        self.assertTrue(live_deliver.is_file())
        with self.database.session() as session:
            self.assertIsNotNone(session.get(ConversationModel, conv_id))
            self.assertIsNone(session.get(SessionModel, "expired_sess_1"))

        # Hot backup + zip download + integrity verification.
        backup_res = await self.client.post("/admin/data/backups", headers=self._admin_headers())
        self.assertEqual(backup_res.status_code, 200, backup_res.text)
        zip_name = backup_res.json()["name"]

        zip_dl = await self.client.get(
            f"/admin/data/backups/{zip_name}/download",
            headers=self._admin_headers(),
        )
        self.assertEqual(zip_dl.status_code, 200)
        dl_zip = self.root / "downloaded.zip"
        dl_zip.write_bytes(zip_dl.content)

        with zipfile.ZipFile(dl_zip, "r") as zf:
            names = set(zf.namelist())
            self.assertTrue({"zhouxu.db", "runs.sqlite3", "manifest.json"}.issubset(names))
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            self.assertIn("zhouxu.db", manifest["databases"])
            extracted = self.root / "verify_zhouxu.db"
            extracted.write_bytes(zf.read("zhouxu.db"))
            with closing(sqlite3.connect(extracted)) as conn:
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    async def test_anti_abuse_global_email_cap_xff_hardening_and_login_throttle(self):
        # Configure Resend + enable email verification via backend .env fallback style.
        await self.client.patch(
            "/admin/ops/settings",
            headers=self._admin_headers(),
            json={
                "resend_api_key": "re_test_99887766",
                "resend_from_email": "noreply@zhouxu.test",
                "email_verification_enabled": True,
            },
        )
        prev_cap = os.environ.get("ZHOUXU_EMAIL_MAX_PER_HOUR")
        os.environ["ZHOUXU_EMAIL_MAX_PER_HOUR"] = "2"
        try:
            # Two sends from completely different spoofed left-most XFF IPs & emails hit the global cap of 2.
            for idx in range(2):
                res = await self.client.post(
                    "/auth/send-code",
                    headers={"X-Real-IP": f"10.0.0.{idx + 1}", "X-Forwarded-For": f"203.0.113.{idx + 1}, 10.0.0.{idx + 1}"},
                    json={"email": f"bot{idx}@zhouxu.test", "purpose": "register"},
                )
                self.assertEqual(res.status_code, 200, res.text)
            blocked = await self.client.post(
                "/auth/send-code",
                headers={"X-Real-IP": "10.0.0.99", "X-Forwarded-For": "198.51.100.77, 10.0.0.99"},
                json={"email": "bot99@zhouxu.test", "purpose": "register"},
            )
            self.assertEqual(blocked.status_code, 429)
            self.assertEqual(blocked.json()["detail"], "verification_rate_limited")
        finally:
            if prev_cap is None:
                os.environ.pop("ZHOUXU_EMAIL_MAX_PER_HOUR", None)
            else:
                os.environ["ZHOUXU_EMAIL_MAX_PER_HOUR"] = prev_cap

        # Repeated wrong logins trigger brute-force 429 lockout even if left-most XFF is spoofed.
        for _ in range(12):
            fail_login = await self.client.post(
                "/auth/login",
                headers={"X-Real-IP": "192.0.2.55", "X-Forwarded-For": "1.2.3.4, 192.0.2.55"},
                json={"email": "user@example.com", "password": "wrong-password-attempt"},
            )
            self.assertEqual(fail_login.status_code, 401)
        locked = await self.client.post(
            "/auth/login",
            headers={"X-Real-IP": "192.0.2.55", "X-Forwarded-For": "9.9.9.9, 192.0.2.55"},
            json={"email": "user@example.com", "password": "user-password-1234"},
        )
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(locked.json()["detail"], "auth_rate_limited")
