"""Invitation pages tell the truth when the invitation email can't be sent."""

import pytest

from orderflow.extensions import db
from orderflow.services.accounts import find_user_by_email


@pytest.fixture
def broken_mail(app):
    app.config["MAIL_BACKEND"] = "not-a-backend"  # send_email returns False
    yield
    app.config["MAIL_BACKEND"] = "memory"


def test_client_invite_warns_when_the_email_fails(client, login, owner, broken_mail):
    login(owner)
    response = client.post(
        "/app/clients/invite",
        data={"first_name": "Ravi", "last_name": "K", "email": "ravi@stores.example", "buyer_company": "Ravi Stores"},
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)
    assert "couldn&#39;t send the invitation email to ravi@stores.example" in body
    assert "Invitation sent" not in body
    user = find_user_by_email("ravi@stores.example")
    assert user is not None and user.invite_sent_at is None  # so it can be resent straight away


def test_team_invite_warns_when_the_email_fails(client, login, owner, broken_mail):
    login(owner)
    response = client.post(
        "/app/team/invite",
        data={"first_name": "Deepa", "last_name": "S", "email": "deepa@kaveri.example"},
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)
    assert "couldn&#39;t send the invitation email to deepa@kaveri.example" in body
    assert find_user_by_email("deepa@kaveri.example").invite_sent_at is None


def test_successful_invite_records_when_it_was_sent(client, login, owner, mail):
    login(owner)
    response = client.post(
        "/app/clients/invite",
        data={"first_name": "Ravi", "last_name": "K", "email": "ravi@stores.example", "buyer_company": "Ravi Stores"},
        follow_redirects=True,
    )
    assert "Invitation sent to ravi@stores.example" in response.get_data(as_text=True)
    db.session.expire_all()
    assert find_user_by_email("ravi@stores.example").invite_sent_at is not None
    assert len(mail) == 1
