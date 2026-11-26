from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from backend.application.accounts.service import AccountService
from backend.config.settings import AuthSettings, DatabaseSettings
from backend.infrastructure.database.connection import Database
from backend.interfaces.api.app import create_app


class AdminHttpContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = DatabaseSettings(url=f"sqlite:///{Path(self.tmp.name) / 'admin.sqlite3'}")
        self.database = Database(settings)
        self.database.initialize()
        self.accounts = AccountService(
            self.database,
            settings,
            AuthSettings(password_min_length=8),
        )
        self.admin = self.accounts.ensure_admin(
            email="admin@example.com", password="a-long-admin-password"
        )
        self.user = self.accounts.register(
            email="user@example.com", password="a-long-user-password"
        )
        self.app = create_app(account_service=self.accounts)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.database.close()
        self.tmp.cleanup()

    async def test_admin_http_journey_and_user_forbidden(self):
        login = await self.client.post(
            "/auth/admin-login",
            json={"email": "admin@example.com", "password": "a-long-admin-password"},
        )
        self.assertEqual(login.status_code, 200)
        admin_token = login.json()["session"]["token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        overview = await self.client.get("/admin/overview", headers=headers)
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["overview"]["total_accounts"], 2)

        accounts = await self.client.get("/admin/accounts", headers=headers)
        self.assertEqual(accounts.status_code, 200)
        self.assertTrue(all("password_hash" not in item for item in accounts.json()["accounts"]))

        forbidden = await self.client.get(
            "/admin/overview",
            headers={"Authorization": f"Bearer {self.user.token}"},
        )
        self.assertEqual(forbidden.status_code, 403)

        update = await self.client.patch(
            f"/admin/accounts/{self.user.account.id}",
            headers=headers,
            json={"status": "disabled"},
        )
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.json()["status"], "disabled")

        audit = await self.client.get("/admin/audit", headers=headers)
        self.assertEqual(audit.status_code, 200)
        self.assertEqual(audit.json()["events"][0]["action"], "account.status_changed")

        user_login = await self.client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "a-long-user-password"},
        )
        self.assertEqual(user_login.status_code, 403)
        self.assertEqual(user_login.json()["detail"], "account_disabled")

    async def test_accounts_support_server_filters_and_pagination(self):
        for index in range(24):
            self.accounts.register(email=f"member-{index:02d}@example.com", password="a-long-user-password")
        headers = {"Authorization": f"Bearer {(await self.client.post('/auth/admin-login', json={'email': 'admin@example.com', 'password': 'a-long-admin-password'})).json()['session']['token']}"}
        response = await self.client.get('/admin/accounts?q=member-0&status=active&role=user&page=2&page_size=3', headers=headers)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['page'], 2)
        self.assertEqual(payload['page_size'], 3)
        self.assertEqual(payload['total'], 10)
        self.assertEqual(payload['total_pages'], 4)
        self.assertEqual(len(payload['accounts']), 3)
        self.assertTrue(all('member-0' in row['email'] for row in payload['accounts']))

    async def test_account_mutation_response_is_immediately_reflectable(self):
        login = await self.client.post('/auth/admin-login', json={'email': 'admin@example.com', 'password': 'a-long-admin-password'})
        headers = {'Authorization': f"Bearer {login.json()['session']['token']}"}
        update = await self.client.patch(f'/admin/accounts/{self.user.account.id}', headers=headers, json={'role': 'admin'})
        self.assertEqual(update.status_code, 200)
        refreshed = await self.client.get('/admin/accounts?q=user@example.com', headers=headers)
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(refreshed.json()['accounts'][0]['role'], 'admin')

    async def test_admin_audit_supports_category_outcome_query_and_cursor_pagination(self):
        from backend.application.admin import AdminService

        self.accounts.ensure_admin(email='second-admin@example.com', password='a-long-admin-password')
        with self.database.session() as session:
            AdminService._add_audit(
                session,
                actor_id=self.admin.id,
                target_account_id=self.user.account.id,
                action='account.capability_changed',
                resource_type='account_capability',
                resource_id=self.user.account.id,
                outcome='succeeded',
                details={'before': {'model_policy': 'default'}, 'after': {'model_policy': 'allowlist'}},
            )
            AdminService._add_audit(
                session,
                actor_id=self.admin.id,
                target_account_id=None,
                action='model.provider_tested',
                resource_type='model_provider',
                resource_id='relay-1',
                outcome='failed',
                details={'reason': 'timeout'},
            )
            AdminService._add_audit(
                session,
                actor_id=self.admin.id,
                target_account_id=self.user.account.id,
                action='conversation.log_viewed',
                resource_type='conversation',
                resource_id='conv-9',
                outcome='succeeded',
                details={'run_count': 2},
            )
            session.commit()

        login = await self.client.post('/auth/admin-login', json={'email': 'admin@example.com', 'password': 'a-long-admin-password'})
        headers = {'Authorization': f"Bearer {login.json()['session']['token']}"}

        overview = await self.client.get('/admin/audit?limit=2', headers=headers)
        self.assertEqual(overview.status_code, 200, overview.text)
        body = overview.json()
        self.assertEqual(len(body['events']), 2)
        self.assertTrue(body['next_cursor'])
        counts = body['category_counts']
        self.assertEqual(counts['all'], sum(value for key, value in counts.items() if key not in {'all', 'operations'}))
        self.assertEqual(counts['model'], 1)
        self.assertEqual(counts['capability'], 1)
        self.assertEqual(counts['conversation'], 1)
        self.assertGreaterEqual(counts['auth'], 2)
        self.assertEqual(counts['operations'], counts['capability'] + counts['model'] + counts['account'] + counts['skill'])

        seen = [row['action'] for row in body['events']]
        cursor = body['next_cursor']
        while cursor:
            page = await self.client.get(f"/admin/audit?limit=2&cursor={cursor}", headers=headers)
            self.assertEqual(page.status_code, 200)
            seen += [row['action'] for row in page.json()['events']]
            cursor = page.json()['next_cursor']
        for action in ('account.capability_changed', 'model.provider_tested', 'conversation.log_viewed', 'auth.login'):
            self.assertIn(action, seen)

        failed = await self.client.get('/admin/audit?category=model&outcome=failed&q=relay-1', headers=headers)
        self.assertEqual(failed.status_code, 200)
        self.assertEqual([row['action'] for row in failed.json()['events']], ['model.provider_tested'])

        bad = await self.client.get('/admin/audit?category=unknown', headers=headers)
        self.assertEqual(bad.status_code, 422)

    async def test_admin_can_delete_account_with_full_cleanup_and_guards(self):
        from datetime import datetime, timezone
        from backend.infrastructure.database.models import ConversationModel, ConversationRunModel

        login = await self.client.post(
            "/auth/admin-login",
            json={"email": "admin@example.com", "password": "a-long-admin-password"},
        )
        headers = {"Authorization": f"Bearer {login.json()['session']['token']}"}

        # Self-delete blocked
        self_del = await self.client.delete(f"/admin/accounts/{self.admin.id}", headers=headers)
        self.assertEqual(self_del.status_code, 409)
        self.assertEqual(self_del.json()["detail"], "cannot_delete_self")

        # Non-admin blocked
        user_del = await self.client.delete(
            f"/admin/accounts/{self.admin.id}",
            headers={"Authorization": f"Bearer {self.user.token}"},
        )
        self.assertEqual(user_del.status_code, 403)

        # Create a conversation with an active run -> delete must be blocked with 409
        now = datetime.now(timezone.utc)
        with self.database.session() as session:
            session.add(
                ConversationModel(
                    id="conv-del-1",
                    account_id=self.user.account.id,
                    title="待删除会话",
                    mode="work",
                    pinned=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                ConversationRunModel(
                    run_id="run-del-1",
                    conversation_id="conv-del-1",
                    question="运行中",
                    status="running",
                    created_at=now,
                )
            )
            session.commit()

        blocked = await self.client.delete(
            f"/admin/accounts/{self.user.account.id}", headers=headers
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["detail"], "account_has_active_runs")

        # Mark run succeeded -> deletion now succeeds cleanly
        with self.database.session() as session:
            run = session.get(ConversationRunModel, "run-del-1")
            run.status = "succeeded"
            session.commit()

        removed = await self.client.delete(
            f"/admin/accounts/{self.user.account.id}", headers=headers
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        body = removed.json()
        self.assertTrue(body["deleted"])
        self.assertEqual(body["email"], "user@example.com")
        self.assertEqual(body["conversations_deleted"], 1)

        # Old user token & credentials are immediately dead
        me = await self.client.get(
            "/auth/me", headers={"Authorization": f"Bearer {self.user.token}"}
        )
        self.assertEqual(me.status_code, 401)
        retry_login = await self.client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "a-long-user-password"},
        )
        self.assertEqual(retry_login.status_code, 401)

        # Account gone from list, audit records account.deleted, zero FK violations
        listing = await self.client.get("/admin/accounts", headers=headers)
        self.assertEqual([a["email"] for a in listing.json()["accounts"]], ["admin@example.com"])
        audit = await self.client.get("/admin/audit?category=operations", headers=headers)
        self.assertEqual(audit.status_code, 200)
        self.assertEqual(audit.json()["events"][0]["action"], "account.deleted")
        self.assertEqual(audit.json()["events"][0]["details"]["email"], "user@example.com")
        self.assertEqual(self.database.foreign_key_violations(), [])


if __name__ == "__main__":
    unittest.main()
