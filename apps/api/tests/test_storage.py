"""Blob storage tests.

The local store is exercised for real against a temporary directory. The Azure adapter is
exercised against a fake container client: that covers the mapping this code is
responsible for — immutability, content type, checksum metadata, error translation —
without pretending we have verified Azure itself. End-to-end Azure verification belongs
with the deployment work in Phase 14.
"""

import json
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from rcpia.core.config import Settings
from rcpia.storage import keys
from rcpia.storage.azure_blob import CHECKSUM_METADATA_KEY, AzureBlobStore
from rcpia.storage.base import (
    BlobNotFound,
    InvalidKey,
    StorageError,
    checksum,
    validate_key,
)
from rcpia.storage.factory import build_blob_store
from rcpia.storage.local import LocalBlobStore

PDF = b"%PDF-1.7\nfake dossier bytes\n"
CASE_ID = uuid.UUID("11111111-2222-4333-8444-555555555555")


@pytest.fixture
def store(tmp_path: Path) -> LocalBlobStore:
    return LocalBlobStore(tmp_path, signing_secret="test-signing-secret")


# --------------------------------------------------------------------------------------
# Key validation
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "cases/abc/dossiers/1.pdf",
        "evidence/guidelines/eau-2026.pdf",
        "a",
    ],
)
def test_valid_keys_are_accepted(key: str) -> None:
    assert validate_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/absolute/path",
        "cases/../../etc/passwd",
        "cases/./dossier.pdf",
        "trailing/slash/",
        "back\\slash",
        "with space.pdf",
        "..",
    ],
)
def test_dangerous_keys_are_rejected(key: str) -> None:
    with pytest.raises(InvalidKey):
        validate_key(key)


async def test_traversal_cannot_escape_the_storage_root(store: LocalBlobStore) -> None:
    with pytest.raises(InvalidKey):
        await store.put_bytes("../escaped.pdf", PDF, "application/pdf")


# --------------------------------------------------------------------------------------
# Local store
# --------------------------------------------------------------------------------------


async def test_put_and_get_round_trip(store: LocalBlobStore) -> None:
    key = "cases/abc/dossiers/one.pdf"

    info = await store.put_bytes(key, PDF, "application/pdf")

    assert info.key == key
    assert info.size_bytes == len(PDF)
    assert info.content_type == "application/pdf"
    assert info.checksum_sha256 == checksum(PDF)
    assert await store.get_bytes(key) == PDF


async def test_checksum_and_content_type_survive_a_restart(tmp_path: Path) -> None:
    """Metadata is on disk, not in memory: a new process must see the same facts."""
    key = "cases/abc/dossiers/two.docx"
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    first = LocalBlobStore(tmp_path, signing_secret="s")
    written = await first.put_bytes(key, PDF, docx)

    second = LocalBlobStore(tmp_path, signing_secret="s")
    read_back = await second.stat(key)

    assert read_back == written
    assert read_back.content_type == docx


async def test_stat_of_a_missing_blob_raises(store: LocalBlobStore) -> None:
    with pytest.raises(BlobNotFound):
        await store.stat("cases/abc/dossiers/absent.pdf")


async def test_get_of_a_missing_blob_raises(store: LocalBlobStore) -> None:
    with pytest.raises(BlobNotFound):
        await store.get_bytes("cases/abc/dossiers/absent.pdf")


async def test_exists_and_delete(store: LocalBlobStore) -> None:
    key = "cases/abc/dossiers/three.pdf"
    assert not await store.exists(key)

    await store.put_bytes(key, PDF, "application/pdf")
    assert await store.exists(key)

    await store.delete(key)
    assert not await store.exists(key)
    await store.delete(key)  # deleting twice is not an error


async def test_nested_keys_create_their_directories(store: LocalBlobStore, tmp_path: Path) -> None:
    await store.put_bytes("cases/a/b/c/deep.pdf", PDF, "application/pdf")

    assert (tmp_path / "cases/a/b/c/deep.pdf").exists()


async def test_no_partial_file_is_left_behind(store: LocalBlobStore, tmp_path: Path) -> None:
    await store.put_bytes("cases/abc/dossiers/four.pdf", PDF, "application/pdf")

    assert list(tmp_path.rglob("*.partial")) == []


async def test_metadata_sidecar_is_readable_json(store: LocalBlobStore, tmp_path: Path) -> None:
    await store.put_bytes("cases/abc/dossiers/five.pdf", PDF, "application/pdf")

    sidecar = tmp_path / "cases/abc/dossiers/five.pdf.meta.json"
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))

    assert metadata["checksum_sha256"] == checksum(PDF)
    assert metadata["content_type"] == "application/pdf"
    assert metadata["size_bytes"] == len(PDF)


