"""Regression suite for Step 1 root-cause recovery, mode upgrade, and cross-pass artifacts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from backend.application.accounts.service import AccountService
from backend.application.workspace.conversations import WorkspaceService
from backend.application.workspace.materials import MaterialService
from backend.bootstrap import build_harness
from backend.config.settings import load_settings
from backend.contracts.model_context import RequestBudget, RequestEstimate
from backend.contracts.modules.loop import ActionRequest, DecisionProposal, RunInput
from backend.harness.lifecycle.executor import HarnessRunExecutor
from backend.harness.runtime.loop import _step_title
from backend.infrastructure.database.connection import Database
from backend.infrastructure.execution.native import NativeProvider
from backend.infrastructure.persistence.run_store import SQLiteRunStore
from backend.interfaces.api.app import EventHub, create_app
from backend.interfaces.api.control_plane import HarnessControlService


class _ScriptedModel:
    def __init__(self, script):
        self._script = list(script)
        self.seen_inputs = []

    def context_budget(self) -> RequestBudget:
        return RequestBudget(
            model_id="test-model",
            purpose="main",
            raw_tokens=64000,
            effective_tokens=64000,
            input_limit=48000,
            output_reserved=8192,
            safety_tokens=2048,
            hard=40000,
            target=28000,
            warning=32000,
            low=24000,
            counting_method="utf8_upper_bound_estimate",
        )

    def estimate_context(self, items, tools, *, run_id: str = "") -> RequestEstimate:
        return RequestEstimate(tokens=1200, method="utf8_upper_bound_estimate")

    def allocate_tool_context(self, used_tokens: int, call_count: int, image_count: int = 0) -> dict[str, int]:
        return {"tokens": 4000, "chars": 12000}

    def supports_images(self) -> bool:
        return False

    async def decide(self, request):
        self.seen_inputs.append(request.input)
        step = self._script.pop(0)
        return step(request) if callable(step) else step


class Step1RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_can_request_work_without_file_keyword_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._make_settings(Path(tmp))
            def request_permission(request):
                names = {tool.name for tool in request.input.tools}
                self.assertIn("request_work_mode", names)
                self.assertNotIn("write", names)
                return DecisionProposal(kind="action", action_requests=[ActionRequest(
                    tool_name="request_work_mode", arguments={}, action_id="request-permission")])
            model = _ScriptedModel([request_permission])
            events = EventHub()
            execution = NativeProvider(settings)
            harness = build_harness(model=model, settings=settings, events=events,
                streams=events, execution_tools=execution)
            try:
                result = await harness.run(RunInput(text="把问候语放进我的工作区", mode="chat"))
                self.assertEqual(result.status, "waiting_user", result.error)
                self.assertEqual(result.input_requests[0]["kind"], "mode_upgrade")
                self.assertFalse(result.input_requests[0]["allow_free_text"])
                self.assertEqual(result.execution_state["tool_results"], [])
            finally:
                await execution.close_all()

    async def test_repeated_reads_do_not_remove_work_delivery_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._make_settings(Path(tmp))
            settings.runtime.no_progress_rounds = 1
            settings.context_policy.repeated_tool_limit = 1
            def read(index):
                return DecisionProposal(kind="action", action_requests=[ActionRequest(
                    tool_name="read_skill", action_id=f"skill-{index}",
                    arguments={"skill_id": "maritime-legal-research"})])
            def after_repetition(request):
                self.assertEqual(request.input.purpose, "main")
                self.assertIn("write", {tool.name for tool in request.input.tools})
                self.assertNotIn("read_skill", {tool.name for tool in request.input.tools})
                loaded = next(item.body["loaded_instructions"] for item in request.input.perceived.items
                              if item.id == "active-skill-state")
                self.assertEqual(len(loaded), 1, "repeated reads must not duplicate protected Skill text")
                return DecisionProposal(kind="answer", answer="交付验证。", complete=True)
            model = _ScriptedModel([read(1), read(2), after_repetition])
            events = EventHub()
            execution = NativeProvider(settings)
            harness = build_harness(model=model, settings=settings, events=events,
                streams=events, execution_tools=execution)
            try:
                result = await harness.run(RunInput(text="完成研究并交付文件", mode="work"))
                self.assertEqual(result.status, "succeeded", result.error)
                self.assertEqual(len(model.seen_inputs), 3)
            finally:
                await execution.close_all()

    def _make_settings(self, root: Path):
        settings = load_settings()
        settings.web.enabled = False
        settings.rag_tool.enabled = False
        settings.cases.enabled = False
        settings.research_cards.enabled = False
        settings.execution.provider = "native"
        settings.execution.workspace_root = str(root / "workspaces")
        settings.materials.storage_root = str(root / "materials")
        settings.materials.cache_root = str(root / "materials-cache")
        settings.database.url = f"sqlite:///{(root / 'zhouxu.db').as_posix()}"
        return settings

    async def test_continue_after_read_skill_does_not_shadow_run_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = self._make_settings(root)
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
                                action_id="act_skill_1",
                            )
                        ],
                    ),
                    # Simulate transient incomplete stop so Run enters failed(can_continue=True)
                    DecisionProposal(
                        kind="answer",
                        answer="",
                        finish_reason="transport_error",
                        incomplete=True,
                    ),
                    DecisionProposal(
                        kind="answer",
                        answer="结合原问题与补充要求完成的最终结论。",
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
                    session_id="conv_skill_continue",
                    conversation_id="conv_skill_continue",
                    account_id="acc_1",
                    client_request_id="req_1",
                    payload=RunInput(text="分析定期租船停租条款", mode="work"),
                )
                await control.scheduler.wait_for_idle(session_id="conv_skill_continue")
                failed_run = await control.run(
                    session_id="conv_skill_continue", run_id=submitted.run_id
                )
                self.assertEqual(failed_run.status, "failed")
                self.assertEqual(failed_run.error.code, "model_unavailable")

                await control.continue_run(
                    session_id="conv_skill_continue",
                    run_id=submitted.run_id,
                    guidance="重点补充2025年海商法修订对照",
                )
                await control.scheduler.wait_for_idle(session_id="conv_skill_continue")
                resumed_run = await control.run(
                    session_id="conv_skill_continue", run_id=submitted.run_id
                )
                self.assertEqual(resumed_run.status, "succeeded")
                self.assertEqual(resumed_run.output, "结合原问题与补充要求完成的最终结论。")
                self.assertEqual(
                    resumed_run.continuation_guidance, ["重点补充2025年海商法修订对照"]
                )
            finally:
                await execution_tools.close_all()
                store.close()

    async def test_chat_mode_file_request_upgrades_same_run_and_preserves_cross_pass_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = self._make_settings(root)
            db = Database(settings.database)
            db.initialize()
            accounts = AccountService(db, settings.database, settings.auth)
            materials = MaterialService(db, settings)
            workspace = WorkspaceService(db)
            events = EventHub()
            execution_tools = NativeProvider(settings)

            model = _ScriptedModel(
                [
                    DecisionProposal(kind="action", action_requests=[ActionRequest(
                        tool_name="request_work_mode", arguments={}, action_id="ask-work")]),
                    # Pass 1 after work-mode upgrade: write internal scratchpad + first deliverable + first part of report, then fail transiently
                    DecisionProposal(
                        kind="action",
                        action_requests=[
                            ActionRequest(
                                tool_name="write",
                                arguments={
                                    "path": "work/draft/ledger.md",
                                    "content": "# 内部台账（不应暴露为交付产物）\n",
                                },
                                action_id="act_ledger",
                            ),
                            ActionRequest(
                                tool_name="write",
                                arguments={
                                    "path": "summary.md",
                                    "content": "# 第一部分\n",
                                },
                                action_id="act_part1",
                            ),
                            ActionRequest(
                                tool_name="write",
                                arguments={
                                    "path": "papers/demo/paper.pdf",
                                    "content": "%PDF-1.4\n%typst-native\n",
                                },
                                action_id="act_pdf",
                            ),
                            ActionRequest(
                                tool_name="write",
                                arguments={
                                    "path": "papers/demo/paper.docx",
                                    "content": "docx-placeholder",
                                },
                                action_id="act_docx",
                            ),
                        ],
                    ),
                    DecisionProposal(
                        kind="answer",
                        answer="",
                        finish_reason="transport_error",
                        incomplete=True,
                    ),
                    # Pass 2 after Continue: append second part to summary.md and finish
                    DecisionProposal(
                        kind="action",
                        action_requests=[
                            ActionRequest(
                                tool_name="write",
                                arguments={
                                    "path": "summary.md",
                                    "content": "## 第二部分（分段追加）\n",
                                    "append": True,
                                },
                                action_id="act_part2",
                            )
                        ],
                    ),
                    DecisionProposal(
                        kind="answer",
                        answer="论文与摘要文件已全部生成完毕。",
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
            control = HarnessControlService(
                HarnessRunExecutor(
                    harness,
                    material_service=materials,
                    history_service=workspace,
                    execution_tools=execution_tools,
                )
            )
            app = create_app(
                control_service=control,
                account_service=accounts,
                material_service=materials,
                workspace_service=workspace,
                settings=settings,
            )
            try:
                async with app.router.lifespan_context(app):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app), base_url="http://test"
                    ) as client:
                        reg = await client.post(
                            "/auth/register",
                            json={
                                "email": "tester@example.com",
                                "password": "Password123456!",
                                "display_name": "Tester",
                            },
                        )
                        self.assertEqual(reg.status_code, 201)
                        token = reg.json()["session"]["token"]
                        headers = {"Authorization": f"Bearer {token}"}

                        conv_resp = await client.post(
                            "/workspace/conversations",
                            headers=headers,
                            json={"title": "论文测试"},
                        )
                        conv_id = conv_resp.json()["conversation"]["conversation_id"]
                        self.assertEqual(conv_resp.json()["conversation"]["mode"], "chat")

                        sub_resp = await client.post(
                            f"/workspace/conversations/{conv_id}/runs",
                            headers=headers,
                            json={
                                "conversation_id": conv_id,
                                "client_request_id": "req_paper_1",
                                "text": "请撰写出一篇跟海事领域有关的论文，并生成PDF和Word",
                            },
                        )
                        self.assertEqual(sub_resp.status_code, 202)
                        run_id = sub_resp.json()["run"]["run_id"]
                        await control.scheduler.wait_for_idle(session_id=conv_id)

                        waiting_view = (
                            await client.get(
                                f"/workspace/runs/{run_id}?conversation_id={conv_id}",
                                headers=headers,
                            )
                        ).json()["run"]
                        self.assertEqual(waiting_view["status"], "waiting_user")
                        req_id = waiting_view["pending_request_ids"][0]
                        req_detail = (
                            await client.get(
                                f"/workspace/runs/{run_id}/input-requests/{req_id}?conversation_id={conv_id}",
                                headers=headers,
                            )
                        ).json()["request"]
                        self.assertEqual(req_detail["kind"], "mode_upgrade")

                        for invalid_answer in ("", "这不是授权，只是补充一句话", None):
                            invalid = await client.post(
                                f"/workspace/runs/{run_id}/resume", headers=headers,
                                json={"conversation_id": conv_id, "request_id": req_id,
                                      "answer": invalid_answer},
                            )
                            self.assertEqual(invalid.status_code, 422)
                        unchanged = (await client.get(
                            f"/workspace/conversations/{conv_id}", headers=headers,
                        )).json()
                        self.assertEqual(unchanged["conversation"]["mode"], "chat")

                        invalid_alias = await client.post(
                            f"/workspace/conversations/{conv_id}/runs", headers=headers,
                            json={"conversation_id": conv_id, "dispatch_mode": "resume",
                                  "target_run_id": run_id, "request_id": req_id,
                                  "answer": "这不是授权"},
                        )
                        self.assertEqual(invalid_alias.status_code, 422)
                        invalid_legacy = await client.post(
                            f"/harness/runs/{run_id}/resume", headers=headers,
                            json={"session_id": conv_id, "request_id": req_id,
                                  "answer": "这不是授权"},
                        )
                        self.assertEqual(invalid_legacy.status_code, 409)

                        resume_resp = await client.post(
                            f"/workspace/runs/{run_id}/resume",
                            headers=headers,
                            json={
                                "conversation_id": conv_id,
                                "request_id": req_id,
                                "answer": "upgrade_work",
                            },
                        )
                        self.assertEqual(resume_resp.status_code, 200)
                        await control.scheduler.wait_for_idle(session_id=conv_id)

                        # Conversation mode upgraded to work, and same run_id reached failed checkpoint
                        detail_mid = (
                            await client.get(
                                f"/workspace/conversations/{conv_id}", headers=headers
                            )
                        ).json()
                        self.assertEqual(detail_mid["conversation"]["mode"], "work")
                        assistant_mid = [
                            m for m in detail_mid["messages"] if m["role"] == "assistant"
                        ][-1]
                        self.assertTrue(assistant_mid["recovery"]["can_continue"])

                        # Continue the same Run to completion
                        cont_resp = await client.post(
                            f"/workspace/runs/{run_id}/continue",
                            headers=headers,
                            json={"conversation_id": conv_id},
                        )
                        self.assertEqual(cont_resp.status_code, 200)
                        await control.scheduler.wait_for_idle(session_id=conv_id)

                        final_run = (
                            await client.get(
                                f"/workspace/runs/{run_id}?conversation_id={conv_id}",
                                headers=headers,
                            )
                        ).json()["run"]
                        self.assertEqual(final_run["status"], "succeeded")
                        names = sorted(a["name"] for a in final_run["artifacts"])
                        kinds = {a["name"]: a["kind"] for a in final_run["artifacts"]}
                        # Internal work/draft/ledger.md excluded; summary.md + paper.pdf + paper.docx all preserved!
                        self.assertEqual(names, ["paper.docx", "paper.pdf", "summary.md"])
                        self.assertEqual(kinds["paper.pdf"], "file")
                        self.assertEqual(kinds["paper.docx"], "document")
                        paper_pdf = next(a for a in final_run["artifacts"] if a["name"] == "paper.pdf")
                        paper_docx = next(a for a in final_run["artifacts"] if a["name"] == "paper.docx")
                        self.assertEqual(paper_docx["preview_artifact_id"], paper_pdf["artifact_id"])

                        # Verify append content via artifact download
                        summary_art = next(
                            a for a in final_run["artifacts"] if a["name"] == "summary.md"
                        )
                        dl = await client.get(
                            f"/workspace/runs/{run_id}/artifacts/{summary_art['artifact_id']}?conversation_id={conv_id}",
                            headers=headers,
                        )
                        self.assertEqual(dl.status_code, 200)
                        self.assertEqual(
                            dl.text, "# 第一部分\n## 第二部分（分段追加）\n"
                        )
            finally:
                await execution_tools.close_all()
                db.close()

    def test_step_title_records_distinct_identifiers(self):
        self.assertEqual(
            _step_title(
                "read_skill",
                {"skill_id": "csl-law-paper-generator", "resource": "assets/csl_template.typ"},
            ),
            "read_skill: csl-law-paper-generator#assets/csl_template.typ",
        )
        self.assertEqual(
            _step_title("information_card_read", {"card_id": "card_99"}),
            "information_card_read: card_99",
        )
        self.assertEqual(
            _step_title("write", {"path": "work/p/paper.typ", "append": True}),
            "write: work/p/paper.typ (append)",
        )
        self.assertEqual(
            _step_title("paper_compile", {"source_path": "work/p/paper.typ"}),
            "paper_compile: work/p/paper.typ",
        )


if __name__ == "__main__":
    unittest.main()
