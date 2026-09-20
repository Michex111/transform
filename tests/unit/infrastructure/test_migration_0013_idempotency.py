"""The ``0013_user_file_extension`` migration must be safe to re-run.

Regression context: this migration was applied to the shared database by a local
run *before* it reached the deployed branch. ``alembic_version`` therefore only
resolved locally, and the deployed API crash-looped at startup with
``Can't locate revision identified by '0013_user_file_extension'``. Once the
revision existed in the deployed image again, the upgrade had to tolerate the
column already being present — a plain ``op.add_column`` would fail with
"column already exists" and put the API back into a crash loop.
"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import op
from alembic.migration import MigrationContext
from alembic.operations import Operations

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "infrastructure"
    / "database"
    / "migrations"
    / "versions"
    / "0013_user_file_extension.py"
)
_INDEX = "ix_user_files_user_id_file_extension"

_CREATE_TABLE = """
CREATE TABLE user_files (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    file_name TEXT,
    file_extension VARCHAR(20) NOT NULL DEFAULT ''
)
"""

_CREATE_TABLE_WITHOUT_COLUMN = """
CREATE TABLE user_files (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    file_name TEXT
)
"""


@pytest.fixture(name="migration")
def migration_fixture():
    """Load the migration module straight from its file (it is not a package)."""
    spec = importlib.util.spec_from_file_location("migration_0013", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine: sa.Engine, fn) -> None:
    """Invoke a migration callable against ``engine`` via Alembic's op proxy."""
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            fn()


def _columns(engine: sa.Engine) -> set[str]:
    with engine.connect() as connection:
        return {col["name"] for col in sa.inspect(connection).get_columns("user_files")}


def _indexes(engine: sa.Engine) -> set[str]:
    with engine.connect() as connection:
        return {idx["name"] for idx in sa.inspect(connection).get_indexes("user_files")}


def test_upgrade_adds_column_index_and_backfills(migration) -> None:
    """A clean database gets the column, the index, and a derived extension."""
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text(_CREATE_TABLE_WITHOUT_COLUMN))
        connection.execute(
            sa.text("INSERT INTO user_files (id, user_id, file_name) VALUES (1, 7, 'Report.PDF')")
        )

    _run(engine, migration.upgrade)

    assert "file_extension" in _columns(engine)
    assert _INDEX in _indexes(engine)
    with engine.connect() as connection:
        value = connection.execute(
            sa.text("SELECT file_extension FROM user_files WHERE id = 1")
        ).scalar()
    assert value == "pdf"


def test_upgrade_is_a_no_op_when_the_column_already_exists(migration) -> None:
    """The out-of-band-applied case: column present, no index yet."""
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text(_CREATE_TABLE))
        connection.execute(
            sa.text(
                "INSERT INTO user_files (id, user_id, file_name, file_extension) "
                "VALUES (1, 7, 'Report.PDF', 'pdf')"
            )
        )

    _run(engine, migration.upgrade)

    # The pre-existing value must survive untouched...
    with engine.connect() as connection:
        value = connection.execute(
            sa.text("SELECT file_extension FROM user_files WHERE id = 1")
        ).scalar()
    assert value == "pdf"
    # ...and the missing index is still created.
    assert _INDEX in _indexes(engine)


def test_upgrade_can_run_twice(migration) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text(_CREATE_TABLE_WITHOUT_COLUMN))

    _run(engine, migration.upgrade)
    _run(engine, migration.upgrade)

    assert "file_extension" in _columns(engine)
    assert _INDEX in _indexes(engine)


def test_downgrade_drops_column_and_is_idempotent(migration) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text(_CREATE_TABLE_WITHOUT_COLUMN))

    _run(engine, migration.upgrade)
    _run(engine, migration.downgrade)
    _run(engine, migration.downgrade)

    assert "file_extension" not in _columns(engine)
    assert _INDEX not in _indexes(engine)


def test_derive_extension_matches_runtime_helper(migration) -> None:
    """The inlined helper mirrors storage/sanitize.extension_from_filename."""
    assert migration._derive_extension("Report.PDF") == "pdf"
    assert migration._derive_extension("archive.TAR.GZ") == "tar.gz"
    assert migration._derive_extension("no-extension") == ""
    assert migration._derive_extension("") == ""
    assert migration._derive_extension(None) == ""
    assert migration._derive_extension("dir.name/file") == ""
