"""Regression tests for Chat/Work Skill split, relaxed compiler warnings, dynamic defaults, and clean projections."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from pydantic import SecretStr

from backend.application.accounts.service import AccountService
from backend.application.admin_operations import AdminOperationsService
from backend.application.maritime.service import MaritimeOffice
from backend.application.workspace.conversations import WorkspaceService
from backend.config.settings import AuthSettings, DatabaseSettings, load_settings
from backend.contracts.model_profiles import ModelCapabilityFlags, ModelProfile
from backend.contracts.modules.loop import ActionRequest, DecisionProposal, RunInput
from backend.contracts.schemas.core import ModuleRequest
from backend.bootstrap import build_harness
from backend.harness.lifecycle.executor import HarnessRunExecutor
from backend.harness.lifecycle.test_step1_recovery import _ScriptedModel
from backend.infrastructure.database.connection import Database
from backend.infrastructure.execution.native import NativeProvider
from backend.infrastructure.model.directory import RuntimeModelRevision
from backend.infrastructure.model.registry import ModelRegistry
from backend.infrastructure.model.router import RoutedModel
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.infrastructure.persistence.run_store import SQLiteRunStore
from backend.infrastructure.tools.skills import SkillReadTool
from backend.interfaces.api.app import EventHub, create_app
from backend.interfaces.api.control_plane import HarnessControlService
from backend.skills.registry import default_skill_registry


def _load_script_module(script_name: str, module_name: str):
    path = (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "csl-law-paper-generator"
        / "scripts"
        / script_name
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_compile_module():
    return _load_script_module("compile_paper.py", "test_compile_paper")


def _custom_zip(name: str = "demo-custom") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            f"{name}/SKILL.md",
            f"---\nname: {name}\ndescription: custom package\n---\n# {name}\nBody.\n",
        )
    return buf.getvalue()


class ChatWorkRefactorTests(unittest.IsolatedAsyncioTestCase):
    def test_compile_paper_relaxes_short_draft_tables_and_refines_terms(self):
        compile_mod = _load_compile_module()
        typst = (
            "#show: csl-paper\n"
            "= 短篇海商法评述\n"
            "本文引用《海商法》第五十一条#footnote[参见《海商法》第51条。]进行对比，并对照交叉核对。"
            "论文阐述了即使部分二手文献未取得原文，仍可就现行条文本身展开规范分析。\n"
            "#table(columns: 2, [规则], [例外])\n"
        )
        errors, warnings = compile_mod.lint_typst_source(typst)
        self.assertEqual(errors, [])
        joined = "\n".join(warnings)
        self.assertIn("建议底线", joined)
        self.assertIn("低于核心期刊常见 12 条基准", joined)
        self.assertIn("#table(", joined)

        errors2, _ = compile_mod.lint_typst_source("正文明确写了本次未取得原文，以及仅取得公开摘要。")
        self.assertTrue(any("未取得原文" in e or "仅取得公开摘要" in e for e in errors2))

        typ2docx = _load_script_module("typ_to_docx.py", "test_typ_to_docx")
        meta, blocks = typ2docx.parse_typ_paper(typst)
        self.assertIsNotNone(meta)
        table_blocks = [b for b in blocks if b[0] == "table"]
        self.assertEqual(len(table_blocks), 1)
        self.assertEqual(table_blocks[0][1]["cols"], 2)
        self.assertEqual(table_blocks[0][1]["rows"], [["规则", "例外"]])
        if typ2docx.HAS_DOCX:
            with tempfile.TemporaryDirectory() as td:
                tp = Path(td) / "t.typ"
                dp = Path(td) / "t.docx"
                tp.write_text(typst, encoding="utf-8")
                typ2docx.build_docx_from_typ(str(tp), str(dp))
                with zipfile.ZipFile(dp) as zf:
                    doc_xml = zf.read("word/document.xml").decode("utf-8")
                self.assertIn("<w:tbl", doc_xml)
                self.assertNotIn("#table(", doc_xml)

    async def test_skill_read_enforces_chat_work_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = load_settings().model_copy(deep=True)
            office = MaritimeOffice(Path(tmp) / "office.sqlite3")
            custom = office.import_skill_package("acc-1", "demo.zip", _custom_zip())
            tool = SkillReadTool(
                registry=default_skill_registry(),
                settings=settings,
                account_skill_loader=lambda acc, sid, ver=None: office.skill(acc, sid),
            )

            chat_spec = tool.spec_for_mode("chat")
            for name in ("maritime-legal-research", "maritime-case-research", "maritime-paper-research"):
                self.assertIn(f"- {name} ", chat_spec.description)
            for forbidden in ("csl-law-paper-generator",):
                self.assertNotIn(f"- {forbidden} ", chat_spec.description)
            self.assertTrue(chat_spec.read_only)

            work_spec = tool.spec_for_mode("work")
            for name in (
                "maritime-legal-research",
                "maritime-case-research",
                "maritime-paper-research",
                "csl-law-paper-generator",
            ):
                self.assertIn(f"- {name} ", work_spec.description)
            self.assertFalse(work_spec.read_only)

            # Chat mode allows maritime-paper-research directly
            ok_req = ModuleRequest(
                run_id="r-chat-ok",
                call_id="c1",
                input=ActionRequest(tool_name="read_skill", arguments={"skill_id": "maritime-paper-research"}),
                context={"mode": "chat", "account_id": "acc-1"},
            )
            ok_res = await tool.execute_context(ok_req)
            self.assertEqual(ok_res.outcome, "ok")

            # Chat mode refuses work Skill and user custom Skill package
            for denied_name in ("csl-law-paper-generator", custom["id"]):
                denied_req = ModuleRequest(
                    run_id="r-chat-deny",
                    call_id="c2",
                    input=ActionRequest(tool_name="read_skill", arguments={"skill_id": denied_name}),
                    context={"mode": "chat", "account_id": "acc-1"},
                )
                denied_res = await tool.execute_context(denied_req)
                self.assertEqual(denied_res.outcome, "error")
                err_text = (denied_res.error.message if denied_res.error else str(denied_res.result))
                self.assertIn("工作模式", err_text)

    def test_router_and_registry_prefer_active_default_profile_over_deepseek_flash(self):
        settings = load_settings().model_copy(deep=True)
        flags = ModelCapabilityFlags(
            tools="confirmed",
            thinking="confirmed",
            streaming="confirmed",
            structured_output="confirmed",
            vision="unconfirmed",
        )
        alt = RuntimeModelRevision(
            profile=ModelProfile(
                profile_id="relay:gpt-6-2",
                provider="relay",
                provider_revision_id="relay:r1",
                profile_revision_id="relay:gpt-6-2:r1",
                revision=1,
                family="gpt",
                label="GPT 6.2",
                model_id="gpt-6.2",
                protocol="openai_chat_completions",
                endpoint="https://relay.example.test/v1/chat/completions",
                context_tokens=200000,
                max_generation_tokens=16384,
                thinking_modes=["none"],
                default_thinking_mode="none",
                capabilities=flags,
                configured=True,
                available=True,
                availability_note="已配置",
                source="admin",
                checked_on="2026-11-23",
            ),
            provider_revision_id="relay:r1",
            profile_revision_id="relay:gpt-6-2:r1",
            endpoint="https://relay.example.test/v1/chat/completions",
            auth_style="bearer",
            credential_mode="env_ref",
            credential_ref="ZHOUXU_RELAY_API_KEY",
        )

        class _Directory:
            def list_current(self):
                return [alt]

            def resolve(self, profile_id, *, profile_revision_id=None):
                if profile_id == "relay:gpt-6-2":
                    return alt
                raise KeyError(profile_id)

            def credential(self, revision):
                return SecretStr("token-123")

        registry = ModelRegistry(settings, directory=_Directory())
        self.assertEqual(registry.default_profile_id(), "relay:gpt-6-2")

        class _Adapter:
            def __init__(self, cfg, *_):
                self.cfg = cfg

            def close_run(self, run_id):
                pass

            async def aclose(self):
                pass

        with patch("backend.infrastructure.model.router.adapter_for", side_effect=_Adapter):
            router = RoutedModel(settings, MemoryEventSink(), registry=registry)
            router.bind_run("run-default", None)
            self.assertEqual(router._run_profiles["run-default"], "relay:gpt-6-2")

    async def test_project_view_excludes_archived_and_admin_operations_clean_projections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_settings = DatabaseSettings(url=f"sqlite:///{root / 'ops.sqlite3'}")
            db = Database(db_settings)
            db.initialize()
            try:
                accounts = AccountService(db, db_settings, AuthSettings(password_min_length=8))
                admin = accounts.ensure_admin(email="admin@example.com", password="a-long-admin-password")
                user = accounts.register(email="user@example.com", password="a-long-user-password")
                admin_token = accounts.login(email="admin@example.com", password="a-long-admin-password").token
                user_token = accounts.login(email="user@example.com", password="a-long-user-password").token

                settings = load_settings().model_copy(deep=True)
                settings.database = db_settings
                settings.application["office_store_path"] = str(root / "office.sqlite3")
                app = create_app(account_service=accounts, settings=settings)

                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://test",
                ) as client:
                    u_headers = {"Authorization": f"Bearer {user_token}"}
                    a_headers = {"Authorization": f"Bearer {admin_token}"}

                    proj_resp = await client.post("/workspace/projects", headers=u_headers, json={"name": "碰撞案"})
                    self.assertEqual(proj_resp.status_code, 201, proj_resp.text)
                    proj_data = proj_resp.json()
                    pid = proj_data["project_id"]
                    c1 = (
                        await client.post(
                            "/workspace/conversations",
                            headers=u_headers,
                            json={"title": "保留会话", "mode": "chat", "project_id": pid},
                        )
                    ).json()["conversation"]["conversation_id"]
                    c2 = (
                        await client.post(
                            "/workspace/conversations",
                            headers=u_headers,
                            json={"title": "将归档会话", "mode": "chat", "project_id": pid},
                        )
                    ).json()["conversation"]["conversation_id"]
                    arch = await client.post(f"/workspace/conversations/{c2}/archive", headers=u_headers)
                    self.assertEqual(arch.status_code, 204)

                    listed_payload = (await client.get("/workspace/projects", headers=u_headers)).json()
                    listed = listed_payload["projects"] if isinstance(listed_payload, dict) else listed_payload
                    target_proj = next(p for p in listed if p["project_id"] == pid)
                    self.assertEqual(target_proj["conversation_count"], 1)
                    convs = (await client.get("/workspace/conversations", headers=u_headers)).json()["conversations"]
                    self.assertEqual([c["conversation_id"] for c in convs if c.get("project_id") == pid], [c1])

                    skills_res = await client.get("/admin/skills", headers=a_headers)
                    self.assertEqual(skills_res.status_code, 200)
                    rows = skills_res.json()["skills"]
                    self.assertTrue(all("（" not in r["name"] for r in rows))
                    by_id = {r["id"]: r for r in rows}
                    self.assertEqual(by_id["maritime-paper-research"]["mode_scope"], "chat")
                    self.assertEqual(by_id["csl-law-paper-generator"]["mode_scope"], "work")
                    self.assertNotIn("claim-calculation", by_id)
                    self.assertNotIn("law-research", by_id)
            finally:
                db.close()

    def test_resource_health_reports_papers_from_opensearch(self):
        settings = load_settings().model_copy(deep=True)
        with tempfile.TemporaryDirectory() as tmp:
            db_settings = DatabaseSettings(url=f"sqlite:///{Path(tmp) / 'op.sqlite3'}")
            db = Database(db_settings)
            db.initialize()
            try:
                fake_corpus = SimpleNamespace(
                    status=lambda: {"indexed": True, "counts": {"laws": 100, "cases": 200}},
                    opensearch_status=lambda: {
                        "configured": True,
                        "reachable": True,
                        "counts": {"laws": 4812, "cases": 29183, "papers": 39022},
                        "errors": {},
                    },
                )
                service = AdminOperationsService(database=db, settings=settings)
                with patch("backend.application.admin_operations.MaritimeCorpus", return_value=fake_corpus):
                    health = service.resource_health()
                papers = health["sources"]["papers"]
                self.assertEqual(papers["status"], "ready")
                self.assertEqual(papers["count"], 39022)
            finally:
                db.close()

    async def test_chat_mode_read_skill_does_not_pause_for_work_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = load_settings().model_copy(deep=True)
            settings.execution.workspace_root = str(root / "workspaces")
            settings.application["run_store_path"] = str(root / "runs.sqlite3")
            events = EventHub()
            execution_tools = NativeProvider(settings)
            model = _ScriptedModel(
                [
                    DecisionProposal(
                        kind="action",
                        action_requests=[
                            ActionRequest(
                                tool_name="read_skill",
                                arguments={"skill_id": "maritime-legal-research"},
                                action_id="act-read-chat-skill",
                            )
                        ],
                    ),
                    DecisionProposal(
                        kind="answer",
                        answer="《海商法》第二十二条规定船员工资等海事请求具有船舶优先权。",
                        complete=True,
                    ),
                ]
            )
            harness = build_harness(
                model=model,
                settings=settings,
                events=events,
                streams=events,
                execution_tools=execution_tools,
            )
            store = SQLiteRunStore(root / "runs.sqlite3")
            control = HarnessControlService(
                HarnessRunExecutor(harness, execution_tools=execution_tools),
                store=store,
            )
            try:
                _, submitted = await control.submit(
                    session_id="conv-chat-skill",
                    conversation_id="conv-chat-skill",
                    account_id="acc-chat",
                    client_request_id="req-chat-skill-1",
                    payload=RunInput(
                        text="检索《海商法》第22条",
                        mode="chat",
                        facts={
                            "skill_id": "maritime-legal-research",
                            "skill_requires_work_mode": False,
                        },
                    ),
                )
                await control.scheduler.wait_for_idle(session_id="conv-chat-skill")
                finished = await control.run(
                    session_id="conv-chat-skill", run_id=submitted.run_id
                )
                self.assertEqual(finished.status, "succeeded")
                self.assertIn("第二十二条", finished.output or "")
                self.assertEqual(finished.pending_request_ids, [])
            finally:
                await execution_tools.close_all()
                store.close()
