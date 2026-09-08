"""The exact UTF-8 attachment bytes measured by the gate and written for Pi."""

import json

from rcpia.agents.schemas import TaskBundle


def bundle_bytes(bundle: TaskBundle) -> bytes:
    return json.dumps(
        bundle.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")
