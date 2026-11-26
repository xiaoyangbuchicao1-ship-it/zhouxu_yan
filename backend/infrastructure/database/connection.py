"""Database engine and migration entry point for the modular monolith."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from backend.config.settings import DatabaseSettings
from backend.infrastructure.database.models import Base

BACKEND_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = BACKEND_ROOT.parent


def resolve_database_url(value: str) -> str:
    """Resolve the configured local SQLite path without hardcoding a machine path."""

    if not value.startswith("sqlite:///"):
        return value
    raw_path = value.removeprefix("sqlite:///")
    parsed = urlparse(value)
    if parsed.netloc or Path(raw_path).is_absolute() or (
        len(raw_path) > 1 and raw_path[1] == ":"
    ):
        return value
    path = (WORKSPACE_ROOT / raw_path).resolve()
    return f"sqlite:///{path.as_posix()}"


class Database:
    def __init__(self, settings: DatabaseSettings | str):
        configured_url = settings.url if isinstance(settings, DatabaseSettings) else settings
        self.url = resolve_database_url(configured_url)
        connect_args = {"check_same_thread": False} if self.url.startswith("sqlite") else {}
        self.engine = create_engine(
            self.url, connect_args=connect_args, pool_pre_ping=True
        )
        if self.url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )
        self.fresh_bootstrap = False

    def initialize(self, *, require_explicit_migrations: bool = False) -> None:
        """Create the checked-in relational schema before authenticated traffic.

        Fresh databases are bootstrapped.  When ``require_explicit_migrations``
        is enabled, an existing database must already carry the explicit
        ``dynamic_admin_v1`` marker; ORM table creation never upgrades it.
        """

        if self.url.startswith("sqlite:///"):
            # A fresh runtime volume may not contain the configured parent
            # directory yet.  Create it before SQLAlchemy tries to inspect
            # the file; inspection itself opens the SQLite database.
            database_path = Path(self.url.removeprefix("sqlite:///"))
            database_path.parent.mkdir(parents=True, exist_ok=True)

        existing_tables: set[str] = set()
        if self.url.startswith("sqlite"):
            existing_tables = set(inspect(self.engine).get_table_names())
            self.fresh_bootstrap = not existing_tables
            if require_explicit_migrations and existing_tables:
                with self.engine.connect() as connection:
                    marker_exists = bool(
                        connection.exec_driver_sql(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_versions'"
                        ).scalar()
                    )
                    if not marker_exists:
                        raise RuntimeError("explicit_migration_required")
                    has_v3 = bool(
                        connection.exec_driver_sql(
                            "SELECT 1 FROM schema_versions WHERE version='dynamic_admin_v3' LIMIT 1"
                        ).scalar()
                    )
                    has_ws_v1 = bool(
                        connection.exec_driver_sql(
                            "SELECT 1 FROM schema_versions WHERE version='user_workspace_v1' LIMIT 1"
                        ).scalar()
                    )
                    if not has_v3 or not has_ws_v1:
                        raise RuntimeError("explicit_migration_required")
        # Additive SQLite migration for the run contract.  ``create_all`` does
        # not alter an existing table, and must therefore run after these
        # columns exist so the idempotency index can be created safely.
        if self.url.startswith("sqlite"):
            self._migrate_sqlite()
        Base.metadata.create_all(self.engine)
        if self.url.startswith("sqlite"):
            self._migrate_sqlite()
            if not existing_tables:
                with self.engine.begin() as connection:
                    connection.exec_driver_sql(
                        "CREATE TABLE IF NOT EXISTS schema_versions (version TEXT PRIMARY KEY, applied_at DATETIME NOT NULL)"
                    )
                    for version in (
                        "dynamic_admin_v1",
                        "dynamic_admin_v2",
                        "dynamic_admin_v3",
                        "user_workspace_v1",
                        "ops_email_v1",
                        "account_register_ip_v1",
                        "account_quota_v1",
                    ):
                        connection.exec_driver_sql(
                            f"INSERT OR IGNORE INTO schema_versions(version, applied_at) VALUES ('{version}', datetime('now'))"
                        )

    def _migrate_sqlite(self) -> None:
        inspector = inspect(self.engine)
        tables = set(inspector.get_table_names())
        if "accounts" in tables:
            account_columns = {
                column["name"] for column in inspector.get_columns("accounts")
            }
            if "role" not in account_columns:
                with self.engine.begin() as connection:
                    connection.execute(
                        text(
                            "ALTER TABLE accounts ADD COLUMN role VARCHAR(16) "
                            "NOT NULL DEFAULT 'user'"
                        )
                    )
            if "register_ip" not in account_columns:
                with self.engine.begin() as connection:
                    connection.execute(
                        text("ALTER TABLE accounts ADD COLUMN register_ip VARCHAR(64)")
                    )
        if "account_capability_grants" in tables:
            grant_columns = {
                column["name"]
                for column in inspector.get_columns("account_capability_grants")
            }
            if "material_quota_mb" not in grant_columns:
                with self.engine.begin() as connection:
                    connection.execute(
                        text(
                            "ALTER TABLE account_capability_grants "
                            "ADD COLUMN material_quota_mb INTEGER"
                        )
                    )
        if "conversations" not in tables:
            conversation_columns = set()
        else:
            conversation_columns = {
                column["name"]
                for column in inspector.get_columns("conversations")
            }
        with self.engine.begin() as connection:
            if "conversations" in tables and "project_id" not in conversation_columns:
                connection.execute(text("ALTER TABLE conversations ADD COLUMN project_id VARCHAR(32)"))
            if "conversations" in tables and "mode" not in conversation_columns:
                connection.execute(
                    text("ALTER TABLE conversations ADD COLUMN mode VARCHAR(16) NOT NULL DEFAULT 'chat'")
                )
        self._migrate_conversation_project_fk(tables)
        if "conversation_runs" not in tables:
            return
        existing = {
            column["name"] for column in inspector.get_columns("conversation_runs")
        }
        additions = {
            "partial_output": "TEXT",
            "revision": "INTEGER NOT NULL DEFAULT 0",
            "client_request_id": "VARCHAR(128)",
            "retry_of_run_id": "VARCHAR(64)",
            "payload_json": "TEXT",
            "material_ids_json": "TEXT",
            "progress_json": "TEXT",
            "last_sequence": "INTEGER NOT NULL DEFAULT 0",
            "first_event_at": "DATETIME",
            "first_delta_at": "DATETIME",
            "last_event_at": "DATETIME",
            "error_code": "VARCHAR(80)",
        }
        with self.engine.begin() as connection:
            for name, declaration in additions.items():
                if name not in existing:
                    connection.execute(
                        text(f"ALTER TABLE conversation_runs ADD COLUMN {name} {declaration}")
                    )
            if "conversation_runs" in tables:
                connection.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS "
                        "uq_conversation_runs_idempotency "
                        "ON conversation_runs (conversation_id, client_request_id)"
                    )
                )

    def _migrate_conversation_project_fk(self, tables: set[str]) -> None:
        """Add the project ownership FK to pre-project SQLite databases.

        SQLite cannot add a foreign key with ``ALTER TABLE``.  The compatible
        migration copies every existing row into the new table, keeps invalid
        historical rows for inspection, and enables enforcement for all future
        writes.  No business row is filtered or deleted.
        """

        if "conversations" not in tables or "projects" not in tables:
            return
        inspector = inspect(self.engine)
        foreign_keys = inspector.get_foreign_keys("conversations")
        if any(fk.get("referred_table") == "projects" for fk in foreign_keys):
            return
        with self.engine.connect() as connection:
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
            try:
                with connection.begin():
                    connection.exec_driver_sql(
                        """
                        CREATE TABLE conversations__contract_migration (
                            id VARCHAR(32) NOT NULL PRIMARY KEY,
                            account_id VARCHAR(32) NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                            project_id VARCHAR(32) REFERENCES projects(id) ON DELETE SET NULL,
                            title VARCHAR(200) NOT NULL,
                            mode VARCHAR(16) NOT NULL DEFAULT 'chat',
                            pinned BOOLEAN NOT NULL,
                            archived_at DATETIME,
                            created_at DATETIME NOT NULL,
                            updated_at DATETIME NOT NULL
                        )
                        """
                    )
                    connection.exec_driver_sql(
                        """
                        INSERT INTO conversations__contract_migration
                            (id, account_id, project_id, title, mode, pinned, archived_at, created_at, updated_at)
                        SELECT id, account_id, project_id, title, COALESCE(mode, 'chat'), pinned, archived_at, created_at, updated_at
                        FROM conversations
                        """
                    )
                    connection.exec_driver_sql("DROP TABLE conversations")
                    connection.exec_driver_sql(
                        "ALTER TABLE conversations__contract_migration RENAME TO conversations"
                    )
                    connection.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_conversations_account_id "
                        "ON conversations (account_id)"
                    )
                    connection.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_conversations_project_id "
                        "ON conversations (project_id)"
                    )
                    connection.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_conversations_account_active "
                        "ON conversations (account_id, archived_at)"
                    )
                    connection.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_conversations_account_project "
                        "ON conversations (account_id, project_id)"
                    )
                    connection.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_conversations_account_order "
                        "ON conversations (account_id, pinned, updated_at)"
                    )
            finally:
                connection.commit()
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()

    def foreign_key_violations(self) -> list[dict[str, object]]:
        """Return orphan rows without deleting or repairing existing data."""

        if not self.url.startswith("sqlite"):
            return []
        with self.engine.connect() as connection:
            rows = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        return [
            {"table": row[0], "rowid": row[1], "parent": row[2], "fkid": row[3]}
            for row in rows
        ]

    def session(self) -> Session:
        return self.session_factory()

    def close(self) -> None:
        self.engine.dispose()
