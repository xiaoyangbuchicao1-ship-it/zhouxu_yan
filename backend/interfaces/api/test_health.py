"""The public probe must detect the SQLite failure that broke history loading."""

import sqlite3
import unittest
from unittest.mock import Mock

import httpx

from backend.interfaces.api.app import create_app


class StorageHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_reports_readable_runtime_store(self):
        app = create_app()
        store = Mock()
        store.visible.return_value = ("", 0)
        app.state.run_store = store
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        store.visible.assert_called_once_with("__health_probe__")

    async def test_probe_rejects_unreadable_runtime_store(self):
        app = create_app()
        app.state.run_store = Mock()
        app.state.run_store.visible.side_effect = sqlite3.OperationalError(
            "unable to open database file"
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "runtime_storage_unavailable"})


if __name__ == "__main__":
    unittest.main()
