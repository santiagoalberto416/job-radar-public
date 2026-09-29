from datetime import datetime, timedelta, timezone

from job_radar.credit import credit_status, format_credit_message
from job_radar.db import Database
from job_radar.scorer import Usage

TZ = timezone(timedelta(hours=-7))
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=TZ)


def db_with_spend():
    db = Database(":memory:")
    for at, cost in [
        (datetime(2026, 9, 26, 12, tzinfo=TZ), 1.00),  # before as_of
        (datetime(2026, 9, 28, 12, tzinfo=TZ), 0.50),
        (datetime(2026, 10, 4, 8, tzinfo=TZ), 0.20),  # yesterday
        (datetime(2026, 10, 5, 8, tzinfo=TZ), 0.05),  # today
    ]:
        db.record_llm_spend(at, "claude-sonnet-5", Usage(calls=1), cost)
    return db


def test_credit_status_math():
    status = credit_status(db_with_spend(), {"balance_usd": 10, "as_of": "2026-09-27"}, NOW)
    assert round(status.yesterday, 2) == 0.20
    assert round(status.last_30_days, 2) == 1.75
    assert round(status.spent_since, 2) == 0.75
    assert round(status.remaining, 2) == 9.25


def test_message_formats_and_warns_when_low():
    status = credit_status(db_with_spend(), {"balance_usd": 1.0, "as_of": "2026-09-27"}, NOW)
    text = format_credit_message(status, warn_below=2.0)
    assert text.startswith("⚠️") and "$0.25" in text and "Recargar" in text
    ok = format_credit_message(credit_status(db_with_spend(), {"balance_usd": 10, "as_of": "2026-09-27"}, NOW), 2.0)
    assert ok.startswith("💳") and "$9.25" in ok and "Ayer: $0.20" in ok


def test_message_without_configured_balance():
    text = format_credit_message(credit_status(db_with_spend(), {}, NOW), 2.0)
    assert "configura credit.balance_usd" in text


def test_boundaries_work_across_timezones():
    # Spend recorded in UTC at 03:00 on Oct 5 is 20:00 on Oct 4 in Pacific Time (UTC-7): it belongs to "yesterday".
    db = Database(":memory:")
    db.record_llm_spend(datetime(2026, 10, 5, 3, tzinfo=timezone.utc), "m", Usage(calls=1), 0.30)
    status = credit_status(db, {"balance_usd": 4.04, "as_of": "2026-10-04 19:30"}, NOW)
    assert round(status.yesterday, 2) == 0.30
    assert round(status.remaining, 2) == 3.74
    later = credit_status(db, {"balance_usd": 4.04, "as_of": "2026-10-04 20:30"}, NOW)
    assert later.spent_since == 0 and "2026-10-04 20:30" in format_credit_message(later, 2.0)
