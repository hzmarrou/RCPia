"""Local filesystem blob store, for development and on-premises deployments.

Content and metadata are written as two files per key: the bytes themselves, and a
sidecar `.meta.json` holding content type and checksum. A sidecar rather than an
extended attribute, because those do not survive Windows, Docker volumes, or a copy.
"""

import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import quote, urlencode

import anyio.to_thread

from rcpia.storage.base import (
    BlobInfo,
    BlobNotFound,
    InvalidKey,
    checksum,
    validate_key,
)

METADATA_SUFFIX = ".meta.json"


class LocalBlobStore:
    """Files under `root`. Keys map to relative paths beneath it."""

    def __init__(self, root: str | Path, *, signing_secret: str) -> None:
        self.root = Path(root).resolve()
        self._signing_secret = signing_secret

    # ---------------------------------------------------------------- paths

    def _paths(self, key: str) -> tuple[Path, Path]:
        validate_key(key)
        target = (self.root / key).resolve()
        # Belt and braces: validate_key already rejects traversal, but a symlink inside
        # the root could still point outside it.
        if not target.is_relative_to(self.root):
            raise InvalidKey(f"Blob key escapes the storage root: {key!r}")
        return target, target.with_name(target.name + METADATA_SUFFIX)

    # ---------------------------------------------------------------- writes

    def _write(self, key: str, content: bytes, content_type: str) -> BlobInfo:
        path, meta_path = self._paths(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        info = BlobInfo(
            key=key,
            size_bytes=len(content),
            content_type=content_type,
            checksum_sha256=checksum(content),
        )
        # Write to a temporary name first: a crash mid-write must not leave a truncated
        # dossier that looks complete.
        temporary = path.with_name(path.name + ".partial")
        temporary.write_bytes(content)
        temporary.replace(path)
        meta_path.write_text(
            json.dumps(
                {
                    "content_type": info.content_type,
                    "checksum_sha256": info.checksum_sha256,
                    "size_bytes": info.size_bytes,
                }
            ),
            encoding="utf-8",
        )
        return info

    async def put_bytes(self, key: str, content: bytes, content_type: str) -> BlobInfo:
        return await anyio.to_thread.run_sync(self._write, key, content, content_type)

    # ---------------------------------------------------------------- reads

    def _read(self, key: str) -> bytes:
        path, _ = self._paths(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise BlobNotFound(key) from exc

    async def get_bytes(self, key: str) -> bytes:
        return await anyio.to_thread.run_sync(self._read, key)

    def _stat(self, key: str) -> BlobInfo:
        path, meta_path = self._paths(key)
        if not path.exists():
            raise BlobNotFound(key)
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            # A blob written by something other than this store: describe what is there
            # rather than failing, but recompute the checksum instead of inventing one.
            content = path.read_bytes()
            return BlobInfo(
                key=key,
                size_bytes=len(content),
                content_type="application/octet-stream",
                checksum_sha256=checksum(content),
            )
        return BlobInfo(
            key=key,
            size_bytes=int(metadata["size_bytes"]),
            content_type=str(metadata["content_type"]),
            checksum_sha256=str(metadata["checksum_sha256"]),
        )

    async def stat(self, key: str) -> BlobInfo:
        return await anyio.to_thread.run_sync(self._stat, key)

    async def exists(self, key: str) -> bool:
        path, _ = self._paths(key)
        return await anyio.to_thread.run_sync(path.exists)

    # ---------------------------------------------------------------- delete

    def _delete(self, key: str) -> None:
        path, meta_path = self._paths(key)
        meta_path.unlink(missing_ok=True)
        path.unlink(missing_ok=True)

    async def delete(self, key: str) -> None:
        await anyio.to_thread.run_sync(self._delete, key)

    # ---------------------------------------------------------------- signing

    def _signature(self, key: str, expires_at: int) -> str:
        message = f"{key}:{expires_at}".encode()
        return hmac.new(self._signing_secret.encode(), message, hashlib.sha256).hexdigest()

    async def signed_url(self, key: str, expires_seconds: int) -> str:
        """A relative, HMAC-signed download URL.

        There is no storage service to sign for locally, so the signature is checked by
        the API's own download route (`verify_signature`). The point is that the same
        calling code works against Azure, where the SAS is signed by the service.
        """
        validate_key(key)
        expires_at = int(time.time()) + expires_seconds
        query = urlencode({"expires": expires_at, "signature": self._signature(key, expires_at)})
        return f"/blobs/{quote(key)}?{query}"

    def verify_signature(self, key: str, expires_at: int, signature: str) -> bool:
        if expires_at < int(time.time()):
            return False
        return hmac.compare_digest(self._signature(key, expires_at), signature)
