"""If the confirmation email can't be sent at sign-up, the next page says so."""

import orderflow.mail
from orderflow.extensions import db
from orderflow.models import User
from tests.test_auth import SIGNUP

WARNING = "we couldn&#39;t send the confirmation email to meera@iyertraders.example just now"


def _mail_is_down(message):
    raise RuntimeError("Resend returned 500")


def test_a_failed_confirmation_email_is_reported(client, monkeypatch):
    monkeypatch.setitem(orderflow.mail.BACKENDS, "memory", _mail_is_down)
    response = client.post("/signup", data=SIGNUP)
    assert response.headers["Location"].endswith("/verify")
    assert db.session.scalar(db.select(User).where(User.email == SIGNUP["email"])) is not None

    page = client.get("/verify").get_data(as_text=True)
    assert WARNING in page
    assert "Send the link again" in page
    assert "We sent a confirmation link" not in page


def test_no_warning_when_the_email_was_sent(client, mail):
    client.post("/signup", data=SIGNUP)
    page = client.get("/verify").get_data(as_text=True)
    assert WARNING not in page
    assert "open the confirmation link we email to <strong>meera@iyertraders.example</strong>" in page
    assert len(mail) == 1
