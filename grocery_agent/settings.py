"""Configuration, read from Streamlit secrets (when deployed) or environment variables (locally).

Settings
    ANTHROPIC_API_KEY     Server-side key. Without ACCESS_CODE the app is open to everyone on it.
    ACCESS_CODE           Optional: only visitors who enter this code may use the server key.
    DAILY_BUDGET_EUR      Free budget per day for all visitors together on the server key
                          (resets at midnight Amsterdam time). No cap when unset.
    SESSION_BUDGET_EUR    Max spend per browser session on the server key (default 1.00).
    USD_TO_EUR            Exchange rate for the budgets; Anthropic bills in USD (default 0.92).
    ADMIN_CODE            On a shared deployment, only this code unlocks importing/deleting promotions.
    TRUST_PROXY           "1" behind Caddy or Cloud Run: take the visitor IP from X-Forwarded-For.
    CORS_ORIGINS          Origins allowed to call the API, e.g. "https://mleona020839.github.io".
    SHARED_DEPLOYMENT     "true" on a public deployment: preferences are kept per session
                          instead of in a shared file.
    PROMOS_DATABASE_URL   SQLAlchemy URL for promotions and the daily spend, e.g. a Supabase
                          Postgres URL. Defaults to a local SQLite file.
"""
from __future__ import annotations

import os
import sys


def get(name: str, default: str | None = None) -> str | None:
    value = None
    if "streamlit" in sys.modules:  # only the Streamlit app has secrets; the API reads the environment
        try:
            import streamlit as st
            if name in st.secrets:
                value = str(st.secrets[name])
        except Exception:  # no secrets.toml
            pass
    if value is None:
        value = os.environ.get(name)
    # Pasted secrets often carry stray spaces or newlines, which silently break API keys.
    value = value.strip() if value is not None else None
    return value or default


def flag(name: str) -> bool:
    return (get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def number(name: str, default: float) -> float:
    try:
        return float(get(name) or default)
    except ValueError:
        return default
