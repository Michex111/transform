"""Structural guards for the startup migration chain.

These are deliberately source-level checks. The failures they pin down can only
be reproduced against PostgreSQL, and CI has no database, so the invariants are
asserted on the migration sources instead:

* ``asyncpg.exceptions.DuplicateObjectError: type "jobstatus" already exists``
  PostgreSQL does **not** drop an enum type when every table using it is
  dropped, so a database can be genuinely table-less while still holding
  ``jobstatus`` / ``subscriptiontier`` / ``apikeystatus``. A migration that lets
  ``create_table`` issue an implicit ``CREATE TYPE`` then aborts the whole
  ``upgrade head``, and with ``RUN_MIGRATIONS=true`` the API never boots.
  Enum types must therefore be created explicitly with ``checkfirst=True`` and
  referenced from the column with ``create_type=False``.

* ``asyncpg.exceptions.UnsafeNewEnumValueUsageError: unsafe use of new value
  "PRO" of enum type subscriptiontier``
  An enum value added by ``ALTER TYPE ... ADD VALUE`` cannot be used until the
  transaction that added it has committed. ``0009_add_paid_tiers`` adds
  PRO/PRO_PLUS/ENTERPRISE and ``0010_migrate_premium_tiers`` uses PRO, so the
  two must not share one transaction. Alembic wraps the **entire** series in a
  single transaction unless ``transaction_per_migration`` is enabled — which is
  why that setting is asserted here.
"""

from pathlib import Path

import pytest

MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[3] / "src" / "infrastructure" / "database" / "migrations"
)
VERSIONS_DIR = MIGRATIONS_DIR / "versions"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "filename",
    [
        "0001_create_conversion_jobs.py",
        "a23fe025bb21_add_api_keys_table.py",
    ],
)
def test_enum_types_are_created_guardedly(filename: str) -> None:
    """Enum-typed columns must reference a pre-guarded type, not create one inline."""
    source = _read(VERSIONS_DIR / filename)

    assert "create_type=False" in source, (
        f"{filename} must reference its enum with create_type=False; an inline "
        "sa.Enum in create_table issues an unguarded CREATE TYPE"
    )
    assert "checkfirst=True" in source, (
        f"{filename} must create its enum type with checkfirst=True so an "
        "existing type is reused instead of raising DuplicateObjectError"
    )


def test_each_migration_is_committed_in_its_own_transaction() -> None:
    """ALTER TYPE ... ADD VALUE must be committed before 0010 uses the new value."""
    source = _read(MIGRATIONS_DIR / "env.py")

    assert "transaction_per_migration=True" in source, (
        "env.py must enable transaction_per_migration, otherwise the whole "
        "0001 -> head chain runs in one transaction and 0010_migrate_premium_tiers "
        'fails with unsafe use of the newly added "PRO" enum value'
    )
