"""The ``0028_cloud_workspaces`` migration: tables, indexes, defaults, idempotency.

``RUN_MIGRATIONS=true`` makes any migration error a *startup failure* on the
production API, so the properties pinned here are the ones that turn a mistake
into an outage:

1. All four tables and every index exist afterwards.
2. A re-run is a no-op — a database migrated out of band, or with a reset alembic
   stamp, must not abort the boot with a duplicate-table error.
3. ``downgrade`` removes exactly what ``upgrade`` created.
4. The one-runtime-per-workspace constraint and the *partial* idempotency index
   really are enforced, because both are the kind of rule that is silently
   useless if the DDL is slightly wrong.

The full chain cannot run here (earlier revisions use PostgreSQL-only
``ALTER TYPE ... ADD VALUE``), so the pre-migration schema is built by hand and
the migration callable is invoked directly through Alembic's op proxy — the same
technique the other single-migration tests in this folder use.
"""

import importlib.util
from pathlib import Path
import uuid

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
    / "0028_cloud_workspaces.py"
)

_TABLES = ("workspaces", "workspace_runtimes", "executions", "runtime_image_revisions")

#: The schema as it exists before this revision: only ``users`` matters, because
#: the new tables reference it.
_PRE_EXISTING_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(50) NOT NULL,
    email VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL
);
"""


@pytest.fixture(name="migration")
def migration_fixture():
    """Load the migration module straight from its file (it is not a package)."""
    spec = importlib.util.spec_from_file_location("migration_0028", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        for statement in _PRE_EXISTING_SCHEMA.strip().split(";"):
            if statement.strip():
                connection.execute(sa.text(statement))
    return engine


def _run(engine: sa.Engine, fn) -> None:
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            fn()


def _tables(engine: sa.Engine) -> set[str]:
    with engine.connect() as connection:
        return set(sa.inspect(connection).get_table_names())


def _indexes(engine: sa.Engine, table: str) -> set[str]:
    with engine.connect() as connection:
        return {
            str(item["name"])
            for item in sa.inspect(connection).get_indexes(table)
            if item.get("name") is not None
        }


def _insert_workspace(engine: sa.Engine, workspace_id: str | None = None) -> str:
    workspace_id = workspace_id or str(uuid.uuid4())
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO workspaces (workspace_id, user_id, name, description,"
                " runtime_config, state, execution_count, created_at, updated_at)"
                " VALUES (:id, 1, 'ws', '', '{}', 'ACTIVE', 0, :now, :now)"
            ),
            {"id": workspace_id, "now": "2026-10-13 00:00:00"},
        )
    return workspace_id


class TestUpgrade:
    def test_all_tables_are_created(self, migration):
        engine = _engine()
        _run(engine, migration.upgrade)
        assert set(_TABLES) <= _tables(engine)

    def test_every_declared_index_is_created(self, migration):
        engine = _engine()
        _run(engine, migration.upgrade)
        for name, table, _columns, _unique, _where in migration._INDEXES:
            assert name in _indexes(engine, table), name

    def test_defaults_land_on_new_rows(self, migration):
        """The server defaults are what make a migration cheap on a live table."""
        engine = _engine()
        _run(engine, migration.upgrade)
        workspace_id = _insert_workspace(engine)
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO workspace_runtimes (runtime_id, workspace_id, provider,"
                    " created_at) VALUES ('rt-1', :ws, 'docker', :now)"
                ),
                {"ws": workspace_id, "now": "2026-10-13 00:00:00"},
            )
            row = connection.execute(
                sa.text(
                    "SELECT state, resource_profile, provider_metadata"
                    " FROM workspace_runtimes WHERE runtime_id = 'rt-1'"
                )
            ).one()
        assert row.state == "STOPPED"
        assert row.resource_profile == "standard"
        assert row.provider_metadata in ("{}", None) or row.provider_metadata == {}

    def test_running_upgrade_twice_is_a_no_op(self, migration):
        engine = _engine()
        _run(engine, migration.upgrade)
        _run(engine, migration.upgrade)
        assert set(_TABLES) <= _tables(engine)

    def test_upgrade_recreates_missing_indexes(self, migration):
        """The guard is per *index*, not per table.

        A database whose tables exist but whose indexes do not — the shape a
        `CREATE INDEX` that was interrupted, or a hand-applied table, leaves
        behind — must converge on the next boot instead of aborting it. This is
        the realistic partial state: PostgreSQL DDL is transactional, so a
        table either exists in full or not at all, but an index can be the one
        statement that did not land.
        """
        engine = _engine()
        _run(engine, migration.upgrade)
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                from alembic import op

                op.drop_index("ix_workspaces_user_id", table_name="workspaces")
                op.drop_index(
                    "uq_workspace_runtimes_workspace_id",
                    table_name="workspace_runtimes",
                )
        assert "ix_workspaces_user_id" not in _indexes(engine, "workspaces")

        _run(engine, migration.upgrade)

        assert "ix_workspaces_user_id" in _indexes(engine, "workspaces")
        assert "uq_workspace_runtimes_workspace_id" in _indexes(
            engine, "workspace_runtimes"
        )


class TestConstraints:
    def test_one_runtime_per_workspace_is_enforced_by_the_database(self, migration):
        engine = _engine()
        _run(engine, migration.upgrade)
        workspace_id = _insert_workspace(engine)
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO workspace_runtimes (runtime_id, workspace_id, provider,"
                    " created_at) VALUES ('rt-1', :ws, 'docker', :now)"
                ),
                {"ws": workspace_id, "now": "2026-10-13 00:00:00"},
            )
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "INSERT INTO workspace_runtimes (runtime_id, workspace_id,"
                        " provider, created_at) VALUES ('rt-2', :ws, 'docker', :now)"
                    ),
                    {"ws": workspace_id, "now": "2026-10-13 00:00:00"},
                )

    def test_the_idempotency_key_is_unique_per_workspace_but_not_globally(self, migration):
        engine = _engine()
        _run(engine, migration.upgrade)
        first = _insert_workspace(engine)
        second = _insert_workspace(engine)

        statement = sa.text(
            "INSERT INTO executions (execution_id, workspace_id, user_id, mode, source,"
            " idempotency_key, created_at) VALUES (:id, :ws, 1, 'SCRIPT', 'x', :key, :now)"
        )
        with engine.begin() as connection:
            connection.execute(
                statement,
                {"id": "e1", "ws": first, "key": "k1", "now": "2026-10-13 00:00:00"},
            )
            # A different workspace may reuse the same key.
            connection.execute(
                statement,
                {"id": "e2", "ws": second, "key": "k1", "now": "2026-10-13 00:00:00"},
            )
        # ...but not the same workspace.
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    statement,
                    {"id": "e3", "ws": first, "key": "k1", "now": "2026-10-13 00:00:00"},
                )

    def test_many_executions_may_have_no_idempotency_key_at_all(self, migration):
        """The partial index must not make the common case collide."""
        engine = _engine()
        _run(engine, migration.upgrade)
        workspace_id = _insert_workspace(engine)
        with engine.begin() as connection:
            for index in range(3):
                connection.execute(
                    sa.text(
                        "INSERT INTO executions (execution_id, workspace_id, user_id,"
                        " mode, source, created_at) VALUES (:id, :ws, 1, 'SCRIPT', 'x',"
                        " :now)"
                    ),
                    {
                        "id": f"e{index}",
                        "ws": workspace_id,
                        "now": "2026-10-13 00:00:00",
                    },
                )


class TestDowngrade:
    def test_downgrade_removes_everything_it_created(self, migration):
        engine = _engine()
        _run(engine, migration.upgrade)
        _run(engine, migration.downgrade)
        assert not (set(_TABLES) & _tables(engine))
        assert "users" in _tables(engine)
