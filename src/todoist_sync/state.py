from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def load_state(path: Path) -> tuple[list[dict], list[dict]]:
    """Returns (pairs, archive). A state.json predating the archive split is
    a bare list of pairs — read as all-active with an empty archive."""
    if not path.exists():
        return [], []
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return data, []
    return data.get("pairs", []), data.get("archive", [])


def save_state(path: Path, pairs: list[dict], archive: list[dict]) -> None:
    # Write to a temp file in the same directory, then atomically swap it
    # into place. A plain write_text() can leave a truncated/invalid
    # state.json behind if the process is killed mid-write — os.replace()
    # can't produce a partial result, so readers only ever see the old
    # complete file or the new complete file, never something in between.
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            # "pairs" is written before "archive" so the live, actively
            # reconciled entries are what a human skimming the file sees
            # first — archive is the cold, id-matching-only tail.
            f.write(json.dumps({"pairs": pairs, "archive": archive}, indent=2))
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise
