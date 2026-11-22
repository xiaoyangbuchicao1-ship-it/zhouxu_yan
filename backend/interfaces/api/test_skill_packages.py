import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from backend.application.maritime.service import MaritimeOffice
from backend.application.maritime.test_skill_packages import skill_zip
from backend.config.settings import load_settings
from backend.interfaces.api.maritime import attach_maritime


class SkillPackageAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_import_resource_and_removal_use_authenticated_http_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = FastAPI()
            app.state.account_service = SimpleNamespace(authenticate=lambda token: SimpleNamespace(id=token))
            app.state.maritime_office = MaritimeOffice(Path(tmp) / "office.sqlite3")
            attach_maritime(app, load_settings())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                owner = {"Authorization": "Bearer owner"}
                other = {"Authorization": "Bearer other"}
                self.assertEqual((await client.post("/maritime/skills/import", files={"file": ("test.zip", skill_zip())})).status_code, 401)
                uploaded = await client.post("/maritime/skills/import", headers=owner, files={"file": ("test.zip", skill_zip())})
                self.assertEqual(uploaded.status_code, 201, uploaded.text)
                selected = uploaded.json()
                resource = await client.get(f"/maritime/skills/{selected['id']}/resources", headers=owner, params={"path": "assets/report.csv"})
                self.assertEqual(resource.status_code, 200)
                self.assertEqual(resource.json()["content"], "name,total\n")
                self.assertEqual((await client.get(f"/maritime/skills/{selected['id']}", headers=other)).status_code, 404)
                self.assertEqual((await client.delete(f"/maritime/skills/{selected['id']}", headers=other)).status_code, 404)
                self.assertEqual((await client.delete(f"/maritime/skills/{selected['id']}", headers=owner)).json(), {"status": "removed"})
                self.assertEqual((await client.get(f"/maritime/skills/{selected['id']}", headers=owner)).status_code, 404)
                # Removing an installation must not erase a completed task's
                # immutable package/version evidence.
                history = await client.get(f"/maritime/skills/{selected['id']}", headers=owner, params={"version": 1})
                self.assertEqual(history.status_code, 200)
                self.assertEqual(history.json()["package"]["sha256"], selected["package"]["sha256"])


if __name__ == "__main__":
    unittest.main()
