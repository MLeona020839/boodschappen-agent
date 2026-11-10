"""Spending caps on the server's API key: per day for all visitors together, and per visitor per day.
The owner's promotion imports have their own daily budget, so they never use up the visitors'.

Spend is recorded per day (Europe/Amsterdam, so it resets at local midnight) in the same database as
the promotions, so it survives restarts on Streamlit Cloud. If that database is unreachable, spend is
counted in memory for this process instead of letting requests through uncounted.

The check happens before each request, so concurrent requests can overshoot the cap by at most the
cost of the requests already running (a few tens of cents each).
"""
from __future__ import annotations

import threading
from datetime import datetime, time, timedelta
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
# Per visitor (browser session id) and day, so a visitor's cap survives server restarts and
# Cloud Run scaling to zero, and resets at midnight like the shared budget.
session_spend = sa.Table(
    "session_spend", metadata,
    sa.Column("day", sa.String(10), primary_key=True),
    sa.Column("session_id", sa.String(64), primary_key=True),
    sa.Column("cost_usd", sa.Float, nullable=False),
)

# The owner's promotion imports, kept apart from the visitors' budget.
import_spend = sa.Table(
    "import_spend", metadata,
    sa.Column("day", sa.String(10), primary_key=True),
    sa.Column("cost_usd", sa.Float, nullable=False),
    sa.Column("requests", sa.Integer, nullable=False),
)

_ready: set[int] = set()          # engines whose table exists
_memory: dict[str, float] = {}    # fallback when the database is down: day -> usd
_memory_imports: dict[str, float] = {}
_memory_sessions: dict[tuple[str, str], float] = {}  # (day, session id) -> usd
_lock = threading.Lock()


def today() -> str:
    return datetime.now(TZ).date().isoformat()


def next_reset(now: datetime | None = None) -> datetime:
    """When the daily budgets start over: the next midnight in Amsterdam."""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    return datetime.combine(now.date() + timedelta(days=1), time(0), TZ)


def usd_to_eur() -> float:
    """Anthropic bills in US dollars; the budget is set in euros."""
    return settings.number("USD_TO_EUR", 0.92)


def daily_budget_eur() -> float | None:
    """The cap, or None when no cap is configured (e.g. running locally)."""
    value = settings.get("DAILY_BUDGET_EUR")
    return float(value) if value else None


def import_budget_eur() -> float:
    """What the owner's imports may cost per day (default €10, about ten folders)."""
    return settings.number("IMPORT_BUDGET_EUR", 10.0)


def _engine():
    engine = promos.connect()
    if id(engine) not in _ready:
        metadata.create_all(engine)
        _ready.add(id(engine))
    return engine


def _day_total(table: sa.Table, memory: dict[str, float], day: str) -> float:
    try:
        with _engine().connect() as con:
            row = con.execute(sa.select(table.c.cost_usd).where(table.c.day == day)).first()
        return (row[0] if row else 0.0) + memory.get(day, 0.0)
    except (promos.Unavailable, sa.exc.SQLAlchemyError):
        return memory.get(day, 0.0)


def _add_to_day(con, table: sa.Table, day: str, cost_usd: float) -> None:
    updated = con.execute(sa.update(table).where(table.c.day == day).values(
        cost_usd=table.c.cost_usd + cost_usd, requests=table.c.requests + 1)).rowcount
    if not updated:
        con.execute(sa.insert(table).values(day=day, cost_usd=cost_usd, requests=1))


def spent_today_usd(day: str | None = None) -> float:
    """What visitors spent today (imports not included)."""
    return _day_total(daily_spend, _memory, day or today())


def imports_spent_usd(day: str | None = None) -> float:
    return _day_total(import_spend, _memory_imports, day or today())


def record_import(cost_usd: float, day: str | None = None) -> None:
    """Add the cost of one promotion import to today's import total."""
    if cost_usd <= 0:
        return
    day = day or today()
    try:
        with _engine().begin() as con:
            _add_to_day(con, import_spend, day, cost_usd)
    except (promos.Unavailable, sa.exc.SQLAlchemyError):
        with _lock:
            _memory_imports[day] = _memory_imports.get(day, 0.0) + cost_usd


def import_remaining_eur() -> float:
    """Euros left in today's import budget."""
    return max(0.0, import_budget_eur() - imports_spent_usd() * usd_to_eur())


def record(cost_usd: float, day: str | None = None, session_id: str | None = None) -> None:
    """Add the cost of one request to today's total (and to the visitor's, if given)."""
    if cost_usd <= 0:
        return
    day = day or today()
    try:
        with _engine().begin() as con:
            _add_to_day(con, daily_spend, day, cost_usd)
            if session_id:
                where = sa.and_(session_spend.c.day == day, session_spend.c.session_id == session_id)
                updated = con.execute(sa.update(session_spend).where(where).values(
                    cost_usd=session_spend.c.cost_usd + cost_usd)).rowcount
                if not updated:
                    con.execute(sa.insert(session_spend).values(day=day, session_id=session_id, cost_usd=cost_usd))
    except (promos.Unavailable, sa.exc.SQLAlchemyError):
        with _lock:
            _memory[day] = _memory.get(day, 0.0) + cost_usd
            if session_id:
                key = (day, session_id)
                _memory_sessions[key] = _memory_sessions.get(key, 0.0) + cost_usd


def session_spent_usd(session_id: str, day: str | None = None) -> float:
    """What this visitor has spent today."""
    day = day or today()
    extra = _memory_sessions.get((day, session_id), 0.0)
    try:
        with _engine().connect() as con:
            row = con.execute(sa.select(session_spend.c.cost_usd).where(
                session_spend.c.day == day, session_spend.c.session_id == session_id)).first()
        return (row[0] if row else 0.0) + extra
    except (promos.Unavailable, sa.exc.SQLAlchemyError):
        return extra


def remaining_eur() -> float | None:
    """Euros left in today's free budget, or None when there is no cap."""
    budget = daily_budget_eur()
    if budget is None:
        return None
    return max(0.0, budget - spent_today_usd() * usd_to_eur())
