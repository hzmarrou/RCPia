"""Strict JSON at request and artifact boundaries."""

import json


def load_json(raw: str | bytes) -> object:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key.")
            result[key] = value
        return result

    def nonfinite(value: str) -> None:
        raise ValueError("Nonfinite JSON number.")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
