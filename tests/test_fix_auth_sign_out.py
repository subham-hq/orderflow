"""Signing out really signs out, and an unused session ends on its own."""

import pytest

from orderflow.auth import sessions
from orderflow.tokens import RESET_PASSWORD, make_token
from tests import factories

HOUR = 3600

# Flask-Login sets the remember-me cookie expiry with datetime.utcnow()
pytestmark = pytest.mark.filterwarnings("ignore:datetime.datetime.utcnow:DeprecationWarning")


@pytest.fixture
def clock(monkeypatch):
    """Move the session clock forward without touching cookie signatures."""
    start = sessions.now()
    offset = {"seconds": 0}
    monkeypatch.setattr(sessions, "now", lambda: start + offset["seconds"])

    def advance(seconds):
        offset["seconds"] += seconds

    return advance


def _cookie(client, name):
    cookie = client.get_cookie(name)
    return cookie.value if cookie else None


def test_signing_out_deletes_the_keep_me_signed_in_cookie(client, owner):
    client.post("/login", data={"email": owner.email, "password": factories.PASSWORD, "remember": "y"})
    assert _cookie(client, "orderflow_remember")

    client.post("/logout")
    assert _cookie(client, "orderflow_remember") is None
    assert client.get("/app/").headers["Location"].startswith("/login")


def test_a_new_sign_in_drops_a_remember_me_cookie_left_by_someone_else(app, client, company, owner):
    client.post("/login", data={"email": owner.email, "password": factories.PASSWORD, "remember": "y"})
    other = factories.make_admin(company, email="other@kaveri.example")
    with app.test_request_context():
        link = f"/reset-password/{make_token(RESET_PASSWORD, other)}"
    client.post(link, data={"password": "a brand new password", "confirm": "a brand new password"})
    assert _cookie(client, "orderflow_remember") is None


def test_an_unused_session_ends_after_the_idle_timeout(client, login, owner, clock):
    login(owner)
    clock(11 * HOUR)
    assert client.get("/app/").status_code == 200  # still in use, so the timer restarts
    clock(11 * HOUR)
    assert client.get("/app/").status_code == 200
    clock(12 * HOUR + 60)
    response = client.get("/app/orders")
    assert response.headers["Location"] == "/login?next=/app/orders"
    page = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "You were signed out because you hadn&#39;t been active for a while." in page


def test_a_copied_session_cookie_stops_working_once_idle(app, client, login, owner, clock):
    login(owner)
    copied = _cookie(client, "orderflow_session")
    client.post("/logout")

    thief = app.test_client()
    thief.set_cookie("orderflow_session", copied)
    clock(12 * HOUR + 60)
    assert thief.get("/app/").headers["Location"].startswith("/login")


def test_keep_me_signed_in_outlasts_the_idle_timeout(client, owner, clock):
    client.post("/login", data={"email": owner.email, "password": factories.PASSWORD, "remember": "y"})
    clock(13 * HOUR)
    assert client.get("/app/").status_code == 200


def test_the_cart_survives_an_idle_sign_out(client, login, company, buyer, clock):
    product = factories.make_product(company)
    login(buyer)
    with client.session_transaction() as session:
        session["cart"] = {str(product.id): 2}
    clock(13 * HOUR)
    client.get("/portal/")
    with client.session_transaction() as session:
        assert session["cart"] == {str(product.id): 2}
        assert "_user_id" not in session
    login(buyer)  # the same person signing in again
    assert client.get("/portal/").status_code == 200


def test_the_idle_timeout_can_be_configured():
    from orderflow import create_app

    app = create_app("testing", SESSION_IDLE_TIMEOUT=sessions.IDLE_TIMEOUT / 12)
    assert app.config["SESSION_IDLE_TIMEOUT"].total_seconds() == HOUR
    assert create_app("testing").config["SESSION_IDLE_TIMEOUT"] == sessions.IDLE_TIMEOUT


def test_an_idle_session_cannot_send_another_confirmation_email(clock):
    # With rate limits on, as in production: that limit is keyed on current_user
    from orderflow import create_app
    from orderflow.extensions import db, limiter
    from orderflow.mail import outbox
    from tests.conftest import FreshContextClient

    app = create_app("testing", RATELIMIT_ENABLED=True)
    app.test_client_class = FreshContextClient
    with app.app_context():
        db.create_all()
        limiter.reset()
        try:
            newcomer = factories.make_owner(email="new@kaveri.example", verified=False)
            client = app.test_client()
            client.post("/login", data={"email": newcomer.email, "password": factories.PASSWORD})
            outbox().clear()
            clock(13 * HOUR)
            response = client.post("/verify/resend")
            assert response.headers["Location"].startswith("/login")
            assert outbox() == []
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()  # close its connections now, not when garbage collected
