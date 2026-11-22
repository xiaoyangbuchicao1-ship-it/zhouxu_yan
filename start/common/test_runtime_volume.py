"""Offline transfer acceptance: preservation, explicit upgrade, overwrite guard."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from start.common.runtime_volume import check_ready, digest, transfer


class RuntimeVolumeTests(unittest.TestCase):
    def test_transfer_preserves_uncheckpointed_wal_and_private_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "legacy", root / "linux"
            source.mkdir()
            with sqlite3.connect(source / "runs.sqlite3") as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("CREATE TABLE run_sessions (id TEXT PRIMARY KEY, body TEXT)")
                db.execute("INSERT INTO run_sessions VALUES (?, ?)", ("原会话", "原问题与暂停进度"))
                db.commit()
                expected = digest(db)
                # Keep the connection open so committed data still lives in
                # its WAL; the stopped-source fixture has no concurrent writer.
                with sqlite3.connect(source / "maritime-office.sqlite3") as skills:
                    skills.execute("CREATE TABLE skills (instructions TEXT)")
                    skills.execute("INSERT INTO skills VALUES (?)", ("完整私有提示词",))
                transfer(source, target)
            with sqlite3.connect(target / "runs.sqlite3") as copied:
                self.assertEqual(digest(copied), expected)
                self.assertEqual(copied.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            with sqlite3.connect(target / "maritime-office.sqlite3") as copied:
                self.assertEqual(copied.execute("SELECT instructions FROM skills").fetchone(), ("完整私有提示词",))

    def test_startup_rejects_empty_volume_with_legacy_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "legacy", root / "linux"
            source.mkdir()
            target.mkdir()
            (source / "runs.sqlite3").touch()
            with self.assertRaisesRegex(RuntimeError, "explicit migration"):
                check_ready(target, source)
            check_ready(target, root / "fresh-install")

    def test_existing_destination_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "legacy", root / "linux"
            source.mkdir()
            target.mkdir()
            (source / "runs.sqlite3").touch()
            original = target / "runs.sqlite3"
            original.write_bytes(b"existing")
            with self.assertRaises(FileExistsError):
                transfer(source, target)
            self.assertEqual(original.read_bytes(), b"existing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
