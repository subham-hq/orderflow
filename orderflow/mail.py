"""Sending email.

Backends (MAIL_BACKEND):
  console  print the message to the log (local development)
  memory   keep messages in app.extensions["mail_outbox"] (tests)
  resend   send through the Resend HTTP API (production)
  smtp     send through any SMTP server

Each email has an HTML template and a plain-text template under
templates/emails/<name>.html and <name>.txt.
"""

import logging
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import parseaddr

import requests
from flask import current_app, render_template

log = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


@dataclass
class Message:
    to: list[str]
    subject: str
    text: str
    html: str
    reply_to: str | None = None
    tags: dict = field(default_factory=dict)


def _send_console(message):
    log.info(
        "Email to %s\nSubject: %s\n\n%s",
        ", ".join(message.to),
        message.subject,
        message.text,
    )


def _send_memory(message):
    current_app.extensions.setdefault("mail_outbox", []).append(message)


def _send_resend(message):
    api_key = current_app.config.get("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY is not set")
    payload = {
        "from": current_app.config["MAIL_FROM"],
        "to": message.to,
        "subject": message.subject,
        "html": message.html,
        "text": message.text,
    }
    if message.reply_to:
        payload["reply_to"] = message.reply_to
    response = requests.post(
        RESEND_URL,
        json=payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10,
    )
    if response.status_code >= 300:
        raise RuntimeError(f"Resend returned {response.status_code}: {response.text[:300]}")


def _send_smtp(message):
    config = current_app.config
    email = EmailMessage()
    email["From"] = config["MAIL_FROM"]
    email["To"] = ", ".join(message.to)
    email["Subject"] = message.subject
    if message.reply_to:
        email["Reply-To"] = message.reply_to
    email.set_content(message.text)
    email.add_alternative(message.html, subtype="html")
    with smtplib.SMTP(config["SMTP_HOST"], config["SMTP_PORT"], timeout=15) as server:
        if config["SMTP_USE_TLS"]:
            server.starttls()
        if config.get("SMTP_USERNAME"):
            server.login(config["SMTP_USERNAME"], config["SMTP_PASSWORD"])
        server.send_message(email)


BACKENDS = {
    "console": _send_console,
    "memory": _send_memory,
    "resend": _send_resend,
    "smtp": _send_smtp,
}


def send_email(to, subject, template, reply_to=None, **context):
    """Render templates/emails/<template>.(html|txt) and send it.

    Returns True when the message was handed to the backend. Delivery
    problems are logged rather than raised, so a mail outage never breaks
    the page the user is on.
    """
    recipients = [to] if isinstance(to, str) else list(to)
    recipients = [addr for addr in recipients if addr and "@" in parseaddr(addr)[1]]
    if not recipients:
        return False

    context.setdefault("app_name", current_app.config["APP_NAME"])
    context.setdefault("base_url", current_app.config["APP_BASE_URL"])
    context["subject"] = subject
    message = Message(
        to=recipients,
        subject=subject,
        text=render_template(f"emails/{template}.txt", **context),
        html=render_template(f"emails/{template}.html", **context),
        reply_to=reply_to or current_app.config.get("MAIL_REPLY_TO"),
    )

    backend = BACKENDS.get(current_app.config["MAIL_BACKEND"])
    if backend is None:
        log.error("Unknown MAIL_BACKEND %r", current_app.config["MAIL_BACKEND"])
        return False
    try:
        backend(message)
    except Exception:
        log.exception("Could not send %r email to %s", template, recipients)
        return False
    return True


def outbox():
    """Messages captured by the memory backend (tests)."""
    return current_app.extensions.setdefault("mail_outbox", [])


def external_url(endpoint, **values):
    """Absolute link for emails, built from APP_BASE_URL. Works outside requests too."""
    from flask import has_request_context, url_for

    if has_request_context():
        path = url_for(endpoint, _external=False, **values)
    else:
        with current_app.test_request_context():
            path = url_for(endpoint, _external=False, **values)
    return current_app.config["APP_BASE_URL"] + path
