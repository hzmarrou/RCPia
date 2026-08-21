"""The operator command: what it reconciles, and what it shows."""

import json
from pathlib import Path

from rcpia.evidence.load import run


def release_dir(tmp_path: Path, *, routing: str | None, manifest: str | None) -> Path:
    directory = tmp_path / "release"
    directory.mkdir(exist_ok=True)
    if routing is not None:
        (directory / "routing.json").write_text(
            json.dumps({"release_id": routing}), encoding="utf-8"
        )
    if manifest is not None:
        (directory / "manifest.json").write_text(
            json.dumps({"release_id": manifest}), encoding="utf-8"
        )
    return directory


async def test_a_release_named_differently_in_two_files_is_refused(
    tmp_path: Path, capsys
) -> None:
    """The database would otherwise record a release different from the one whose coverage
    was proved. The manifest is the file that was validated, so it wins -- and a disagreement
    is refused rather than resolved."""
    directory = release_dir(
        tmp_path, routing="rcpia-corpus-v4.0", manifest="rcpia-corpus-v5.0"
    )

    assert await run(directory, dry_run=True) == 1
    assert "release refused" in capsys.readouterr().err
