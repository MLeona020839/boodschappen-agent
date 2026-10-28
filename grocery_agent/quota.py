"""Shared daily spending cap on the server's API key: all visitors together.

Spend is recorded per day (Europe/Amsterdam, so it resets at local midnight) in the same database as
the promotions, so it survives restarts on Streamlit Cloud. If that database is unreachable, spend is
counted in memory for this process instead of letting requests through uncounted.

The check happens before each request, so concurrent requests can overshoot the cap by at most the
cost of the requests already running (a few tens of cents each).
"""
from __future__ import annotations

import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from . import promos, settings

TZ = ZoneInfo("Europe/Amsterdam")

metadata = sa.MetaData()
daily_spend = sa.Table(
    "daily_spend", metadata,
    sa.Column("day", sa.String(10), primary_key=True),   # ISO date in Amsterdam time
    sa.Column("cost_usd", sa.Float, nullable=False),
    sa.Column("requests", sa.Integer, nullable=False),
)

_ready: set[int] = set()          # engines whose table exists
_memory: dict[str, float] = {}    # fallback when the database is down
_lock = threading.Lock()


def today() -> str:
    return datetime.now(TZ).date().isoformat()


def usd_to_eur() -> float:
    """Anthropic bills in US dollars; the budget is set in euros."""
    return settings.number("USD_TO_EUR", 0.92)


def daily_budget_eur() -> float | None:
    """The cap, or None when no cap is configured (e.g. running locally)."""
    value = settings.get("DAILY_BUDGET_EUR")
    return float(value) if value else None


def _engine():
    engine = promos.connect()
    if id(engine) not in _ready:
        metadata.create_all(engine)
        _ready.add(id(engine))
    return engine


def spent_today_usd(day: str | None = None) -> float:
    day = day or today()
    try:
        with _engine().connect() as con:
            row = con.execute(sa.select(daily_spend.c.cost_usd).where(daily_spend.c.day == day)).first()
        return (row[0] if row else 0.0) + _memory.get(day, 0.0)
    except (promos.Unavailable, sa.exc.SQLAlchemyError):
        return _memory.get(day, 0.0)


def record(cost_usd: float, day: str | None = None) -> None:
    """Add the cost of one request to today's total."""
    if cost_usd <= 0:
        return
    day = day or today()
    try:
        with _engine().begin() as con:
            updated = con.execute(
                sa.update(daily_spend).where(daily_spend.c.day == day).values(
                    cost_usd=daily_spend.c.cost_usd + cost_usd,
                    requests=daily_spend.c.requests + 1)
            ).rowcount
            if not updated:
                con.execute(sa.insert(daily_spend).values(day=day, cost_usd=cost_usd, requests=1))
    except (promos.Unavailable, sa.exc.SQLAlchemyError):
        with _lock:
            _memory[day] = _memory.get(day, 0.0) + cost_usd


def remaining_eur() -> float | None:
    """Euros left in today's free budget, or None when there is no cap."""
    budget = daily_budget_eur()
    if budget is None:
        return None
    return max(0.0, budget - spent_today_usd() * usd_to_eur())
