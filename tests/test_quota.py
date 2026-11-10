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
    monkeypatch.setattr(quota, "_memory_sessions", {})
    monkeypatch.setattr(quota, "_memory_imports", {})
    for name in ("DAILY_BUDGET_EUR", "USD_TO_EUR", "IMPORT_BUDGET_EUR"):
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



def test_visitor_spend_is_per_day_and_per_session():
    quota.record(0.30, day="2026-10-28", session_id="aaa")
    quota.record(0.20, day="2026-10-28", session_id="aaa")
    quota.record(0.40, day="2026-10-28", session_id="bbb")
    quota.record(0.10, day="2026-10-29", session_id="aaa")
    assert quota.session_spent_usd("aaa", day="2026-10-28") == pytest.approx(0.50)
    assert quota.session_spent_usd("bbb", day="2026-10-28") == pytest.approx(0.40)
    assert quota.session_spent_usd("aaa", day="2026-10-29") == pytest.approx(0.10)  # new day, fresh cap
    assert quota.spent_today_usd("2026-10-28") == pytest.approx(0.90)  # still in the shared total


def test_visitor_spend_survives_the_database_being_down(monkeypatch):
    monkeypatch.setenv("PROMOS_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/nothing")
    quota.record(0.25, session_id="aaa")
    assert quota.session_spent_usd("aaa") == pytest.approx(0.25)


def test_imports_have_their_own_budget(monkeypatch):
    monkeypatch.setenv("DAILY_BUDGET_EUR", "3")
    monkeypatch.setenv("USD_TO_EUR", "1")
    quota.record_import(0.80)
    quota.record_import(0.70)
    assert quota.imports_spent_usd() == pytest.approx(1.50)
    assert quota.remaining_eur() == pytest.approx(3.00)          # visitors' budget untouched
    assert quota.import_remaining_eur() == pytest.approx(8.50)   # default €10
    monkeypatch.setenv("IMPORT_BUDGET_EUR", "1")
    assert quota.import_remaining_eur() == 0
    quota.record(0.25)
    assert quota.imports_spent_usd() == pytest.approx(1.50)      # and visitors don't use the imports'


def test_next_reset_is_midnight_in_amsterdam():
    from zoneinfo import ZoneInfo
    utc = ZoneInfo("UTC")
    # 22:30 UTC on 10 Nov is already 23:30 in Amsterdam: reset at 11 Nov 00:00 local = 23:00 UTC
    reset = quota.next_reset(datetime(2026, 11, 10, 22, 30, tzinfo=utc))
    assert reset.isoformat() == "2026-11-11T00:00:00+01:00"
    # 23:30 UTC is already the 11th in Amsterdam: the next reset is the 12th
    assert quota.next_reset(datetime(2026, 11, 10, 23, 30, tzinfo=utc)).date().isoformat() == "2026-11-12"
