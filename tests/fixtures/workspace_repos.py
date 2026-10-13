"""Shared SQLite-backed scaffolding for the workspace service tests.

The services are exercised against the **real** SQL repositories rather than
hand-written repository fakes. That is deliberate: the ownership predicates, the
conditional claim and the idempotency-key conflict handling are all in the
adapter, and a fake would happily accept behaviour the real adapter cannot
perform — which is exactly how a repository bug survives a green test suite.

A file-backed SQLite database with ``NullPool`` is used rather than ``:memory:``,
following the pattern already established in this suite: an in-memory database
plus a second connection yields "no such table", and that flakiness looks like a
product bug.
"""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

# Registers every ORM model on ``Base.metadata``.
import src.infrastructure.database.models  # noqa: F401
from src.infrastructure.adapters.repository.sql_execution_repo import (
    SQLExecutionRepository,
)
from src.infrastructure.adapters.repository.sql_workspace_repo import (
    SQLRuntimeImageRevisionRepository,
    SQLRuntimeRepository,
    SQLWorkspaceRepository,
)
from src.infrastructure.database.session import Base


@dataclass
class WorkspaceRepos:
    """One session's worth of real repositories, plus the engine that owns them."""

    engine: AsyncEngine
    session: AsyncSession
    workspaces: SQLWorkspaceRepository
    runtimes: SQLRuntimeRepository
    revisions: SQLRuntimeImageRevisionRepository
    executions: SQLExecutionRepository


async def build_repos(tmp_path: Path, name: str = "workspaces.sqlite") -> WorkspaceRepos:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / name}", poolclass=NullPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    return WorkspaceRepos(
        engine=engine,
        session=session,
        workspaces=SQLWorkspaceRepository(session=session),
        runtimes=SQLRuntimeRepository(session=session),
        revisions=SQLRuntimeImageRevisionRepository(session=session),
        executions=SQLExecutionRepository(session=session),
    )


async def dispose_repos(repos: WorkspaceRepos) -> None:
    await repos.session.close()
    await repos.engine.dispose()
