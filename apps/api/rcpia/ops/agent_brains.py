"""Synchronize the four active brains with their delimited review payloads.

Run from a source checkout with ``python -m rcpia.ops.agent_brains --check``.
``--write`` regenerates all four runtime files after validating every source.
Neither mode changes routing or makes a dormant role runnable.
"""

import argparse
import hashlib
from pathlib import Path

PROMOTIONS = {
    "urology_oncology": "agent-urology-oncology-brain-v2.md",
    "medical_oncology": "agent-medical-oncology-brain-v2.md",
    "radiation_oncology": "agent-radiation-oncology-brain-v2.md",
    "rcp_synthesis": "agent-rcp-synthesis-brain-v2.md",
}
BEGIN = "<!-- BEGIN RUNTIME PROMPT -->"
END = "<!-- END RUNTIME PROMPT -->"
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def payload(review: str) -> str:
    """Extract exactly one complete payload, excluding editorial front matter."""
    if review.count(BEGIN) != 1 or review.count(END) != 1:
        raise ValueError("Expected exactly one pair of runtime prompt markers.")
    _, content = review.split(BEGIN)
    body, after = content.split(END)
    if after.strip():
        raise ValueError("Editorial content must precede the runtime payload.")
    body = body.strip()
    if not body.startswith("<identity>") or not body.endswith("</style>"):
        raise ValueError("Runtime payload must span <identity> through </style>.")
    return body + "\n"


def expected_brains(root: Path) -> dict[Path, bytes]:
    """Validate every source and destination before any file can be written."""
    result: dict[Path, bytes] = {}
    for role, document in PROMOTIONS.items():
        source = root / "docs" / "implementation" / document
        destination = root / "agents" / role / "brain.md"
        if not destination.is_file():
            raise FileNotFoundError(f"Active runtime brain is missing: {destination}")
        result[destination] = payload(source.read_text(encoding="utf-8")).encode("utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--root", type=Path, default=REPOSITORY_ROOT)
    args = parser.parse_args()
    expected = expected_brains(args.root)
    different = []
    for destination, content in expected.items():
        if args.write:
            destination.write_bytes(content)
        elif destination.read_text(encoding="utf-8").encode("utf-8") != content:
            different.append(str(destination.relative_to(args.root)))
        print(f"{destination.parent.name}: sha256={hashlib.sha256(content).hexdigest()}")
    if different:
        parser.exit(1, "Runtime/review drift: " + ", ".join(different) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
