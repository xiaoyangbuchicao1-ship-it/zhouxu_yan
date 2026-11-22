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


if __name__ == "__main__":
    unittest.main()
