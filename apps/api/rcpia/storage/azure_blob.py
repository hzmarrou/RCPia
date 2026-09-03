"""Azure Blob Storage backend.

The Azure SDK is imported lazily so that a local or on-premises deployment — which is the
expected production shape for RCPia — does not need the dependency installed at all.

Checksums are stored as blob metadata rather than recomputed on read: re-downloading a
dossier only to hash it would defeat the point of `stat()`.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from rcpia.storage.base import (
    BlobAlreadyExists,
    BlobInfo,
    BlobNotFound,
    StorageError,
    checksum,
    validate_key,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from azure.storage.blob.aio import ContainerClient

CHECKSUM_METADATA_KEY = "sha256"


class AzureBlobStore:
    """Blob storage reached either by account key or by managed identity.

    **Managed identity is not a hardening nicety here; for some tenants it is the only way
    in.** A subscription policy can forbid shared-key access entirely, and then a connection
    string fails at the first write with

        KeyBasedAuthenticationNotPermitted: Key based authentication is not permitted on
        this storage account.

    Measured on the first Azure deployment: `allowSharedKeyAccess` was already `false` on a
    freshly created account, and setting it to `true` silently returned `false` again, because
    the policy and not the account owns that decision.

    So an account URL with a credential is preferred where one is given, and the connection
    string remains for local emulators and for tenants that still permit keys.
    """

    def __init__(
        self,
        connection_string: str,
        container: str,
        account_url: str = "",
    ) -> None:
        if not connection_string and not account_url:
            raise StorageError(
                "BLOB_PROVIDER=azure requires AZURE_STORAGE_ACCOUNT_URL (managed identity) "
                "or AZURE_STORAGE_CONNECTION_STRING (account key)"
            )
        self._connection_string = connection_string
        self._account_url = account_url
        self._container = container

    def _container_client(self) -> "ContainerClient":
        try:
            from azure.storage.blob.aio import BlobServiceClient
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on install extras
            raise StorageError(
                "BLOB_PROVIDER=azure requires the azure-storage-blob package. "
                "Install the 'azure' extra."
            ) from exc

        if self._account_url:
            try:
                from azure.identity.aio import DefaultAzureCredential
            except ModuleNotFoundError as exc:  # pragma: no cover - depends on extras
                raise StorageError(
                    "AZURE_STORAGE_ACCOUNT_URL needs the azure-identity package. "
                    "Install the 'azure' extra."
                ) from exc
            # Resolves the container app's managed identity in Azure, and a developer's
            # `az login` locally, without either being named here.
            service = BlobServiceClient(
                account_url=self._account_url, credential=DefaultAzureCredential()
            )
        else:
            service = BlobServiceClient.from_connection_string(self._connection_string)
        return service.get_container_client(self._container)

    async def put_bytes(self, key: str, content: bytes, content_type: str) -> BlobInfo:
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import ContentSettings

        validate_key(key)
        digest = checksum(content)

        async with self._container_client() as container:
            try:
                await container.upload_blob(
                    name=key,
                    data=content,
                    overwrite=False,  # blobs are immutable; a correction gets a new key
                    content_settings=ContentSettings(content_type=content_type),
                    metadata={CHECKSUM_METADATA_KEY: digest},
                )
            except ResourceExistsError as exc:
                # Callers handle storage errors, never SDK types.
                raise BlobAlreadyExists(key) from exc

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
