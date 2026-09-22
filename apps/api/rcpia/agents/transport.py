"""Lossless, readable synthesis transport. Canonical model dumps and source IDs do not change."""

import copy
import hashlib
import json
from collections import Counter
from typing import Any

FORMAT = "rcpia.task-bundle.transport.v1"
CHUNK_RULES = {
    "text_sha256": "SHA-256 of UTF-8 patient text[start:end]",
    "start": "0 for first, previous end + 2 otherwise",
    "ordinal": "array index unless explicit",
}
READING_HASH_RULE = "missing value_sha256 is the unchanged enclosing field_value_sha256"
DEFAULT_KEYS = {
    "case_ledger": frozenset({"unit", "status", "origin", "correction_note", "source_ids"}),
    "guideline_passages": frozenset({
        "source", "clinical_status", "section", "version", "published_at", "page", "page_end",
        "recommendation_number", "locator_method",
    }),
    "patient_chunks": frozenset({"page", "section", "block_type"}),
    "context_observations": frozenset({
        "field_value_sha256", "ledger_status", "authority", "value_basis", "relationship",
        "review_reasons",
    }),
    "context_readings": frozenset({
        "current_ledger_reading", "source_ids", "source_assertion_indices", "assessment",
        "normalized_stage", "reported_disease_phase", "organ", "organ_basis", "episode",
        "recorded_date", "date_basis", "specimen", "unresolved_dimensions", "competing_dimensions",
    }),
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Transport object expected")
    return value


def _objects(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("Transport object list expected")
    return value


def _integer(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Transport index must be a nonnegative integer")
    return value


def _table(records: list[dict[str, Any]], columns: tuple[str, ...]) -> dict[str, Any]:
    return {
        "columns": [*columns, "overrides"],
        "rows": [
            [*[record[key] for key in columns],
             {key: value for key, value in record.items() if key not in columns}]
            for record in records
        ],
    }


def _untable(value: Any, columns: tuple[str, ...]) -> list[dict[str, Any]]:
    table = _object(value)
    if set(table) != {"columns", "rows"} or table["columns"] != [*columns, "overrides"]:
        raise ValueError("Unknown or conflicting transport table columns")
    if not isinstance(table["rows"], list):
        raise ValueError("Transport rows must be a list")
    records = []
    for row in table["rows"]:
        if not isinstance(row, list) or len(row) != len(columns) + 1:
            raise ValueError("Invalid transport row width")
        overrides = _object(row[-1])
        if set(overrides) & set(columns):
            raise ValueError("Transport override conflicts with a named column")
        records.append({**dict(zip(columns, row[:-1], strict=True)), **overrides})
    return records


def _documents(inputs: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [
        (unit, document)
        for unit in _objects(inputs["patient_source_units"])
        for document in _objects(unit["documents"])
    ]


def _profiles(inputs: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    context = _object(inputs["patient_context"])
    observations = _objects(context["observations"])
    return {
        "case_ledger": _objects(inputs["case_ledger"]),
        "guideline_passages": _objects(inputs["guideline_passages"]),
        "patient_chunks": [
            chunk for _, document in _documents(inputs) for chunk in _objects(document["chunks"])
        ],
        "context_observations": observations,
        "context_readings": [
            reading for observation in observations for reading in _objects(observation["readings"])
        ],
    }


def _factor_defaults(records: list[dict[str, Any]], allowed: frozenset[str]) -> dict[str, Any]:
    if len(records) < 2:
        return {}
    defaults = {}
    for key in sorted(set.intersection(*(set(record) for record in records)) & allowed):
        counts = Counter(_json(record[key]) for record in records)
        encoded, count = counts.most_common(1)[0]
        value = json.loads(encoded)
        saving = count * (len(_json(key)) + len(encoded) + 2) - len(_json({key: value}))
        if count < 2 or saving <= 0:
            continue
        defaults[key] = value
        for record in records:
            if _json(record[key]) == encoded:
                del record[key]
    return defaults


def _restore_defaults(records: list[dict[str, Any]], defaults: Any, name: str) -> None:
    defaults = _object(defaults)
    if set(defaults) - DEFAULT_KEYS[name] or (defaults and not records):
        raise ValueError("Unsupported or unused transport defaults")
    for key, value in defaults.items():
        if all(key in record for record in records):
            raise ValueError("Unused transport default")
        for record in records:
            if key not in record:
                record[key] = copy.deepcopy(value)


def _segment_document(
    segment: dict[str, Any], context: dict[str, Any],
    documents: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    locations = _objects(context["source_locations"])
    index = _integer(segment["source_location_index"])
    if index >= len(locations):
        raise ValueError("Transport source location is out of range")
    location = locations[index]
    document = documents.get((location["source_id"], location["document_id"]))
    if document is None:
        raise ValueError("Transport source location is not supplied by the patient units")
    return document


def encode_synthesis(canonical: dict[str, Any]) -> dict[str, Any]:
    """Factor metadata only; clinical text, opinions, citations and output schema stay literal."""
    inputs = canonical["inputs"]
    if (
        inputs["agent_role"] != "rcp_synthesis"
        or not inputs.get("patient_source_units") or not inputs.get("patient_context")
    ):
        return canonical
    if "_transport" in canonical:
        raise ValueError("Canonical bundle cannot already contain transport metadata")
    encoded = copy.deepcopy(canonical)
    inputs = encoded["inputs"]
    context = inputs["patient_context"]
    documents = {
        (unit["id"], document["id"]): document for unit, document in _documents(inputs)
    }
    segments = []
    for segment in _objects(context["source_segments"]):
        document = _segment_document(segment, context, documents)
        matching = [
            index for index, chunk in enumerate(document["chunks"])
            if chunk["id"] == segment["chunk_id"]
        ]
        if len(matching) != 1:
            raise ValueError("Transport segment does not identify exactly one patient chunk")
        segments.append({
            "source_location_index": segment["source_location_index"],
            "patient_chunk_index": matching[0],
            **{key: value for key, value in segment.items()
               if key not in {"source_location_index", "chunk_id"}},
        })
    context["source_segments"] = _table(
        segments, ("source_location_index", "patient_chunk_index"),
    )
    for unit, document in _documents(inputs):
        start = 0
        for index, chunk in enumerate(document["chunks"]):
            end = _integer(chunk["end"])
            if (
                chunk["start"] != start or not start <= end <= len(unit["text"])
                or chunk["text_sha256"] != hashlib.sha256(
                    unit["text"][start:end].encode("utf-8")
                ).hexdigest()
            ):
                raise ValueError("Transport cannot repair inconsistent patient source metadata")
            del chunk["start"], chunk["text_sha256"]
            if chunk["ordinal"] == index:
                del chunk["ordinal"]
            start = end + 2
    for observation in context["observations"]:
        for reading in observation["readings"]:
            if reading["value_sha256"] == observation["field_value_sha256"]:
                del reading["value_sha256"]
    defaults = {
        name: _factor_defaults(records, DEFAULT_KEYS[name])
        for name, records in _profiles(inputs).items()
    }
    for _, document in _documents(inputs):
        document["chunks"] = _table(document["chunks"], ("id", "end"))
    inputs["case_ledger"] = _table(inputs["case_ledger"], ("key", "label", "value"))
    context["observations"] = _table(context["observations"], ("field_key", "readings"))
    encoded["_transport"] = {
        "format": FORMAT, "canonical_sha256": canonical_sha256(canonical), "defaults": defaults,
        "derived_patient_chunk_fields": dict(CHUNK_RULES), "reading_hash": READING_HASH_RULE,
    }
    if decode_transport(encoded) != canonical:
        raise ValueError("Transport round-trip changed canonical bundle content")
    return encoded


def decode_transport(value: Any) -> Any:
    """Accept expanded historical bundles or the versioned self-contained transport."""
    if not isinstance(value, dict) or "_transport" not in value:
        return value
    try:
        return _decode_compact(value)
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Malformed task-bundle transport structure") from exc


def _decode_compact(value: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(value)
    metadata = _object(data.pop("_transport"))
    if set(metadata) != {
        "format", "canonical_sha256", "defaults", "derived_patient_chunk_fields", "reading_hash",
    } or metadata["format"] != FORMAT:
        raise ValueError("Unknown task-bundle transport")
    if (
        metadata["derived_patient_chunk_fields"] != CHUNK_RULES
        or metadata["reading_hash"] != READING_HASH_RULE
    ):
        raise ValueError("Conflicting transport derivation rules")
    inputs = _object(data["inputs"])
    if inputs["agent_role"] != "rcp_synthesis":
        raise ValueError("This transport is scoped to synthesis")
    context = _object(inputs["patient_context"])
    inputs["case_ledger"] = _untable(inputs["case_ledger"], ("key", "label", "value"))
    context["observations"] = _untable(context["observations"], ("field_key", "readings"))
    for _, document in _documents(inputs):
        document["chunks"] = _untable(document["chunks"], ("id", "end"))
    defaults = _object(metadata["defaults"])
    if set(defaults) != set(DEFAULT_KEYS):
        raise ValueError("Transport defaults must name exactly the supported profiles")
    for name, records in _profiles(inputs).items():
        _restore_defaults(records, defaults[name], name)
    for unit, document in _documents(inputs):
        if not isinstance(unit["text"], str):
            raise ValueError("Patient text must stay literal")
        start = 0
        for index, chunk in enumerate(document["chunks"]):
            if "start" in chunk or "text_sha256" in chunk:
                raise ValueError("Conflicting derived patient chunk fields")
            end = _integer(chunk["end"])
            if not start <= end <= len(unit["text"]):
                raise ValueError("Patient chunk bounds escape the supplied complete text")
            chunk["ordinal"] = _integer(chunk.get("ordinal", index))
            chunk["start"] = start
            chunk["text_sha256"] = hashlib.sha256(
                unit["text"][start:end].encode("utf-8")
            ).hexdigest()
            start = end + 2
    for observation in context["observations"]:
        for reading in observation["readings"]:
            if "value_sha256" not in reading:
                reading["value_sha256"] = observation["field_value_sha256"]
    documents = {
        (unit["id"], document["id"]): document for unit, document in _documents(inputs)
    }
    segments = _untable(
        context["source_segments"], ("source_location_index", "patient_chunk_index"),
    )
    for segment in segments:
        if "chunk_id" in segment:
            raise ValueError("Conflicting patient chunk reference")
        document = _segment_document(segment, context, documents)
        index = _integer(segment.pop("patient_chunk_index"))
        if index >= len(document["chunks"]):
            raise ValueError("Patient chunk reference is out of range")
        segment["chunk_id"] = document["chunks"][index]["id"]
    context["source_segments"] = segments
    if canonical_sha256(data) != metadata["canonical_sha256"]:
        raise ValueError("Transport canonical bundle hash mismatch")
    return data
