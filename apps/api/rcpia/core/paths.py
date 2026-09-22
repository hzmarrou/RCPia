"""Filesystem paths without depending on Windows machine-wide MAX_PATH settings."""

import os
import sys
from pathlib import Path


def filesystem_path(path: Path) -> Path:
    """Preserve the target while making long Windows paths explicit.

    Node can write a session that Python's normal-path exists() then reports as
    absent. Use the same extended namespace for writing, checking and reading.
    Non-Windows paths keep their existing representation.
    """
    if sys.platform != "win32":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)
