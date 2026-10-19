"""Nobody can flood one inbox with password reset emails by switching networks."""

import pytest

from orderflow import create_app
from orderflow.extensions import db, limiter
from orderflow.mail import outbox
from tests import factories
from tests.conftest import FreshContextClient


@pytest.fixture
def limited_app():
    app = create_app("testing", RATELIMIT_ENABLED=True)
    app.test_client_class = FreshContextClient
    with app.app_context():
        db.create_all()
        limiter.reset()
        outbox().clear()
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()  # close its connections now, not when garbage collected


def forgot(app, email, address):
    return (
        app.test_client()
        .post("/forgot-password", data={"email": email}, environ_base={"REMOTE_ADDR": address})
        .status_code
    )


def test_reset_emails_to_one_address_are_limited_whatever_the_network(limited_app):
    victim = factories.make_owner(email="victim@kaveri.example")
    statuses = [forgot(limited_app, " VICTIM@kaveri.example ", f"10.0.1.{n}") for n in range(10)]
    assert statuses == [200, 200, 200] + [429] * 7
    assert len([m for m in outbox() if m.to == [victim.email]]) == 3


def test_the_limit_is_the_same_for_addresses_without_an_account(limited_app):
    statuses = [forgot(limited_app, "nobody@nowhere.example", f"10.0.2.{n}") for n in range(5)]
    assert statuses == [200, 200, 200, 429, 429]
    assert outbox() == []


def test_other_people_can_still_reset_their_password(limited_app):
    factories.make_owner(email="victim@kaveri.example")
    other = factories.make_owner(email="other@kaveri.example")
    for n in range(4):
        forgot(limited_app, "victim@kaveri.example", f"10.0.3.{n}")
    assert forgot(limited_app, other.email, "10.0.4.1") == 200
    assert outbox()[-1].to == [other.email]