async def test_stat_falls_back_when_metadata_is_missing(
    store: LocalBlobStore, tmp_path: Path
) -> None:
    """A file put there by hand still describes itself honestly."""
    key = "cases/abc/dossiers/six.pdf"
    await store.put_bytes(key, PDF, "application/pdf")
    (tmp_path / f"{key}.meta.json").unlink()

    info = await store.stat(key)

    assert info.checksum_sha256 == checksum(PDF), "recomputed, not invented"
    assert info.content_type == "application/octet-stream"


# --------------------------------------------------------------------------------------
# Signed URLs
# --------------------------------------------------------------------------------------


async def test_signed_url_verifies_and_expires(store: LocalBlobStore) -> None:
    key = "cases/abc/dossiers/seven.pdf"
    await store.put_bytes(key, PDF, "application/pdf")

    url = await store.signed_url(key, expires_seconds=60)
    query = parse_qs(urlparse(url).query)
    expires_at = int(query["expires"][0])
    signature = query["signature"][0]

    assert store.verify_signature(key, expires_at, signature)
    assert not store.verify_signature("cases/abc/dossiers/other.pdf", expires_at, signature)
    assert not store.verify_signature(key, expires_at, "0" * 64)
    assert not store.verify_signature(key, int(time.time()) - 1, signature)


async def test_signed_url_signature_depends_on_the_secret(tmp_path: Path) -> None:
    key = "cases/abc/dossiers/eight.pdf"
    mine = LocalBlobStore(tmp_path, signing_secret="secret-one")
    theirs = LocalBlobStore(tmp_path, signing_secret="secret-two")
    await mine.put_bytes(key, PDF, "application/pdf")

    url = await mine.signed_url(key, expires_seconds=60)
    query = parse_qs(urlparse(url).query)

    assert not theirs.verify_signature(
        key, int(query["expires"][0]), query["signature"][0]
    ), "a forged signature from another secret must not verify"


# --------------------------------------------------------------------------------------
# Key layout
# --------------------------------------------------------------------------------------


def test_dossier_key_ignores_the_uploaded_filename() -> None:
    document_id = uuid.uuid4()

    key = keys.dossier_key(CASE_ID, document_id, "../../etc/passwd.pdf")

    assert key == f"cases/{CASE_ID}/dossiers/{document_id}.pdf"
    assert validate_key(key) == key


def test_dossier_key_drops_an_unrecognizable_extension() -> None:
    document_id = uuid.uuid4()

    key = keys.dossier_key(CASE_ID, document_id, "dossier.this-is-not-an-extension")

    assert key == f"cases/{CASE_ID}/dossiers/{document_id}"


def test_session_and_pps_keys_are_valid() -> None:
    run_id, package_id = uuid.uuid4(), uuid.uuid4()

    session_key = keys.pi_session_key(CASE_ID, "urology_oncology", run_id)
    package_key = keys.pps_key(CASE_ID, package_id, "docx")

    assert session_key == f"cases/{CASE_ID}/sessions/urology_oncology/{run_id}.jsonl"
    assert package_key == f"cases/{CASE_ID}/pps/{package_id}.docx"
    assert validate_key(session_key) and validate_key(package_key)


def test_unsafe_agent_names_are_refused() -> None:
    with pytest.raises(ValueError):
        keys.pi_session_key(CASE_ID, "../escape", uuid.uuid4())


# --------------------------------------------------------------------------------------
# Provider selection
# --------------------------------------------------------------------------------------


def test_provider_is_chosen_by_configuration(tmp_path: Path) -> None:
    local = build_blob_store(Settings(blob_provider="local", local_blob_root=str(tmp_path)))
    azure = build_blob_store(
        Settings(
            blob_provider="azure",
            azure_storage_connection_string="DefaultEndpointsProtocol=https;AccountName=a;AccountKey=k;EndpointSuffix=core.windows.net",
            azure_storage_container="dossiers",
        )
    )

    assert isinstance(local, LocalBlobStore)
    assert isinstance(azure, AzureBlobStore)


def test_azure_provider_without_a_connection_string_fails_loudly() -> None:
    with pytest.raises(StorageError, match="AZURE_STORAGE_CONNECTION_STRING"):
        build_blob_store(Settings(blob_provider="azure"))


# --------------------------------------------------------------------------------------
# Azure adapter, against a fake container client
# --------------------------------------------------------------------------------------


class FakeBlobClient:
    def __init__(self, container: "FakeContainerClient", name: str) -> None:
        self._container = container
        self._name = name
        self.url = f"https://account.blob.core.windows.net/{container.name}/{name}"

    async def get_blob_properties(self) -> Any:
        from azure.core.exceptions import ResourceNotFoundError

        if self._name not in self._container.blobs:
            raise ResourceNotFoundError(self._name)
        return self._container.blobs[self._name]["properties"]


