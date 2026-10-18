"""The bulletin end-time error suggests formats the form really accepts."""

import html
import re
from datetime import UTC, datetime

import pytest

from orderflow import timeutil
from orderflow.admin.forms import parse_end_time
from orderflow.extensions import db
from orderflow.models import Bulletin

NOW = datetime(2026, 10, 18, 3, 0, tzinfo=UTC)
NEW = {"title": "Diwali dispatch schedule", "body": "Orders approved by 25 Oct ship first.", "publish": "y"}
# 6 PM on 31 Oct in India
EXPECTED = datetime(2026, 10, 31, 12, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(timeutil, "utcnow", lambda: NOW)


@pytest.fixture
def signed_in(login, owner):
    login(owner)
    return owner


def saved_expiry():
    [bulletin] = db.session.scalars(db.select(Bulletin)).all()
    return timeutil.as_utc(bulletin.expires_at)


def test_every_example_in_the_error_message_is_accepted(client, signed_in):
    body = client.post("/app/bulletins/new", data={**NEW, "expires_at": "tomorrow"}).get_data(as_text=True)
    message = html.unescape(re.search(r'class="field__error"[^>]*>([^<]*Enter a date and time[^<]*)<', body).group(1))
    assert message.strip() == "Enter a date and time, like 2026-10-31 18:00 or 31 Oct 2026, 6:00 PM."
    examples = re.fullmatch(r"Enter a date and time, like (.+) or (.+)\.", message.strip()).groups()
    for example in examples:
        assert parse_end_time(example, "Asia/Kolkata") == EXPECTED, example


@pytest.mark.parametrize(
    "typed",
    [
        "31 Oct 2026, 6:00 PM",
        "31 oct 2026, 6:00 pm",
        "31 October 2026 6:00 PM",
        " 31  Oct 2026,  06:00 PM ",
        "2026-10-31 18:00",
        "2026-10-31T18:00",
    ],
)
def test_the_shown_date_format_can_be_typed(client, signed_in, typed):
    response = client.post("/app/bulletins/new", data={**NEW, "expires_at": typed})
    assert response.status_code == 302
    assert saved_expiry() == EXPECTED


@pytest.mark.parametrize("typed", ["31 Oct 2026", "6:00 PM", "31/10/2026 18:00", "32 Oct 2026, 6:00 PM", "soon"])
def test_other_text_is_still_refused(client, signed_in, typed):
    response = client.post("/app/bulletins/new", data={**NEW, "expires_at": typed})
    assert response.status_code == 200
    assert "Enter a date and time, like 2026-10-31 18:00" in response.get_data(as_text=True)
    assert db.session.scalars(db.select(Bulletin)).all() == []


def test_a_typed_time_follows_the_company_time_zone(client, signed_in, company):
    company.timezone = "America/New_York"
    db.session.commit()
    client.post("/app/bulletins/new", data={**NEW, "expires_at": "31 Oct 2026, 6:00 PM"})
    assert saved_expiry() == datetime(2026, 10, 31, 22, 0, tzinfo=UTC)
