"""The account holder hears about every password change and reset."""

import orderflow.mail
from tests import factories
from tests.conftest import link_from
from tests.test_design_emails import check_layout, preheader

SUBJECT = "Your OrderFlow password was changed"
NEW_PASSWORD = "a different password"


def _change(client):
    return client.post(
        "/account/password",
        data={"current_password": factories.PASSWORD, "password": NEW_PASSWORD, "confirm": NEW_PASSWORD},
    )


def test_changing_the_password_sends_a_notice(client, login, owner, mail):
    login(owner)
    mail.clear()
    assert _change(client).status_code == 302

    [message] = mail
    assert message.to == [owner.email] and message.subject == SUBJECT
    html = check_layout(message)
    assert "Your password was changed" in html.text
    assert "If you didn't, reset your password now" in html.text.replace("’", "'")
    assert "reset your password now" in preheader(message)
    assert link_from(message, "/forgot-password") == "/forgot-password"
    assert "/reset-password/" not in message.text  # no usable reset link in the notice


def test_resetting_the_password_sends_a_notice(client, owner, mail):
    client.post("/forgot-password", data={"email": owner.email})
    link = link_from(mail[-1], "/reset-password/")
    mail.clear()
    response = client.post(link, data={"password": NEW_PASSWORD, "confirm": NEW_PASSWORD})
    assert response.headers["Location"].endswith("/app/")
    assert [message.subject for message in mail] == [SUBJECT]
    assert mail[0].to == [owner.email]


def test_a_suspended_account_still_hears_about_a_reset(client, buyer, mail):
    from orderflow.extensions import db

    client.post("/forgot-password", data={"email": buyer.email})
    link = link_from(mail[-1], "/reset-password/")
    buyer.active = False
    db.session.commit()
    mail.clear()
    client.post(link, data={"password": NEW_PASSWORD, "confirm": NEW_PASSWORD})
    assert [message.subject for message in mail] == [SUBJECT]


def test_the_change_still_works_when_the_notice_cannot_be_sent(client, login, owner, monkeypatch):
    def mail_is_down(message):
        raise RuntimeError("Resend returned 500")

    login(owner)
    monkeypatch.setitem(orderflow.mail.BACKENDS, "memory", mail_is_down)
    assert _change(client).status_code == 302
    assert owner.check_password(NEW_PASSWORD)
    assert client.get("/app/").status_code == 200


def test_failed_attempts_send_nothing(client, login, owner, mail):
    login(owner)
    mail.clear()
    client.post(
        "/account/password", data={"current_password": "wrong", "password": NEW_PASSWORD, "confirm": NEW_PASSWORD}
    )
    assert mail == []
