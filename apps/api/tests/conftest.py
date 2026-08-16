"""Test fixtures.

Database tests run against a real PostgreSQL instance — the schema uses PostgreSQL
enums, JSONB, check constraints, and triggers, so SQLite would prove nothing.

Each test session creates its **own ephemeral database** and drops it afterwards, so
concurrent runs (including `pytest -n auto`) never share state and the suite never
touches a database it did not create. `TEST_DATABASE_URL` names the server and a base
database whose name must end in `_test`; the ephemeral databases derive from it:

    TEST_DATABASE_URL=postgresql+psycopg://rcpia:rcpia@localhost:5433/rcpia_test
    -> rcpia_test_gw0_4f21ac93
"""

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command

if sys.platform == "win32":
    # psycopg's async driver cannot run on Windows' default ProactorEventLoop.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

API_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://rcpia:rcpia@localhost:5432/rcpia_test"
# PostgreSQL identifiers are limited to 63 bytes.
MAX_DATABASE_NAME_LENGTH = 63


def base_test_database_url() -> str:
    """Return the configured test URL, refusing anything not clearly a test database.

    Ephemeral databases are created from this name, and dropped at the end of the
    session. The `_test` suffix keeps a mistyped URL from producing throwaway databases
    next to a production one.
    """
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    database = sa.make_url(url).database
    if not database or not database.endswith("_test"):
        raise RuntimeError(
            f"Refusing to run database tests against {database!r}. "
            "TEST_DATABASE_URL must name a database ending in '_test'."
        )
    return url


def _ephemeral_database_url(base_url: str) -> str:
    """Derive a unique database name for this session (and this xdist worker)."""
    base = sa.make_url(base_url)
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    suffix = f"_{worker}_{uuid.uuid4().hex[:8]}"
    name = f"{base.database}{suffix}"[-MAX_DATABASE_NAME_LENGTH:]
    # str(URL) masks the password; render explicitly so the URL stays usable.
    return base.set(database=name).render_as_string(hide_password=False)


def _server_engine(url: str) -> sa.Engine:
    """Engine bound to the maintenance database, for CREATE/DROP DATABASE."""
    return sa.create_engine(
        sa.make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )


def _create_database(url: str) -> None:
    name = sa.make_url(url).database
    engine = _server_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    finally:
        engine.dispose()


def _drop_database(url: str) -> None:
    name = sa.make_url(url).database
    engine = _server_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}"'))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[str]:
    url = _ephemeral_database_url(base_test_database_url())
    _create_database(url)

    alembic_config = Config(str(API_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(API_ROOT / "alembic"))
    alembic_config.set_main_option("sqlalchemy.url", url)

    try:
        # Round-tripping proves the migration applies to an empty database and that
        # downgrade cleans up completely — a leftover enum type, trigger, or function
        # would break the second upgrade.
        command.upgrade(alembic_config, "head")
        command.downgrade(alembic_config, "base")
        command.upgrade(alembic_config, "head")
        yield url
    finally:
        _drop_database(url)


@pytest.fixture
async def session_factory(
    migrated_database: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Committing sessions, for tests that need several concurrent connections.

    The rollback-per-test trick used by `session` cannot express concurrency, so these
    tests commit for real. The ephemeral database is dropped at the end of the session,
    so leftover rows do not leak between runs.
    """
    engine = create_async_engine(migrated_database)
    try:
        # Committed rows would otherwise leak into the next test. TRUNCATE cascades from
        # organizations to every clinical table, and does not fire the row-level
        # append-only triggers.
        async with engine.begin() as connection:
            await connection.execute(sa.text("TRUNCATE organizations CASCADE"))
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def session(migrated_database: str) -> AsyncIterator[AsyncSession]:
    """Session wrapped in a transaction that is rolled back after each test."""
    engine = create_async_engine(migrated_database)
    connection = await engine.connect()
    transaction = await connection.begin()
    factory = async_sessionmaker(bind=connection, expire_on_commit=False)

    async with factory() as db_session:
        try:
            yield db_session
        finally:
            # A test that asserts on an IntegrityError leaves the transaction
            # aborted, so roll the session back before unwinding the connection.
            await db_session.rollback()
            await db_session.close()
            if transaction.is_active:
                await transaction.rollback()
            await connection.close()
            await engine.dispose()
