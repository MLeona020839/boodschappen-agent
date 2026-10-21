"""Long-term user preferences (household size, diet, favourite stores, ...), stored as JSON."""
from __future__ import annotations

import json
from pathlib import Path

from .db import DATA_DIR

PREFS_PATH = DATA_DIR / "preferences.json"


def load(path: Path = PREFS_PATH) -> list[str]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save(notes: list[str], path: Path = PREFS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
