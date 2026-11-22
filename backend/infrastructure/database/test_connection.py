import tempfile
import unittest
from pathlib import Path

from backend.config.settings import DatabaseSettings
from backend.infrastructure.database.connection import Database


class DatabaseInitializationTests(unittest.TestCase):
    def test_fresh_nested_sqlite_path_is_created_before_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "data" / "relational" / "zhouxu.db"
            database = Database(DatabaseSettings(url=f"sqlite:///{database_path}"))
            try:
                database.initialize(require_explicit_migrations=True)
            finally:
                database.close()

            self.assertTrue(database_path.is_file())


if __name__ == "__main__":
    unittest.main()
