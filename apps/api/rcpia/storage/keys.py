"""Blob key layout.

Keys are derived from identifiers, never from user-supplied filenames: a dossier is
called `dossier.pdf` on someone's desktop and may be named anything at all, while the
key must stay predictable, unique, and free of anything resembling a path. The original
filename is kept in `uploaded_documents.original_filename`, where it is data rather than
a path.

Layout:

    cases/<case_id>/dossiers/<document_id><ext>
    cases/<case_id>/sessions/<agent>/<run_id>.jsonl
    cases/<case_id>/pps/<package_id>.<ext>
"""

import posixpath
import re
import uuid

SAFE_EXTENSION = re.compile(r"^\.[A-Za-z0-9]{1,10}$")
SAFE_AGENT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,49}$")


def _extension(filename: str) -> str:
    """Keep only a recognizable extension, and only its shape — never the name."""
    extension = posixpath.splitext(filename)[1].lower()
    return extension if SAFE_EXTENSION.match(extension) else ""


def dossier_key(case_id: uuid.UUID, document_id: uuid.UUID, original_filename: str) -> str:
    return f"cases/{case_id}/dossiers/{document_id}{_extension(original_filename)}"


def pi_session_key(case_id: uuid.UUID, agent_name: str, run_id: uuid.UUID) -> str:
    """Pi session logs are audit artifacts, stored per case and per run."""
    if not SAFE_AGENT_NAME.match(agent_name):
        raise ValueError(f"Unsafe agent name for a blob key: {agent_name!r}")
    return f"cases/{case_id}/sessions/{agent_name}/{run_id}.jsonl"


def pps_key(case_id: uuid.UUID, package_id: uuid.UUID, extension: str = "pdf") -> str:
    normalized = extension.lstrip(".").lower()
    if not normalized.isalnum():
        raise ValueError(f"Unsafe extension for a blob key: {extension!r}")
    return f"cases/{case_id}/pps/{package_id}.{normalized}"
