from datetime import datetime

import pytest

from grocery_agent import promos, quota


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("PROMOS_DATABASE_URL", f"sqlite:///{tmp_path / 'p.db'}")
    monkeypatch.setattr(promos, "_engines", {})
    monkeypatch.setattr(promos, "_down_until", 0.0)
    monkeypatch.setattr(quota, "_ready", set())
    monkeypatch.setattr(quota, "_memory", {})
    for name in ("DAILY_BUDGET_EUR", "USD_TO_EUR"):
        monkeypatch.delenv(name, raising=False)


def test_spend_accumulates_per_day():
    quota.record(0.10, day="2026-10-28")
    quota.record(0.25, day="2026-10-28")
    quota.record(0.40, day="2026-10-29")
    assert quota.spent_today_usd("2026-10-28") == pytest.approx(0.35)
    assert quota.spent_today_usd("2026-10-29") == pytest.approx(0.40)
    quota.record(0.0)  # nothing to record
    assert quota.spent_today_usd("2026-10-30") == 0


def test_remaining_in_euros(monkeypatch):
    assert quota.remaining_eur() is None  # no cap configured
    monkeypatch.setenv("DAILY_BUDGET_EUR", "3")
    monkeypatch.setenv("USD_TO_EUR", "0.9")
    quota.record(1.00)
    assert quota.remaining_eur() == pytest.approx(2.10)
    quota.record(5.00)
    assert quota.remaining_eur() == 0  # never negative


def test_day_is_amsterdam_time():
    assert quota.today() == datetime.now(quota.TZ).date().isoformat()


def test_falls_back_to_memory_when_the_database_is_down(monkeypatch):
    monkeypatch.setenv("PROMOS_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/nothing")
    quota.record(0.30)
    quota.record(0.20)
    assert quota.spent_today_usd() == pytest.approx(0.50)  # still counted, not let through
