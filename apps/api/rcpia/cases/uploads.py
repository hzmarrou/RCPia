"""Upload validation.

Only `.docx` and `.pdf` dossiers are accepted, and the check does not trust the filename
or the browser-supplied content type: both are attacker-controlled in the general case,
and simply wrong often enough in the ordinary case. The magic bytes decide.
"""

from dataclasses import dataclass

PDF_CONTENT_TYPE = "application/pdf"
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# 50 MB. Real dossiers with imaging reports run to a few megabytes; this leaves room
# without letting one upload exhaust the API container's memory.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class UploadRejected(Exception):
    """The uploaded file is not an acceptable dossier."""


@dataclass(frozen=True)
class DetectedType:
    content_type: str
    extension: str


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

    # .docx is a zip whose first entry is normally [Content_Types].xml. Checking the zip
    # signature plus that marker keeps arbitrary archives out.
    if content.startswith((b"PK\x03\x04", b"PK\x05\x06")) and b"[Content_Types].xml" in content[
        :4096
    ]:
        return DetectedType(DOCX_CONTENT_TYPE, ".docx")

    lowered = filename.lower()
    if lowered.endswith((".doc", ".rtf", ".odt")):
        raise UploadRejected(
            "Format non pris en charge. Convertissez le dossier en .docx ou .pdf."
        )
    raise UploadRejected("Seuls les fichiers .docx et .pdf sont acceptés.")
