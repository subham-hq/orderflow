from datetime import UTC, datetime, timedelta

import pytest

from orderflow.timeutil import (
    as_utc,
    format_datetime,
    get_zone,
    local_input_value,
    parse_local_datetime,
    time_ago,
    to_local,
)


def test_datetime_local_input_is_read_in_company_time_zone():
    # 18 Oct 2026, 14:30 in India is 09:00 UTC
    assert parse_local_datetime("2026-10-18T14:30", "Asia/Kolkata") == datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    assert parse_local_datetime("2026-10-18T14:30", "UTC") == datetime(2026, 10, 18, 14, 30, tzinfo=UTC)


def test_parse_local_datetime_handles_empty_and_bad_input():
    assert parse_local_datetime("", "Asia/Kolkata") is None
    assert parse_local_datetime(None, "Asia/Kolkata") is None
    with pytest.raises(ValueError):
        parse_local_datetime("next tuesday", "Asia/Kolkata")


def test_round_trip_for_form_values():
    stored = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    assert local_input_value(stored, "Asia/Kolkata") == "2026-10-18T14:30"
    assert local_input_value(None, "Asia/Kolkata") == ""


def test_naive_values_from_sqlite_are_treated_as_utc():
    naive = datetime(2026, 1, 1, 0, 0)
    assert as_utc(naive) == datetime(2026, 1, 1, tzinfo=UTC)
    assert to_local(naive, "Asia/Kolkata").hour == 5


def test_unknown_time_zone_falls_back_to_india():
    assert str(get_zone("Mars/Olympus")) == "Asia/Kolkata"


def test_format_datetime():
    value = datetime(2026, 10, 18, 9, 5, tzinfo=UTC)
    assert format_datetime(value, "Asia/Kolkata") == "18 Oct 2026, 2:35 PM"
    assert format_datetime(None, "Asia/Kolkata") == ""


def test_time_ago():
    now = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    assert time_ago(now - timedelta(seconds=10), now) == "just now"
    assert time_ago(now - timedelta(minutes=5), now) == "5 min ago"
    assert time_ago(now - timedelta(hours=3), now) == "3 hr ago"
    assert time_ago(now - timedelta(days=1), now) == "1 day ago"
    assert time_ago(now - timedelta(days=90), now) == "3 months ago"
    assert time_ago(now - timedelta(days=800), now) == "2 years ago"
    assert time_ago(None) == ""
