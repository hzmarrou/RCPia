"""Upload validation.

Only `.docx` and `.pdf` dossiers are accepted, and the check does not trust the filename
or the browser-supplied content type: both are attacker-controlled in the general case,
and simply wrong often enough in the ordinary case. The magic bytes decide.
"""

import io
import zipfile
from dataclasses import dataclass

PDF_CONTENT_TYPE = "application/pdf"
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# 50 MB. Real dossiers with imaging reports run to a few megabytes; this leaves room
# without letting one upload exhaust the API container's memory.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class UploadRejected(Exception):
    """The uploaded file is not an acceptable dossier."""


# A .docx of a clinical dossier has tens of entries, not thousands. The cap bounds the
# work done on a hostile archive; entry names are read from the central directory, so
# nothing is decompressed to make this decision.
MAX_ZIP_ENTRIES = 2000
# Refuse archives claiming to expand far beyond the upload limit (zip bomb).
MAX_DECLARED_EXPANSION = 200


@dataclass(frozen=True)
class DetectedType:
    content_type: str
    extension: str


def _is_docx(content: bytes) -> bool:
    """Decide from the archive's structure, not from a substring of the first few KB.

    A valid .docx can carry another entry before `[Content_Types].xml`, and a file that
    merely contains that string somewhere is not an Office document at all.
    """
    if not content.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return False

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_ZIP_ENTRIES:
                return False

            declared = sum(entry.file_size for entry in entries)
            if declared > len(content) * MAX_DECLARED_EXPANSION:
                return False

            names = {entry.filename for entry in entries}
    except (zipfile.BadZipFile, OSError, ValueError):
        return False

    # WordprocessingML: the content-type map plus the main document part.
    return "[Content_Types].xml" in names and any(
        name.startswith("word/") for name in names
    )


def detect_dossier_type(filename: str, content: bytes) -> DetectedType:
    """Return the real type of an uploaded dossier, or raise UploadRejected."""
    if not content:
        raise UploadRejected("Le fichier est vide.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise UploadRejected(
            f"Le fichier dépasse la taille maximale de {MAX_UPLOAD_BYTES // (1024 * 1024)} Mo."
        )

    if content.startswith(b"%PDF-"):
        return DetectedType(PDF_CONTENT_TYPE, ".pdf")

    if _is_docx(content):
        return DetectedType(DOCX_CONTENT_TYPE, ".docx")

    lowered = filename.lower()
    if lowered.endswith((".doc", ".rtf", ".odt")):
        raise UploadRejected(
            "Format non pris en charge. Convertissez le dossier en .docx ou .pdf."
        )
    raise UploadRejected("Seuls les fichiers .docx et .pdf sont acceptés.")
