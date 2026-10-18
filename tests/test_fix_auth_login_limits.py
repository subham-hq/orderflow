"""Sign-in rate limits slow down password guessing without locking real people out."""

import pytest

from orderflow import create_app
from orderflow.extensions import db, limiter
from tests import factories
from tests.conftest import FreshContextClient

VICTIM_ADDRESS = "198.51.100.8"


class Clock:
    """Stands in for time.time() in the rate limiter's in-memory storage."""

    def __init__(self):
        self.now = 1_800_000_000.0

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    import limits.storage.memory

    fake = Clock()
    monkeypatch.setattr(limits.storage.memory, "time", fake)
    return fake


@pytest.fixture
def limited_app(clock):
    app = create_app("testing", RATELIMIT_ENABLED=True)
    app.test_client_class = FreshContextClient
    with app.app_context():
        db.create_all()
        limiter.reset()
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()  # close its connections now, not when garbage collected


@pytest.fixture
def victim(limited_app):
    return factories.make_owner(email="owner@kaveri.example")


def sign_in(app, email, password, address):
    """One sign-in attempt from a fresh browser at the given address."""
    return app.test_client().post(
        "/login", data={"email": email, "password": password}, environ_base={"REMOTE_ADDR": address}
    )


def test_wrong_guesses_from_many_addresses_do_not_lock_out_the_real_user(limited_app, victim):
    for n in range(12):
        assert sign_in(limited_app, " OWNER@Kaveri.example ", "not the password", f"203.0.113.{n}").status_code == 200
    response = sign_in(limited_app, victim.email, factories.PASSWORD, VICTIM_ADDRESS)
    assert response.status_code == 302
    assert response.headers["Location"] == "/app/"


def test_wrong_guesses_from_one_address_do_not_lock_out_the_real_user(limited_app, victim, clock):
    for _ in range(2):
        for _ in range(5):
            assert sign_in(limited_app, victim.email, "not the password", "203.0.113.50").status_code == 200
        clock.advance(61)  # stay under the per-address limit of 10 a minute
    assert sign_in(limited_app, victim.email, "not the password", "203.0.113.50").status_code == 429
    assert sign_in(limited_app, victim.email, factories.PASSWORD, VICTIM_ADDRESS).status_code == 302


def test_guessing_from_one_address_is_still_capped_per_account(limited_app, victim, clock):
    for _ in range(2):
        for _ in range(5):
            assert sign_in(limited_app, victim.email, "not the password", "203.0.113.50").status_code == 200
        clock.advance(61)
    # Ten wrong guesses in 15 minutes: that address can't try this account again yet
    assert sign_in(limited_app, victim.email, factories.PASSWORD, "203.0.113.50").status_code == 429
    clock.advance(15 * 60)
    assert sign_in(limited_app, victim.email, factories.PASSWORD, "203.0.113.50").status_code == 302


def test_successful_sign_ins_do_not_use_up_the_limit(limited_app, victim, clock):
    for _ in range(3):
        for _ in range(5):
            assert sign_in(limited_app, victim.email, factories.PASSWORD, VICTIM_ADDRESS).status_code == 302
        clock.advance(61)


def test_one_address_is_still_limited_to_ten_attempts_a_minute(limited_app, victim):
    for n in range(10):
        assert sign_in(limited_app, f"someone{n}@example.com", "whatever12345", "203.0.113.77").status_code == 200
    assert sign_in(limited_app, victim.email, factories.PASSWORD, "203.0.113.77").status_code == 429
    assert sign_in(limited_app, victim.email, factories.PASSWORD, VICTIM_ADDRESS).status_code == 302
