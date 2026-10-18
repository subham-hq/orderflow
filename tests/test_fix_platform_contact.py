"""The landing page's contact form: its messages, and what happens to each enquiry."""

import re

from orderflow.extensions import db
from orderflow.models import Lead


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
