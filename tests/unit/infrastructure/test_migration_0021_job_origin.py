"""The ``0021_job_origin`` migration: column, default, idempotency, reversal.

Pins the properties that turn a migration mistake into an *outage* rather than
a bug:

1. The ``origin`` column the model expects must exist afterwards, or the API
   boots and then fails on the first job read/write.
2. The column is non-nullable, so it must carry a ``server_default`` or
   ``ADD COLUMN`` against the populated production table fails outright.
3. A re-run must be a no-op: ``RUN_MIGRATIONS=true`` makes a migration error a
   *startup* failure, so a database where the column was added out of band (or
   whose alembic stamp was reset) must not abort the boot on a duplicate-column
   error.
4. ``downgrade`` must remove the column, so the revision is genuinely
   reversible.

The default is also behavioural: a row that predates the column must read as
``WEB``, which is the pre-existing spend order (plan credits first).
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
    / "0021_job_origin.py"
)

_TABLE = "conversion_jobs"
_NEW_COLUMN = "origin"

#: Only the columns the migration touches need to exist; the origin column is
#: added by the migration itself.
_CREATE_TABLE = """
CREATE TABLE conversion_jobs (
    job_id VARCHAR(36) PRIMARY KEY,
    status VARCHAR(20) NOT NULL,
    source_format VARCHAR(20) NOT NULL,
    target_format VARCHAR(20) NOT NULL,
    input_file VARCHAR(255) NOT NULL,
    created_at DATETIME NOT NULL
)
"""


@pytest.fixture(name="migration")
def migration_fixture():
    """Load the migration module straight from its file (it is not a package)."""
    spec = importlib.util.spec_from_file_location("migration_0021", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engine_with_conversion_jobs_table() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text(_CREATE_TABLE))
    return engine


def _columns(engine: sa.Engine) -> set[str]:
    return {col["name"] for col in sa.inspect(engine).get_columns(_TABLE)}


def _run(engine: sa.Engine, migration, direction: str) -> None:
    """Execute ``upgrade``/``downgrade`` against a real connection.

    The migration module reaches for the alembic ``op`` proxy as a module
    global, so it is bound to a live :class:`Operations` here rather than being
    imported.
    """
    with engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        getattr(migration, direction)()


def test_upgrade_adds_the_origin_column(migration) -> None:
    engine = _engine_with_conversion_jobs_table()
    _run(engine, migration, "upgrade")

    assert _NEW_COLUMN in _columns(engine)


def test_upgrade_is_idempotent(migration) -> None:
    """Running twice must not raise — a duplicate column would be a boot failure."""
    engine = _engine_with_conversion_jobs_table()
    _run(engine, migration, "upgrade")
    _run(engine, migration, "upgrade")  # must not raise

    assert _NEW_COLUMN in _columns(engine)


def test_downgrade_removes_the_column(migration) -> None:
    engine = _engine_with_conversion_jobs_table()
    _run(engine, migration, "upgrade")
    _run(engine, migration, "downgrade")

    assert _NEW_COLUMN not in _columns(engine)
    # The pre-existing columns must survive the rollback.
    assert "input_file" in _columns(engine)


def test_downgrade_is_idempotent(migration) -> None:
    engine = _engine_with_conversion_jobs_table()
    _run(engine, migration, "upgrade")
    _run(engine, migration, "downgrade")
    _run(engine, migration, "downgrade")  # must not raise


def test_existing_rows_default_to_web(migration) -> None:
    """A row that predates the column must read as WEB, not NULL.

    The column is non-nullable, so without the server default the ADD COLUMN
    would fail on the populated table; with it, every existing job is labelled
    with the pre-existing behaviour.
    """
    engine = _engine_with_conversion_jobs_table()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO conversion_jobs "
                "(job_id, status, source_format, target_format, input_file, created_at) "
                "VALUES ('job-1', 'COMPLETED', 'pdf', 'docx', 'invoice.pdf', "
                "'2026-01-01 00:00:00')"
            )
        )

    _run(engine, migration, "upgrade")

    with engine.begin() as connection:
        origin = connection.execute(
            sa.text("SELECT origin FROM conversion_jobs WHERE job_id = 'job-1'")
        ).scalar_one()

    assert origin == "WEB"