class FakeContainerClient:
    def __init__(self) -> None:
        self.name = "rcpia"
        self.account_name = "account"
        self.credential = type("Credential", (), {"account_key": "a2V5"})()
        self.blobs: dict[str, dict[str, Any]] = {}
        self.upload_calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> "FakeContainerClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def upload_blob(self, name: str, data: bytes, **kwargs: Any) -> None:
        from azure.core.exceptions import ResourceExistsError

        if name in self.blobs and not kwargs.get("overwrite", False):
            raise ResourceExistsError(name)

        self.upload_calls.append({"name": name, **kwargs})
        content_settings = kwargs["content_settings"]
        self.blobs[name] = {
            "data": data,
            "properties": type(
                "Properties",
                (),
                {
                    "size": len(data),
                    "content_settings": content_settings,
                    "metadata": kwargs.get("metadata") or {},
                },
            )(),
        }

    async def download_blob(self, name: str) -> Any:
        from azure.core.exceptions import ResourceNotFoundError

        if name not in self.blobs:
            raise ResourceNotFoundError(name)
        payload = self.blobs[name]["data"]
        return type("Downloader", (), {"readall": staticmethod(lambda: _async(payload))})()

    async def delete_blob(self, name: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        if name not in self.blobs:
            raise ResourceNotFoundError(name)
        del self.blobs[name]

    def get_blob_client(self, name: str) -> FakeBlobClient:
        return FakeBlobClient(self, name)


async def _async(value: Any) -> Any:
    return value


@pytest.fixture
def azure_store(monkeypatch: pytest.MonkeyPatch) -> tuple[AzureBlobStore, FakeContainerClient]:
    pytest.importorskip("azure.storage.blob", reason="azure extra not installed")
    container = FakeContainerClient()
    store = AzureBlobStore("AccountName=account;AccountKey=a2V5", "rcpia")
    monkeypatch.setattr(store, "_container_client", lambda: container)
    return store, container


async def test_azure_put_records_content_type_and_checksum(
    azure_store: tuple[AzureBlobStore, FakeContainerClient],
) -> None:
    store, container = azure_store
    key = "cases/abc/dossiers/nine.pdf"

    info = await store.put_bytes(key, PDF, "application/pdf")

    call = container.upload_calls[0]
    assert call["content_settings"].content_type == "application/pdf"
    assert call["metadata"][CHECKSUM_METADATA_KEY] == checksum(PDF)
    assert info.checksum_sha256 == checksum(PDF)


async def test_azure_blobs_are_immutable(
    azure_store: tuple[AzureBlobStore, FakeContainerClient],
) -> None:
    """A correction must write a new key, never overwrite a stored dossier."""
    from azure.core.exceptions import ResourceExistsError

    store, container = azure_store
    key = "cases/abc/dossiers/ten.pdf"
    await store.put_bytes(key, PDF, "application/pdf")

    assert container.upload_calls[0]["overwrite"] is False
    with pytest.raises(ResourceExistsError):
        await store.put_bytes(key, b"different bytes", "application/pdf")


async def test_azure_round_trip_and_stat(
    azure_store: tuple[AzureBlobStore, FakeContainerClient],
) -> None:
    store, _ = azure_store
    key = "cases/abc/dossiers/eleven.pdf"
    await store.put_bytes(key, PDF, "application/pdf")

    assert await store.get_bytes(key) == PDF
    info = await store.stat(key)
    assert info.size_bytes == len(PDF)
    assert info.content_type == "application/pdf"
    assert info.checksum_sha256 == checksum(PDF)
    assert await store.exists(key)


async def test_azure_missing_blob_raises_the_shared_error(
    azure_store: tuple[AzureBlobStore, FakeContainerClient],
) -> None:
    """Callers handle BlobNotFound, not an SDK-specific exception."""
    store, _ = azure_store

    with pytest.raises(BlobNotFound):
        await store.get_bytes("cases/abc/dossiers/absent.pdf")
    with pytest.raises(BlobNotFound):
        await store.stat("cases/abc/dossiers/absent.pdf")
    assert not await store.exists("cases/abc/dossiers/absent.pdf")


async def test_azure_delete_is_idempotent(
    azure_store: tuple[AzureBlobStore, FakeContainerClient],
) -> None:
    store, _ = azure_store
    key = "cases/abc/dossiers/twelve.pdf"
    await store.put_bytes(key, PDF, "application/pdf")

    await store.delete(key)
    await store.delete(key)

    assert not await store.exists(key)


async def test_azure_signed_url_is_a_read_only_sas(
    azure_store: tuple[AzureBlobStore, FakeContainerClient],
) -> None:
    store, _ = azure_store
    key = "cases/abc/dossiers/thirteen.pdf"
    await store.put_bytes(key, PDF, "application/pdf")

    url = await store.signed_url(key, expires_seconds=300)

    query = parse_qs(urlparse(url).query)
    assert url.startswith("https://account.blob.core.windows.net/rcpia/")
    assert query["sp"] == ["r"], "read permission only"
    assert "sig" in query and "se" in query
