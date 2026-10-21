"""Long-term user preferences (household size, diet, favourite stores, ...).

Locally they are stored in a JSON file. On a shared deployment every visitor must have their
own, so the app installs a per-session store with `use_store()`; tools and the system
prompt read whichever store is active for the current session (a ContextVar, so each
Streamlit session thread sees its own).
"""
from __future__ import annotations

import json
from contextvars import ContextVar
from pathlib import Path
from typing import Protocol

from .db import DATA_DIR

PREFS_PATH = DATA_DIR / "preferences.json"


class Store(Protocol):
    def load(self) -> list[str]: ...
    def save(self, notes: list[str]) -> None: ...


class FileStore:
    def __init__(self, path: Path = PREFS_PATH):
        self.path = path

    def load(self) -> list[str]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def save(self, notes: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")


class MemoryStore:
    """Preferences held in memory, e.g. inside st.session_state."""

    def __init__(self, notes: list[str] | None = None):
        self.notes = list(notes or [])

    def load(self) -> list[str]:
        return list(self.notes)

    def save(self, notes: list[str]) -> None:
        self.notes = list(notes)


_store: ContextVar[Store] = ContextVar("prefs_store", default=FileStore())


def use_store(store: Store) -> None:
    """Make `store` the preferences store for the current thread/context."""
    _store.set(store)


def load() -> list[str]:
    return _store.get().load()


def save(notes: list[str]) -> None:
    _store.get().save(notes)
