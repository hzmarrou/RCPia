"""Test fixtures.

Database tests run against a real PostgreSQL instance — the schema uses PostgreSQL
enums, JSONB, and check constraints, so SQLite would not prove anything. The test
database is created if absent and migrated with Alembic, which also verifies that
migrations apply cleanly to an empty database.

Point the tests at a server with TEST_DATABASE_URL, e.g.:

    TEST_DATABASE_URL=postgresql+psycopg://rcpia:rcpia@localhost:5433/rcpia_test
"""

import asyncio
import os
import sys
from collections.abc import AsyncIterator
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


def test_database_url() -> str:
    """Return the test database URL, refusing anything that is not clearly a test database.

    The session fixture drops and recreates the whole schema. A mistyped
    TEST_DATABASE_URL pointing at a development or clinical database would destroy
    it, so the name must end in `_test`.
    """
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    database = sa.make_url(url).database
    if not database or not database.endswith("_test"):
        raise RuntimeError(
            f"Refusing to run destructive database tests against {database!r}. "
            "TEST_DATABASE_URL must name a database ending in '_test'."
        )
    return url


def _ensure_database_exists(url: str) -> None:
    target = sa.make_url(url)
    server = target.set(database="postgres")
    engine = sa.create_engine(server, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            ).scalar()
            if not exists:
                connection.execute(sa.text(f'CREATE DATABASE "{target.database}"'))
    finally:
        engine.dispose()


def _reset_schema(url: str) -> None:
    """Drop everything, including enum types a bare `downgrade base` would miss."""
    engine = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            connection.execute(sa.text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def migrated_database() -> str:
    url = test_database_url()
    _ensure_database_exists(url)
    _reset_schema(url)

    alembic_config = Config(str(API_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(API_ROOT / "alembic"))
    alembic_config.set_main_option("sqlalchemy.url", url)

    # Round-tripping proves the migration applies to an empty database and that
    # downgrade cleans up completely — a leftover enum type would break the second
    # upgrade with DuplicateObject.
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    return url


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
