import unittest

from backend.harness.runtime.loop import _delivery_tool_gaps


def _ok(tool: str) -> dict:
    return {"tool_name": tool, "outcome": "ok"}


class DeliveryGateTest(unittest.TestCase):
    def test_paper_skill_needs_write_and_a_successful_compile(self):
        skill = "csl-law-paper-generator"
        self.assertEqual(_delivery_tool_gaps(skill, [], []), ["write", "paper_compile"])
        self.assertEqual(_delivery_tool_gaps(skill, [], [_ok("write")]), ["paper_compile"])
        failed_compile = {"tool_name": "paper_compile", "outcome": "error"}
        self.assertEqual(
            _delivery_tool_gaps(skill, [], [_ok("write"), failed_compile]),
            ["paper_compile"],
        )
        self.assertEqual(
            _delivery_tool_gaps(skill, [], [_ok("write"), _ok("paper_compile")]), []
        )

    def test_loaded_skill_counts_even_without_an_explicit_selection(self):
        gaps = _delivery_tool_gaps(
            None, [{"skill_id": "csl-law-paper-generator"}], [_ok("write")]
        )
        self.assertEqual(gaps, ["paper_compile"])

    def test_skills_without_required_tools_never_block(self):
        self.assertEqual(_delivery_tool_gaps("maritime-legal-research", [], []), [])
        self.assertEqual(_delivery_tool_gaps("no-such-skill", [], []), [])
        self.assertEqual(_delivery_tool_gaps(None, [], []), [])


if __name__ == "__main__":
    unittest.main()
