import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from infrastructure.database.session import resolve_database_url


def _run_database_migrations() -> None:
    project_root = Path(__file__).resolve().parents[3]
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", resolve_database_url())
    command.upgrade(alembic_config, "head")


async def initialize_database() -> None:
    await asyncio.to_thread(_run_database_migrations)
