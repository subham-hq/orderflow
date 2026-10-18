"""The landing page's contact form: its messages, and what happens to each enquiry."""

import logging
import re
from datetime import UTC, datetime

from orderflow import config, create_app
from orderflow.extensions import db
from orderflow.models import Lead

ENQUIRY = {"name": "Kiran Rao", "email": "Kiran@Shop.example", "company": "Kiran Stores", "message": "We sell boxes."}


def field_errors(response):
    body = response.get_data(as_text=True).replace("&#39;", "'")
    return dict(re.findall(r'<p class="field__error" id="(\w+)-error">([^<]*)</p>', body))


def test_contact_form_errors_say_what_to_do(client, mail):
    response = client.post("/contact", data={"name": "   ", "email": "ananya@sunrise-retail"})
    assert response.status_code == 400
    assert field_errors(response) == {
        "name": "Enter your name.",
        "email": "Enter a valid email address, like name@business.in.",
    }

    response = client.post("/contact", data={"name": "A", "email": ""})
    assert field_errors(response) == {"email": "Enter your work email."}

    long = {"name": "x" * 121, "email": "a@example.com", "company": "c" * 161, "message": "m" * 2001}
    assert field_errors(client.post("/contact", data=long)) == {
        "name": "Keep your name to 120 characters or fewer.",
        "company": "Keep the company name to 160 characters or fewer.",
        "message": "Keep your message to 2,000 characters or fewer.",
    }
    assert db.session.scalar(db.select(db.func.count(Lead.id))) == 0
    assert mail == []


def test_the_form_opens_at_itself_after_a_mistake_even_without_javascript(client):
    # The form posts to /contact#contact, so the page shown with the errors
    # opens at the form rather than at the top of the landing page
    assert 'action="/contact#contact"' in client.get("/").get_data(as_text=True)
    response = client.post("/contact", data={"name": "", "email": "nope"})
    assert response.status_code == 400
    assert 'action="/contact#contact"' in response.get_data(as_text=True)


# Every enquiry reaches someone -------------------------------------------------


def saved_lead():
    return db.session.scalar(db.select(Lead))


def test_each_enquiry_is_logged_and_emailed(client, mail, caplog):
    caplog.set_level(logging.INFO)
    assert client.post("/contact", data=ENQUIRY).status_code == 302
    lead = saved_lead()
    assert f"Contact enquiry {lead.id} from Kiran Rao <kiran@shop.example> (Kiran Stores)" in caplog.text
    assert "wasn't emailed" not in caplog.text and "couldn't be emailed" not in caplog.text
    assert mail[-1].to == ["sales@orderflow.test"]


def test_names_stay_on_one_line_in_the_log_and_the_subject(client, mail, caplog):
    caplog.set_level(logging.INFO)
    forged = {**ENQUIRY, "name": "Kiran\nERROR orderflow: forged line", "company": "Kiran\r\nStores"}
    assert client.post("/contact", data=forged).status_code == 302
    lead = saved_lead()
    assert (lead.name, lead.company) == ("Kiran ERROR orderflow: forged line", "Kiran Stores")
    assert not any("\n" in record.getMessage() for record in caplog.records)
    assert mail[-1].subject == "New enquiry from Kiran ERROR orderflow: forged line"


def test_an_enquiry_without_contact_email_is_logged_loudly(app, client, mail, caplog):
    app.config["CONTACT_EMAIL"] = None
    assert client.post("/contact", data=ENQUIRY).status_code == 302
    lead = saved_lead()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [
        f"Contact enquiry {lead.id} wasn't emailed: CONTACT_EMAIL is not set. See it with `flask leads`."
    ]
    assert mail == []


def test_an_enquiry_whose_email_fails_is_logged_loudly(app, client, caplog):
    app.config.update(MAIL_BACKEND="resend", RESEND_API_KEY=None)
    assert client.post("/contact", data=ENQUIRY).status_code == 302
    assert f"Contact enquiry {saved_lead().id} was saved but couldn't be emailed" in caplog.text


def test_production_without_contact_email_warns_at_start_up(monkeypatch, caplog):
    for name, value in {
        "SECRET_KEY": "a real secret",
        "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg://u:p@db.internal/orderflow",
        "APP_BASE_URL": "https://orders.example.com",
        "MAIL_FROM": "OrderFlow <orders@example.com>",
        "RESEND_API_KEY": "re_test",
        "CONTACT_EMAIL": None,
    }.items():
        monkeypatch.setattr(config.ProductionConfig, name, value)
    create_app("production")
    assert "CONTACT_EMAIL is not set, so nobody is emailed about contact-form enquiries" in caplog.text

    caplog.clear()
    monkeypatch.setattr(config.ProductionConfig, "CONTACT_EMAIL", "hello@example.com")
    create_app("production")
    assert "CONTACT_EMAIL" not in caplog.text


def test_flask_leads_lists_enquiries_newest_first(app):
    runner = app.test_cli_runner()
    assert runner.invoke(args=["leads"]).output == "No enquiries yet.\n"

    db.session.add_all(
        [
            Lead(name="Meera", email="meera@iyer.example", created_at=datetime(2026, 10, 1, 4, 30, tzinfo=UTC)),
            Lead(
                name="Kiran",
                email="kiran@shop.example",
                company="Kiran Stores",
                message="We sell boxes.\nTo 40 shops.",
                created_at=datetime(2026, 10, 2, 9, 0, tzinfo=UTC),
            ),
        ]
    )
    db.session.commit()
    result = runner.invoke(args=["leads"])
    assert result.exit_code == 0
    assert result.output == (
        "#2  2 Oct 2026, 2:30 PM  Kiran <kiran@shop.example>, Kiran Stores\n"
        "    We sell boxes.\n"
        "    To 40 shops.\n"
        "\n"
        "#1  1 Oct 2026, 10:00 AM  Meera <meera@iyer.example>\n"
        "\n"
    )
    assert runner.invoke(args=["leads", "--limit", "1"]).output.startswith("#2 ")
