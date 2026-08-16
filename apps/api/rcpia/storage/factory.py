"""Blob store selection.

`BLOB_PROVIDER` decides which backend the application uses; nothing above this module
names a provider.
"""

from functools import lru_cache

from rcpia.core.config import Settings, get_settings
from rcpia.storage.azure_blob import AzureBlobStore
from rcpia.storage.base import BlobStore, StorageError
from rcpia.storage.local import LocalBlobStore


def build_blob_store(settings: Settings) -> BlobStore:
    if settings.blob_provider == "local":
        return LocalBlobStore(settings.local_blob_root, signing_secret=settings.auth_secret)
    if settings.blob_provider == "azure":
        return AzureBlobStore(
            settings.azure_storage_connection_string, settings.azure_storage_container
        )
    raise StorageError(f"Unknown BLOB_PROVIDER: {settings.blob_provider!r}")


@lru_cache
def get_blob_store() -> BlobStore:
    """FastAPI dependency. Cached, since backends are safe to share."""
    return build_blob_store(get_settings())
