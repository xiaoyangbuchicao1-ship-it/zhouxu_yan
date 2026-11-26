"""Unit tests for puzzle slider captcha and account register_ip recording."""

import tempfile
import time
import unittest
from pathlib import Path

from backend.application.accounts.service import AccountService
from backend.application.admin import AdminService
from backend.application.captcha import CaptchaService, GLOBAL_CAPTCHA
from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database.connection import Database
from backend.infrastructure.database.models import AccountModel


class CaptchaAndRegisterIpTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.database = Database(f"sqlite:///{self.db_path.as_posix()}")
        self.database.initialize()
        self.auth_settings = AuthSettings(
            token_bytes=32,
            session_ttl_seconds=3600,
            password_min_length=12,
        )
        self.account_service = AccountService(
            database=self.database,
            database_settings=DatabaseSettings(url=f"sqlite:///{self.db_path.as_posix()}"),
            auth_settings=self.auth_settings,
        )
        self.admin_service = AdminService(self.database)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_captcha_challenge_generation_and_verification_lifecycle(self):
        service = CaptchaService(ttl_seconds=10, token_ttl_seconds=10)
        ch = service.create_challenge(client_ip="1.2.3.4")
        self.assertTrue(ch.challenge_id.startswith("cap_"))
        self.assertTrue(ch.bg_image.startswith("data:image/jpeg;base64,"))
        self.assertTrue(ch.piece_image.startswith("data:image/png;base64,"))
        self.assertEqual(ch.width, 360)
        self.assertEqual(ch.height, 180)
        self.assertFalse(service.validate_token("test-captcha-bypass-token", client_ip="1.2.3.4"))

        # Retrieve target_x directly from internal dict for testing exact positioning
        target_x = service._challenges[ch.challenge_id]["target_x"]

        # 1. Slide too fast (< 200ms)
        ok, token, err = service.verify_challenge(
            challenge_id=ch.challenge_id,
            x=target_x,
            duration_ms=100,
            client_ip="1.2.3.4",
        )
        self.assertFalse(ok)
        self.assertEqual(err, "slide_too_fast")

        # Create new challenge since failed attempt was popped or test position mismatch
        ch2 = service.create_challenge(client_ip="1.2.3.4")
        target_x2 = service._challenges[ch2.challenge_id]["target_x"]

        # 2. Position mismatch (> 6px)
        ok, token, err = service.verify_challenge(
            challenge_id=ch2.challenge_id,
            x=target_x2 + 20,
            duration_ms=500,
            client_ip="1.2.3.4",
        )
        self.assertFalse(ok)
        self.assertEqual(err, "position_mismatch")

        # 3. Successful slide within ±6px and >= 200ms
        ch3 = service.create_challenge(client_ip="1.2.3.4")
        target_x3 = service._challenges[ch3.challenge_id]["target_x"]
        trail = [[0, 0, 0], [target_x3 // 2, 2, 250], [target_x3 + 2, 1, 480]]
        ok, token, err = service.verify_challenge(
            challenge_id=ch3.challenge_id,
            x=target_x3 + 2,
            duration_ms=480,
            trail=trail,
            client_ip="1.2.3.4",
        )
        self.assertTrue(ok)
        self.assertIsNotNone(token)
        self.assertTrue(token.startswith("ctk_"))

        # 4. Token validation: IP mismatch fails
        self.assertFalse(service.validate_token(token, client_ip="5.6.7.8"))

        # 5. Token validation: correct IP succeeds and consumes token (single-use)
        self.assertTrue(service.validate_token(token, client_ip="1.2.3.4"))
        self.assertFalse(service.validate_token(token, client_ip="1.2.3.4"))

    def test_account_registration_stores_register_ip(self):
        result = self.account_service.register(
            email="newuser@example.com",
            password="StrongPassword1234!",
            display_name="New User",
            register_ip="203.0.113.42",
        )
        self.assertIsNotNone(result.token)
        self.assertEqual(result.account.email, "newuser@example.com")

        # Verify database record
        with self.database.session() as session:
            account = session.get(AccountModel, result.account.id)
            self.assertIsNotNone(account)
            self.assertEqual(account.register_ip, "203.0.113.42")

        # Verify admin view reflects register_ip
        accounts_page = self.admin_service.list_accounts_page()
        found = next((a for a in accounts_page["accounts"] if a["id"] == result.account.id), None)
        self.assertIsNotNone(found)
        self.assertEqual(found["register_ip"], "203.0.113.42")


if __name__ == "__main__":
    unittest.main()
