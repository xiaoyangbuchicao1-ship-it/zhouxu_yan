import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from backend.application.maritime.service import MaritimeError, MaritimeOffice


def skill_zip(*, traversal: bool = False) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        prefix = "expense-skill/"
        archive.writestr(
            prefix + "SKILL.md",
            "---\nname: expense-skill\ndescription: 汇总费用并生成表格\ncompatibility: Python 3.11\n---\n\n按规则汇总材料。\n",
        )
        archive.writestr(prefix + "scripts/sum.py", "print('ok')\n")
        archive.writestr(prefix + "references/rules.md", "合计前先检查币种。\n")
        archive.writestr(prefix + "assets/report.csv", "name,total\n")
        if traversal:
            archive.writestr("../outside.txt", "must not be written")
    return stream.getvalue()


class SkillPackageTests(unittest.TestCase):
    def test_import_preserves_manifest_and_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            office = MaritimeOffice(Path(tmp) / "office.sqlite3")
            imported = office.import_skill_package("account-a", "expense.zip", skill_zip())

            self.assertEqual(imported["name"], "expense-skill")
            self.assertEqual(imported["summary"], "汇总费用并生成表格")
            self.assertEqual(imported["package"]["name"], "expense.zip")
            self.assertEqual(
                [item["path"] for item in imported["package"]["files"]],
                [
                    "SKILL.md",
                    "assets/report.csv",
                    "references/rules.md",
                    "scripts/sum.py",
                ],
            )
            resource = office.skill_resource(
                "account-a", imported["id"], "references/rules.md"
            )
            self.assertEqual(resource["content"], "合计前先检查币种。\n")
            self.assertEqual(resource["size_bytes"], len("合计前先检查币种。\n".encode()))

    def test_package_rejects_traversal_without_writing_outside_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            office = MaritimeOffice(root / "office.sqlite3")
            with self.assertRaises(MaritimeError) as error:
                office.import_skill_package("account-a", "bad.zip", skill_zip(traversal=True))
            self.assertIn("技能包路径不安全", str(error.exception))
            self.assertFalse((root / "outside.txt").exists())

    def test_package_is_private_and_missing_resource_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            office = MaritimeOffice(Path(tmp) / "office.sqlite3")
            imported = office.import_skill_package("account-a", "one.zip", skill_zip())
            with self.assertRaises(MaritimeError) as error:
                office.skill("account-b", imported["id"])
            self.assertEqual(error.exception.status, 404)
            with self.assertRaises(MaritimeError) as error:
                office.skill_resource("account-a", imported["id"], "missing.md")
            self.assertEqual(error.exception.status, 404)

    def test_invalid_frontmatter_does_not_create_partial_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            office = MaritimeOffice(Path(tmp) / "office.sqlite3")
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("SKILL.md", "not a skill package")
            with self.assertRaises(MaritimeError) as error:
                office.import_skill_package("account-a", "bad.zip", stream.getvalue())
            self.assertIn("SKILL.md", str(error.exception))
            self.assertFalse(any(item["owner_id"] == "account-a" for item in office.skills("account-a")))


if __name__ == "__main__":
    unittest.main()
