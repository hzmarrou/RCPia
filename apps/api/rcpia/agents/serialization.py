"""The exact UTF-8 attachment bytes measured by the gate and written for Pi."""

import json

from rcpia.agents.schemas import TaskBundle
from rcpia.agents.transport import encode_synthesis


def bundle_bytes(bundle: TaskBundle) -> bytes:
    """Measure/send the same lossless transport; never change canonical model identities."""
    return json.dumps(
        encode_synthesis(bundle.model_dump(mode="json", by_alias=True)),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
