import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.application.workspace.progress import project_progress


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 11, 20, tzinfo=timezone.utc)

    def event(self, seconds, stage=None, kind="progress"):
        return dict(type=kind, emitted_at=(self.start + timedelta(seconds=seconds)).isoformat(), stage=stage, label=stage)

    def test_pause_resume_excludes_wait_and_survives_reopen(self):
        events = [self.event(0, "execution:running"), self.event(1, "law_searching"), self.event(5, "execution:paused"), self.event(100, "execution:running"), self.event(101, "reasoning"), self.event(103, kind="delta"), self.event(107, "execution:succeeded"), self.event(200, "execution:snapshot")]
        stages = project_progress(SimpleNamespace(status="succeeded"), events)
        self.assertEqual([s["elapsed_ms"] for s in stages], [4000, 2000, 4000])
        self.assertFalse(any(s["active"] for s in stages))

    def test_missing_history_or_restart_does_not_invent_time(self):
        for events in ([self.event(0, "reasoning"), self.event(5, kind="delta")], [self.event(0, "execution:running"), self.event(1, "reasoning"), self.event(100, "execution:snapshot")]):
            stages = project_progress(SimpleNamespace(status="paused"), events)
            self.assertTrue(all(s["elapsed_ms"] is None for s in stages))
            self.assertFalse(any(s["active"] for s in stages))

    def test_repeated_steps_merge_and_live_time_uses_server_clock(self):
        events = [self.event(0, "execution:running"), self.event(0, "law_searching"), self.event(2, "reasoning"), self.event(3, "case_searching"), self.event(6, kind="delta")]
        stages = project_progress(SimpleNamespace(status="running"), events, self.start + timedelta(seconds=8))
        self.assertEqual([s["id"] for s in stages], ["research", "model", "writing"])
        self.assertEqual([s["elapsed_ms"] for s in stages], [5000, 1000, 2000])
        self.assertTrue(stages[-1]["active"])

    def test_model_internal_steps_have_one_plain_label_and_finalizing_is_visible(self):
        events = [
            self.event(0, "execution:running"),
            self.event(1, "memory"),
            self.event(2, "perception"),
            self.event(4, "reasoning"),
            self.event(6, "reflection"),
            self.event(7, "finalizing"),
            self.event(8, "execution:succeeded"),
        ]

        stages = project_progress(SimpleNamespace(status="succeeded"), events)

        self.assertEqual([item["id"] for item in stages], ["model", "delivery"])
        self.assertEqual([item["label"] for item in stages], ["模型处理中", "整理结果"])

    def test_research_and_file_actions_keep_user_facing_labels(self):
        events = [
            self.event(0, "execution:running"),
            self.event(1, "researching"),
            self.event(3, "file_writing"),
            self.event(5, "file_compiling"),
        ]

        stages = project_progress(SimpleNamespace(status="running"), events)

        self.assertEqual([item["id"] for item in stages], ["research", "files"])
        self.assertEqual([item["label"] for item in stages], ["查找资料", "生成文件"])

    def test_granular_pipeline_separates_synthesis_and_replaces_pending_details(self):
        events = [
            self.event(0, "execution:running"),
            {"type": "progress", "emitted_at": (datetime(2026, 4, 15, tzinfo=timezone.utc) + timedelta(seconds=1)).isoformat(), "stage": "model_processing", "label": "理解问题并规划步骤"},
            {"type": "progress", "emitted_at": (datetime(2026, 4, 15, tzinfo=timezone.utc) + timedelta(seconds=3)).isoformat(), "stage": "skill_reading", "label": "加载技能 · 海事法学论文写作"},
            {"type": "progress", "emitted_at": (datetime(2026, 4, 15, tzinfo=timezone.utc) + timedelta(seconds=4)).isoformat(), "stage": "skill_reading", "label": "已加载技能 · 海事法学论文写作"},
            {"type": "progress", "emitted_at": (datetime(2026, 4, 15, tzinfo=timezone.utc) + timedelta(seconds=5)).isoformat(), "stage": "law_searching", "label": "检索法规 · 海商法"},
            {"type": "progress", "emitted_at": (datetime(2026, 4, 15, tzinfo=timezone.utc) + timedelta(seconds=8)).isoformat(), "stage": "law_searching", "label": "检索法规 · 海商法（命中 6 条）"},
            {"type": "progress", "emitted_at": (datetime(2026, 4, 15, tzinfo=timezone.utc) + timedelta(seconds=8, microseconds=500000)).isoformat(), "stage": "law_searching", "label": "检索法规 · 海商法（命中 10 条）"},
            {"type": "progress", "emitted_at": (datetime(2026, 4, 15, tzinfo=timezone.utc) + timedelta(seconds=9)).isoformat(), "stage": "synthesizing", "label": "分析已查依据并继续（第 1 轮）"},
            {"type": "progress", "emitted_at": (datetime(2026, 4, 15, tzinfo=timezone.utc) + timedelta(seconds=15)).isoformat(), "stage": "work_package", "label": "生成结构化工作包对照表"},
            {"type": "progress", "emitted_at": (datetime(2026, 4, 15, tzinfo=timezone.utc) + timedelta(seconds=17)).isoformat(), "stage": "work_package", "label": "已保存工作包对照表"},
            {"type": "progress", "emitted_at": (datetime(2026, 4, 15, tzinfo=timezone.utc) + timedelta(seconds=18)).isoformat(), "stage": "file_writing", "label": "续写文稿 · paper.typ"},
            {"type": "progress", "emitted_at": (datetime(2026, 4, 15, tzinfo=timezone.utc) + timedelta(seconds=20)).isoformat(), "stage": "file_writing", "label": "已保存文稿 · paper.typ"},
            {"type": "progress", "emitted_at": (datetime(2026, 4, 15, tzinfo=timezone.utc) + timedelta(seconds=21)).isoformat(), "stage": "writing", "label": "撰写回答"},
            self.event(24, "execution:succeeded"),
        ]

        stages = project_progress(SimpleNamespace(status="succeeded"), events)

        by_id = {item["id"]: item for item in stages}
        self.assertEqual([item["id"] for item in stages], ["model", "skills", "research", "synthesis", "files", "writing"])
        # Round-0 planning elapsed stays strictly at 2000ms (1s -> 3s) instead of absorbing later rounds.
        self.assertEqual(by_id["model"]["elapsed_ms"], 2000)
        self.assertEqual(by_id["synthesis"]["elapsed_ms"], 6000)
        self.assertEqual(by_id["skills"]["details"], ["已加载技能 · 海事法学论文写作"])
        self.assertEqual(by_id["research"]["details"], ["检索法规 · 海商法（命中 6 条）", "检索法规 · 海商法（命中 10 条）"])
        self.assertEqual(by_id["files"]["details"], ["已保存工作包对照表", "已保存文稿 · paper.typ"])

    def test_harness_action_labels_sanitize_paths_and_report_counts(self):
        from backend.contracts.modules.loop import ActionResult
        from backend.harness.runtime.loop import _action_progress_done_label, _action_progress_label, _action_progress_stage

        self.assertEqual(_action_progress_stage("read_skill"), "skill_reading")
        self.assertEqual(_action_progress_stage("information_card_search"), "paper_searching")
        self.assertEqual(
            _action_progress_label("write", {"path": "/workspace/secret/drafts/paper.typ", "append": False}),
            "撰写文稿 · paper.typ",
        )
        self.assertNotIn("/workspace/secret", _action_progress_label("write", {"path": "/workspace/secret/paper.typ"}))
        self.assertEqual(
            _action_progress_label("bash", {"command": "unzip -p out.docx word/document.xml | head -n 5"}),
            "核验 Word 文档结构",
        )
        done = _action_progress_done_label(
            "dynamic_rag_search",
            {"query": "《海商法》第51条"},
            ActionResult(tool_name="dynamic_rag_search", action_id="a1", outcome="ok", result={"items": [{"id": "1"}, {"id": "2"}]}),
        )
        self.assertEqual(done, "检索法规 · 《海商法》第51条（命中 2 条）")


if __name__ == "__main__":
    unittest.main()
