"""Local filesystem blob store, for development and on-premises deployments.

Each blob is a **directory** containing its body and its metadata:

    <root>/objects/<key>/data
    <root>/objects/<key>/meta.json

That layout buys two properties a plain file plus a sidecar cannot:

- **Body and metadata become visible together.** Both are written into a staging
  directory and published with a single rename, so a reader never sees new bytes
  described by stale metadata.
- **Metadata cannot collide with a blob.** With sidecars, a blob legitimately named
  `a.meta.json` overwrites the metadata of blob `a`. Here every key owns its directory.

Publication is `rename(staging, object_dir)`, which fails when the destination exists and
is non-empty — so a second write of the same key is refused rather than silently
overwriting a stored dossier, on both POSIX and Windows.
"""

import hashlib
import hmac
import json
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlencode

import anyio.to_thread

from rcpia.storage.base import (
    BlobAlreadyExists,
    BlobInfo,
    BlobNotFound,
    InvalidKey,
    checksum,
    validate_key,
)

OBJECTS_DIRNAME = "objects"
STAGING_DIRNAME = ".staging"
BODY_FILENAME = "data"
METADATA_FILENAME = "meta.json"


class LocalBlobStore:
    """Blob directories under `root`."""

    def __init__(self, root: str | Path, *, signing_secret: str) -> None:
        self.root = Path(root).resolve()
        self._objects = self.root / OBJECTS_DIRNAME
        self._staging = self.root / STAGING_DIRNAME
        self._signing_secret = signing_secret

    # ---------------------------------------------------------------- paths

    def _object_dir(self, key: str) -> Path:
        validate_key(key)
        target = (self._objects / key).resolve()
        # validate_key already rejects traversal; this also catches a symlink component
        # pointing outside the store.
        if not target.is_relative_to(self._objects.resolve()):
            raise InvalidKey(f"Blob key escapes the storage root: {key!r}")
        return target

    # ---------------------------------------------------------------- writes

    def _write(self, key: str, content: bytes, content_type: str) -> BlobInfo:
        object_dir = self._object_dir(key)
        if (object_dir / BODY_FILENAME).exists():
            raise BlobAlreadyExists(key)

        info = BlobInfo(
            key=key,
            size_bytes=len(content),
            content_type=content_type,
            checksum_sha256=checksum(content),
        )

        # A unique staging directory per write: two concurrent writers never share one.
        staging = self._staging / uuid.uuid4().hex
        staging.mkdir(parents=True)
        try:
            (staging / BODY_FILENAME).write_bytes(content)
            (staging / METADATA_FILENAME).write_text(
                json.dumps(
                    {
                        "content_type": info.content_type,
                        "checksum_sha256": info.checksum_sha256,
                        "size_bytes": info.size_bytes,
                    }
                ),
                encoding="utf-8",
            )
            object_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                # One atomic step publishes body and metadata together, and refuses to
                # replace an existing blob.
                staging.rename(object_dir)
            except OSError as exc:
                raise BlobAlreadyExists(key) from exc
        finally:
            # A successful rename leaves nothing to clean up; a failure must not leak.
            shutil.rmtree(staging, ignore_errors=True)

        return info

    async def put_bytes(self, key: str, content: bytes, content_type: str) -> BlobInfo:
        return await anyio.to_thread.run_sync(self._write, key, content, content_type)

    # ---------------------------------------------------------------- reads

    def _read(self, key: str) -> bytes:
        try:
            return (self._object_dir(key) / BODY_FILENAME).read_bytes()
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise BlobNotFound(key) from exc

    async def get_bytes(self, key: str) -> bytes:
        return await anyio.to_thread.run_sync(self._read, key)

    def _stat(self, key: str) -> BlobInfo:
        object_dir = self._object_dir(key)
        body = object_dir / BODY_FILENAME
        if not body.exists():
            raise BlobNotFound(key)

        try:
            metadata = json.loads((object_dir / METADATA_FILENAME).read_text(encoding="utf-8"))
            return BlobInfo(
                key=key,
                size_bytes=int(metadata["size_bytes"]),
                content_type=str(metadata["content_type"]),
                checksum_sha256=str(metadata["checksum_sha256"]),
            )
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
            # Unreadable metadata: describe the bytes that are actually there rather
            # than reporting something that was never true.
            content = body.read_bytes()
            return BlobInfo(
                key=key,
                size_bytes=len(content),
                content_type="application/octet-stream",
                checksum_sha256=checksum(content),
            )

    async def stat(self, key: str) -> BlobInfo:
        return await anyio.to_thread.run_sync(self._stat, key)

    def _exists(self, key: str) -> bool:
        return (self._object_dir(key) / BODY_FILENAME).exists()

    async def exists(self, key: str) -> bool:
        return await anyio.to_thread.run_sync(self._exists, key)

    # ---------------------------------------------------------------- delete

    def _delete(self, key: str) -> None:
        shutil.rmtree(self._object_dir(key), ignore_errors=True)

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
