from __future__ import annotations

import json
from pathlib import Path


def load_state(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save_state(path: Path, pairs: list[dict]) -> None:
    path.write_text(json.dumps(pairs, indent=2))
