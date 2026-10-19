"""Error pages and error logging."""

import logging

import pytest

from orderflow import create_app
from orderflow.errors import wait_message
from orderflow.extensions import db, limiter
from tests.conftest import FreshContextClient


@pytest.fixture
def limited_app():
    """An app with rate limits switched on, as in production."""
    app = create_app("testing", RATELIMIT_ENABLED=True)
    app.test_client_class = FreshContextClient
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def limit_page(response):
    assert response.status_code == 429
    return response.get_data(as_text=True).replace("&#39;", "'")


def test_the_limit_page_says_how_long_an_hour_long_limit_lasts(limited_app):
    limiter.reset()
    client = limited_app.test_client()
    # Different addresses, so the per-address limit (3 an hour) never trips first
    for n in range(5):
        assert client.post("/forgot-password", data={"email": f"nobody{n}@example.com"}).status_code == 200
    response = client.post("/forgot-password", data={"email": "nobody9@example.com"})
    page = limit_page(response)
    assert "Please wait an hour and try again." in page
    assert "a minute" not in page
    assert 3590 <= int(response.headers["Retry-After"]) <= 3600


def test_the_limit_page_for_sign_in_matches_the_limit_that_was_hit(limited_app):
    limiter.reset()
    client = limited_app.test_client()
    wrong = {"email": "owner@example.com", "password": "not the password"}
    # Ten wrong passwords for one account from one place trip the limits for
    # that place; the page must describe the wait the response actually sets
    for _ in range(10):
        client.post("/login", data=wrong, environ_base={"REMOTE_ADDR": "203.0.113.5"})
    response = client.post("/login", data=wrong, environ_base={"REMOTE_ADDR": "203.0.113.5"})
    retry_after = int(response.headers["Retry-After"])
    assert 0 < retry_after <= 15 * 60
    assert wait_message(retry_after) in limit_page(response)

    # Wrong passwords sent from elsewhere never lock the account out here
    assert client.post("/login", data=wrong, environ_base={"REMOTE_ADDR": "192.0.2.44"}).status_code == 200


def test_the_limit_page_for_many_accounts_from_one_place_says_a_minute(limited_app):
    limiter.reset()
    client = limited_app.test_client()
    wrong = {"email": "owner@example.com", "password": "not the password"}
    # Ten tries from one place for different addresses: the one-minute per-IP limit
    for n in range(10):
        client.post(
            "/login", data={**wrong, "email": f"user{n}@example.com"}, environ_base={"REMOTE_ADDR": "198.51.100.7"}
        )
    response = client.post(
        "/login", data={**wrong, "email": "x@example.com"}, environ_base={"REMOTE_ADDR": "198.51.100.7"}
    )
    assert "Please wait a minute and try again." in limit_page(response)


@pytest.mark.parametrize(
    ("seconds", "message"),
    [
        (None, "Please wait a while and try again."),
        (0, "Please wait a minute and try again."),
        (59, "Please wait a minute and try again."),
        (60, "Please wait a minute and try again."),
        (61, "Please wait 2 minutes and try again."),
        (899, "Please wait 15 minutes and try again."),
        (3540, "Please wait 59 minutes and try again."),
        (3600, "Please wait an hour and try again."),
        (7200, "Please wait 2 hours and try again."),
    ],
)
def test_wait_message(seconds, message):
    assert wait_message(seconds) == message


def test_an_unhandled_error_is_logged_once_without_the_emailed_token(app, caplog):
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.route("/reset-password/<token>/boom")
    def boom(token):
        raise RuntimeError("kaboom")

    caplog.set_level(logging.ERROR)
    response = app.test_client().get("/reset-password/IjEyMyI.Zx9Q.secret-token/boom")
    assert response.status_code == 500

    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert errors[0].getMessage() == "Unhandled error on GET /reset-password/[token]/boom"
    assert errors[0].exc_info[0] is RuntimeError  # the traceback is still there
    assert "secret-token" not in caplog.text
