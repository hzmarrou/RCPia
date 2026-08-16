"""Azure Blob Storage backend.

The Azure SDK is imported lazily so that a local or on-premises deployment — which is the
expected production shape for RCPia — does not need the dependency installed at all.

Checksums are stored as blob metadata rather than recomputed on read: re-downloading a
dossier only to hash it would defeat the point of `stat()`.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from rcpia.storage.base import BlobInfo, BlobNotFound, StorageError, checksum, validate_key

if TYPE_CHECKING:  # pragma: no cover - typing only
    from azure.storage.blob.aio import ContainerClient

CHECKSUM_METADATA_KEY = "sha256"


class AzureBlobStore:
    def __init__(self, connection_string: str, container: str) -> None:
        if not connection_string:
            raise StorageError(
                "BLOB_PROVIDER=azure requires AZURE_STORAGE_CONNECTION_STRING to be set"
            )
        self._connection_string = connection_string
        self._container = container

    def _container_client(self) -> "ContainerClient":
        try:
            from azure.storage.blob.aio import BlobServiceClient
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on install extras
            raise StorageError(
                "BLOB_PROVIDER=azure requires the azure-storage-blob package. "
                "Install the 'azure' extra."
            ) from exc

        service = BlobServiceClient.from_connection_string(self._connection_string)
        return service.get_container_client(self._container)

    async def put_bytes(self, key: str, content: bytes, content_type: str) -> BlobInfo:
        from azure.storage.blob import ContentSettings

        validate_key(key)
        digest = checksum(content)

        async with self._container_client() as container:
            await container.upload_blob(
                name=key,
                data=content,
                overwrite=False,  # blobs are immutable; a correction gets a new key
                content_settings=ContentSettings(content_type=content_type),
                metadata={CHECKSUM_METADATA_KEY: digest},
            )

        return BlobInfo(
            key=key, size_bytes=len(content), content_type=content_type, checksum_sha256=digest
        )

    async def get_bytes(self, key: str) -> bytes:
        from azure.core.exceptions import ResourceNotFoundError

        validate_key(key)
        async with self._container_client() as container:
            try:
                downloader = await container.download_blob(key)
                return await downloader.readall()
            except ResourceNotFoundError as exc:
                raise BlobNotFound(key) from exc

    async def stat(self, key: str) -> BlobInfo:
        from azure.core.exceptions import ResourceNotFoundError

        validate_key(key)
        async with self._container_client() as container:
            try:
                properties: Any = await container.get_blob_client(key).get_blob_properties()
            except ResourceNotFoundError as exc:
                raise BlobNotFound(key) from exc

        metadata = properties.metadata or {}
        return BlobInfo(
            key=key,
            size_bytes=int(properties.size),
            content_type=str(properties.content_settings.content_type or ""),
            checksum_sha256=str(metadata.get(CHECKSUM_METADATA_KEY, "")),
        )

    async def exists(self, key: str) -> bool:
        try:
            await self.stat(key)
        except BlobNotFound:
            return False
        return True

    async def delete(self, key: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        validate_key(key)
        async with self._container_client() as container:
            try:
                await container.delete_blob(key)
            except ResourceNotFoundError:
                return

    async def signed_url(self, key: str, expires_seconds: int) -> str:
        """A read-only SAS URL, signed by the storage account key."""
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        validate_key(key)
        async with self._container_client() as container:
            account_name = container.account_name
            credential: Any = container.credential
            account_key = getattr(credential, "account_key", None)
            blob_url = container.get_blob_client(key).url

        if not account_key:
            raise StorageError(
                "Signed URLs need an account key in AZURE_STORAGE_CONNECTION_STRING"
            )

        sas = generate_blob_sas(
            account_name=account_name,
            container_name=self._container,
            blob_name=key,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(UTC) + timedelta(seconds=expires_seconds),
        )
        return f"{blob_url}?{sas}"
