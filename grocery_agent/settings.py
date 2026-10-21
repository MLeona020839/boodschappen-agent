"""Configuration, read from Streamlit secrets (when deployed) or environment variables (locally).

Settings
    ANTHROPIC_API_KEY     Server-side key. Optional when every user brings their own key.
    ACCESS_CODE           If set, the server key is only used after entering this code.
    SESSION_BUDGET_USD    Max spend per browser session on the server key (default 1.00).
    SHARED_DEPLOYMENT     "true" on a public deployment: preferences are kept per session
                          instead of in a shared file.
    PROMOS_DATABASE_URL   SQLAlchemy URL for promotions, e.g. a Supabase Postgres URL.
                          Defaults to a local SQLite file.
"""
from __future__ import annotations

import os


def get(name: str, default: str | None = None) -> str | None:
    try:
        import streamlit as st
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:  # not running under Streamlit, or no secrets.toml
        pass
    return os.environ.get(name, default)


def flag(name: str) -> bool:
    return (get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def number(name: str, default: float) -> float:
    try:
        return float(get(name) or default)
    except ValueError:
        return default
