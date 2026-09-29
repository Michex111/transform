"""The ``0019_ai_assistant`` migration: tables, idempotency, reversibility.

The properties worth pinning are the ones that turn a migration mistake into an
*outage* rather than a bug:

1. The two tables the models expect must exist afterwards, or the API boots and
   then fails on the first assistant request.
2. A re-run must be a no-op. ``RUN_MIGRATIONS=true`` makes a migration error a
   startup failure, so a database where the tables were created out of band (or
   whose alembic stamp was reset) must not abort the boot.
3. ``downgrade`` must remove both tables, so the revision is genuinely
   reversible — and must drop ``ai_messages`` first, while its foreign key
   target still exists.
4. The ``(conversation_id, position)`` index the history read relies on must be
   part of the migration, not only of ``create_all`` (which production never
   runs).
"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "infrastructure"
    / "database"
    / "migrations"
    / "versions"
    / "0019_ai_assistant.py"
)

_CONVERSATIONS = "ai_conversations"
_MESSAGES = "ai_messages"

_CREATE_USERS = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(50) NOT NULL,
    email VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL
)
"""


@pytest.fixture(name="migration")
def migration_fixture():
    """Load the migration module straight from its file (it is not a package)."""
    spec = importlib.util.spec_from_file_location("migration_0019", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text(_CREATE_USERS))
    return engine


def _run(engine: sa.Engine, fn) -> None:
    """Invoke a migration callable against ``engine`` via Alembic's op proxy."""
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            fn()


def _tables(engine: sa.Engine) -> set[str]:
    with engine.connect() as connection:
        return set(sa.inspect(connection).get_table_names())


def _indexes(engine: sa.Engine, table: str) -> set[str]:
    with engine.connect() as connection:
        return {
            index["name"]
            for index in sa.inspect(connection).get_indexes(table)
            if index["name"] is not None
        }


def test_upgrade_creates_both_tables(migration) -> None:
    engine = _engine()
    _run(engine, migration.upgrade)
    assert {_CONVERSATIONS, _MESSAGES} <= _tables(engine)


def test_upgrade_creates_the_history_index(migration) -> None:
    engine = _engine()
    _run(engine, migration.upgrade)
    assert "ix_ai_messages_conversation_position" in _indexes(engine, _MESSAGES)
    assert "ix_ai_conversations_user_id" in _indexes(engine, _CONVERSATIONS)


def test_upgrade_is_idempotent(migration) -> None:
    """Re-running must not raise: a duplicate-table error is a startup outage."""
    engine = _engine()
    _run(engine, migration.upgrade)
    _run(engine, migration.upgrade)
    assert {_CONVERSATIONS, _MESSAGES} <= _tables(engine)


def test_downgrade_removes_both_tables(migration) -> None:
    engine = _engine()
    _run(engine, migration.upgrade)
    _run(engine, migration.downgrade)
    assert _CONVERSATIONS not in _tables(engine)
    assert _MESSAGES not in _tables(engine)


def test_downgrade_is_idempotent(migration) -> None:
    engine = _engine()
    _run(engine, migration.upgrade)
    _run(engine, migration.downgrade)
    _run(engine, migration.downgrade)
    assert _MESSAGES not in _tables(engine)


def test_revision_ids_are_within_the_alembic_limit(migration) -> None:
    """Alembic's version column is 32 characters wide."""
    assert len(migration.revision) <= 32
    assert migration.down_revision == "0018_user_default_save_folder"


def test_the_message_table_cascades_from_its_conversation(migration) -> None:
    engine = _engine()
    _run(engine, migration.upgrade)
    with engine.connect() as connection:
        foreign_keys = sa.inspect(connection).get_foreign_keys(_MESSAGES)
    assert [fk["referred_table"] for fk in foreign_keys] == [_CONVERSATIONS]
