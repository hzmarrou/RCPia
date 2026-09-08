"""Read-only loaded-corpus identity and clean-source provenance; no Settings serialization."""

import hashlib
import importlib.metadata
import platform
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from rcpia.evidence.hashing import payload_sha256
from rcpia.evidence.ingestion import verify_search_configurations

SOURCE_ROOT = Path(__file__).resolve().parents[4]


def source_identity(expected_commit: str, root: Path = SOURCE_ROOT) -> dict[str, object]:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "--no-pager", "-C", str(root), *args],
            text=True,
            encoding="utf-8",
        ).strip()

    commit = git("rev-parse", "HEAD")
    if expected_commit != commit:
        raise ValueError("Source HEAD does not match the required full source commit.")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Runtime baselines require a clean source checkout.")
    tracked = set(git("ls-files", "-z").split("\0"))
    files = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in sorted(tracked)
        if (
            (name.startswith("apps/api/rcpia/") and name.endswith(".py"))
            or name.startswith("agents/")
            and name.endswith((".md", ".json"))
            or name in {"apps/api/pyproject.toml", "apps/api/uv.lock"}
        )
    }
    for path in (root / "apps" / "api" / "rcpia").rglob("*.py"):
        if path.relative_to(root).as_posix() not in files:
            raise ValueError("Untracked/ignored Python source can shadow the recorded checkout.")
    for name, module in tuple(sys.modules.items()):
        if (name == "rcpia" or name.startswith("rcpia.")) and getattr(module, "__file__", None):
            path = Path(module.__file__).resolve()  # type: ignore[arg-type]
            if not path.is_relative_to(root) or path.relative_to(root).as_posix() not in files:
                raise ValueError("Imported RCPia module is outside the recorded source checkout.")
    return {
        "commit": commit,
        "tree": git("rev-parse", "HEAD^{tree}"),
        "working_files_sha256": payload_sha256(files),
        "files": files,
        "python": platform.python_version(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in (
                "sqlalchemy",
                "psycopg",
                "pgvector",
                "pydantic",
                "httpx",
            )
        },
    }


@asynccontextmanager
async def readonly_snapshot(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """One MVCC view for corpus fingerprint, membership checks and both searches."""
    async with engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="REPEATABLE READ")
        async with connection.begin():
            await connection.execute(sa.text("SET TRANSACTION READ ONLY"))
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                yield session


async def corpus_identity(session: AsyncSession) -> dict[str, object]:
    """Hash actual rows server-side, including vectors, not trusted stored text hashes.

    Only row digests cross the wire. UUID/ingest time are excluded so identical reimports
    have identical identities. Every other column (including generated FTS vectors) counts.
    PostgreSQL JSONB encoding/version is explicitly part of this versioned scheme.
    """
    await verify_search_configurations(session)
    documents = (
        await session.execute(
            sa.text("""
        SELECT document_id, encode(sha256(convert_to(
            (to_jsonb(d) - 'ingested_at')::text, 'UTF8')), 'hex')
        FROM evidence_documents d ORDER BY document_id COLLATE "C"
    """)
        )
    ).all()
    chunks = (
        await session.execute(
            sa.text("""
        SELECT chunk_id, encode(sha256(convert_to(
            (to_jsonb(c) - 'id')::text, 'UTF8')), 'hex')
        FROM evidence_chunks c ORDER BY chunk_id COLLATE "C"
    """)
        )
    ).all()
    if not documents or not chunks:
        raise ValueError("The loaded corpus is empty.")
    inventory = (
        (
            await session.execute(
                sa.text("""
        SELECT embedding_model, (embedding IS NOT NULL) AS embedded,
               vector_dims(embedding) AS dimensions, retrieval_eligible, count(*) AS chunks
        FROM evidence_chunks
        GROUP BY embedding_model, (embedding IS NOT NULL), vector_dims(embedding),
                 retrieval_eligible
        ORDER BY embedding_model NULLS FIRST, embedded, dimensions, retrieval_eligible
    """)
            )
        )
        .mappings()
        .all()
    )
    environment = {
        "server_version": (await session.execute(sa.text("SHOW server_version"))).scalar_one(),
        "extensions": [
            list(row)
            for row in (
                await session.execute(
                    sa.text("SELECT extname, extversion FROM pg_extension ORDER BY extname")
                )
            ).all()
        ],
        "settings": [
            list(row)
            for row in (
                await session.execute(
                    sa.text("""
            SELECT name, setting FROM pg_settings
            WHERE name LIKE 'enable_%' OR name LIKE 'hnsw.%' OR name LIKE 'ivfflat.%'
               OR name IN ('default_text_search_config', 'search_path', 'work_mem',
                           'max_parallel_workers_per_gather', 'lc_collate', 'lc_ctype')
            ORDER BY name
        """)
                )
            ).all()
        ],
        "indexes": [
            list(row)
            for row in (
                await session.execute(
                    sa.text("""
            SELECT tablename, indexname, indexdef FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename IN ('evidence_chunks', 'evidence_documents')
            ORDER BY tablename, indexname
        """)
                )
            ).all()
        ],
        "search_configurations": (
            "verified by rcpia.evidence.ingestion.verify_search_configurations"
        ),
    }
    identity = {
        "scheme": "rcpia.loaded-corpus.v1",
        "documents": len(documents),
        "chunks": len(chunks),
        "document_rows_sha256": payload_sha256([list(r) for r in documents]),
        "chunk_rows_sha256": payload_sha256([list(r) for r in chunks]),
        "inventory": [dict(row) for row in inventory],
        "release_ids": list(
            (
                await session.execute(
                    sa.text(
                        "SELECT DISTINCT release_id FROM evidence_documents "
                        "ORDER BY release_id NULLS FIRST"
                    )
                )
            ).scalars()
        ),
        "environment": environment,
    }
    return {**identity, "sha256": payload_sha256(identity)}


def check_output(root: Path, output: Path, source: Path = SOURCE_ROOT) -> Path:
    """Require an existing caller-restricted directory, outside every Git checkout."""
    root = root.resolve(strict=True)
    if not root.is_dir() or root.is_relative_to(source.resolve()):
        raise ValueError("Artifacts must be in an existing restricted directory outside source.")
    if any((parent / ".git").exists() for parent in (root, *root.parents)):
        raise ValueError("Restricted artifacts must not be inside a Git checkout.")
    output = output.absolute()
    if output.is_symlink() or output.exists():
        raise ValueError("Output already exists; previous runs are never overwritten.")
    parent = output.parent.resolve(strict=True)
    if parent != root:
        raise ValueError("Output must be directly inside the restricted artifact directory.")
    return parent / output.name


def restricted_input(path: Path, root: Path) -> bytes:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve(strict=True)) or not resolved.is_file():
        raise ValueError("Frozen inputs must be stored within the restricted artifact directory.")
    return resolved.read_bytes()
