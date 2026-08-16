"""Blob storage contract.

Uploaded dossiers, generated PPS packages, and Pi session logs live in blob storage;
PostgreSQL holds their keys and checksums. The same interface backs a local directory in
development and Azure Blob Storage in deployment, so nothing above this layer knows which
one it is talking to.

Blobs are immutable once written: a correction produces a new key rather than a rewrite,
matching the append-only rule the clinical tables enforce.
"""

import hashlib
import posixpath
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Keys are POSIX-style paths, e.g. cases/<case_id>/dossier/<uuid>.pdf
KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,499}$")
DEFAULT_CONTENT_TYPE = "application/octet-stream"


class StorageError(Exception):
    """Base class for storage failures."""


class BlobNotFound(StorageError):
    """No blob exists for the requested key."""


class BlobAlreadyExists(StorageError):
    """A blob already exists for this key.

    Blobs are immutable, so this is the expected answer to a repeated write rather than
    an unexpected failure: a correction gets a new key.
    """


class InvalidKey(StorageError):
    """The key is empty, malformed, or attempts to escape the store."""


@dataclass(frozen=True)
class BlobInfo:
    """What the store knows about a stored object."""

    key: str
    size_bytes: int
    content_type: str
    checksum_sha256: str


def checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def validate_key(key: str) -> str:
    """Reject anything that is not a plain relative key.

    Keys are built from case and document identifiers, but they are the one part of a
    blob path that could ever be influenced by a filename from a dossier. Path traversal
    is rejected here, once, rather than being each backend's problem.
    """
    # fullmatch, not match: `match` would accept a trailing newline.
    if not key or not KEY_PATTERN.fullmatch(key):
        raise InvalidKey(f"Invalid blob key: {key!r}")
    if key.startswith("/") or key.endswith("/"):
        raise InvalidKey(f"Invalid blob key: {key!r}")
    if posixpath.normpath(key) != key or ".." in key.split("/"):
        raise InvalidKey(f"Blob key must not traverse directories: {key!r}")
    return key


@runtime_checkable
class BlobStore(Protocol):
    """Storage backend. Implementations must be safe to share between requests."""

    async def put_bytes(self, key: str, content: bytes, content_type: str) -> BlobInfo:
        """Store `content` under `key` and return what was written."""
        ...

    async def get_bytes(self, key: str) -> bytes:
        """Return the stored bytes, or raise BlobNotFound."""
        ...

    async def stat(self, key: str) -> BlobInfo:
        """Return metadata without transferring the body."""
        ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> None:
        """Remove a blob. Used by the audited purge path, not by ordinary flows."""
        ...

    async def signed_url(self, key: str, expires_seconds: int) -> str:
        """A time-limited URL a browser can follow without an API session."""
        ...
