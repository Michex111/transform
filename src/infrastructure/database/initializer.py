import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from src.infrastructure.database.session import resolve_database_url


def build_alembic_config() -> Config:
    """
    Build an Alembic Config programmatically so migrations can run without an
    alembic.ini file on disk (required for containerized deployments).
    """
    project_root = Path(__file__).resolve().parents[3]
    alembic_config = Config()
    alembic_config.set_main_option(
        "script_location",
        str(project_root / "src" / "infrastructure" / "database" / "migrations"),
    )
    alembic_config.set_main_option("sqlalchemy.url", resolve_database_url())
    alembic_config.set_main_option("prepend_sys_path", str(project_root))
    alembic_config.set_main_option("path_separator", "os")
    return alembic_config


def _run_database_migrations() -> None:
    command.upgrade(build_alembic_config(), "head")


async def initialize_database() -> None:
    await asyncio.to_thread(_run_database_migrations)
