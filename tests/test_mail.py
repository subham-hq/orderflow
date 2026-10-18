from unittest import mock

from orderflow.mail import RESEND_URL, external_url, send_email
from orderflow.models import Lead
from tests import factories


def _send(app, backend, **config):
    app.config.update(MAIL_BACKEND=backend, **config)
    user = factories.make_owner(factories.make_company("Mail Co"), first_name="Mina")
    with app.test_request_context():
        return send_email(user.email, "Hello", "verify_email", user=user, link="https://example.com/x")


def test_resend_backend_posts_html_and_text(app):
    with mock.patch("orderflow.mail.requests.post") as post:
        post.return_value.status_code = 200
        assert _send(app, "resend", RESEND_API_KEY="re_test_key")
    url = post.call_args.args[0]
    payload = post.call_args.kwargs["json"]
    assert url == RESEND_URL
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer re_test_key"
    assert payload["subject"] == "Hello"
    assert "https://example.com/x" in payload["text"] and "https://example.com/x" in payload["html"]


def test_resend_failure_is_logged_not_raised(app, caplog):
    with mock.patch("orderflow.mail.requests.post") as post:
        post.return_value.status_code = 422
        post.return_value.text = "invalid from address"
        assert _send(app, "resend", RESEND_API_KEY="re_test_key") is False
    assert "Could not send" in caplog.text


def test_resend_without_api_key_fails_safely(app):
    assert _send(app, "resend", RESEND_API_KEY=None) is False


def test_smtp_backend(app):
    with mock.patch("orderflow.mail.smtplib.SMTP") as smtp:
        assert _send(app, "smtp", SMTP_HOST="smtp.example.com", SMTP_USERNAME="u", SMTP_PASSWORD="p")
    server = smtp.return_value.__enter__.return_value
    server.starttls.assert_called_once()
    server.login.assert_called_once_with("u", "p")
    sent = server.send_message.call_args.args[0]
    assert sent["Subject"] == "Hello"


def test_console_backend_logs(app, caplog):
    caplog.set_level("INFO")
    assert _send(app, "console")
    assert "Subject: Hello" in caplog.text


def test_unknown_backend_and_bad_recipients(app):
    assert _send(app, "carrier-pigeon") is False
    with app.test_request_context():
        assert send_email(["", "not-an-email"], "Hi", "verify_email") is False


def test_external_url_uses_base_url_outside_requests(app):
    app.config["APP_BASE_URL"] = "https://orders.example.com"
    assert external_url("auth.login") == "https://orders.example.com/login"


def test_contact_form_stores_lead_and_emails_sales(client, mail, app):
    response = client.post(
        "/contact",
        data={
            "name": "Kiran",
            "email": "Kiran@Shop.example",
            "company": "Kiran Stores",
            "message": "We sell to 40 retailers.",
        },
    )
    assert response.status_code == 302
    lead = app.extensions["sqlalchemy"].session.scalar(factories.db.select(Lead))
    assert lead.email == "kiran@shop.example"
    assert mail[-1].to == ["sales@orderflow.test"]
    assert mail[-1].reply_to == "kiran@shop.example"


def test_contact_form_validation(client, mail):
    response = client.post("/contact", data={"name": "", "email": "nope"})
    assert response.status_code == 302
    assert mail == []


def test_start_redirects_by_role(client, login, owner):
    assert client.get("/start").headers["Location"] == "/signup"
    login(owner)
    assert client.get("/start").headers["Location"] == "/app/"
