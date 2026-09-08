"""Versioned evidence artifacts use UTF-8, sorted keys, and no non-finite numbers."""

import hashlib
import json


def canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def payload_sha256(value: object) -> str:
    return text_sha256(canonical_json(value))


def text_identity(value: str) -> dict[str, object]:
    return {
        "characters": len(value),
        "utf8_bytes": len(value.encode("utf-8")),
        "sha256": text_sha256(value),
    }
