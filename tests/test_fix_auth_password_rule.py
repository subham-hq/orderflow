"""The "don't base your password on your email" rule: no false alarms, and the same everywhere."""

import pytest

from orderflow.services.accounts import invite_user, password_problem
from orderflow.tokens import RESET_PASSWORD, make_token
from tests import factories
from tests.conftest import link_from
from tests.test_auth import SIGNUP

EMAIL_RULE = "Don't base your password on your email address."


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("a@acme.in", "tangerine-sky"),
        ("me@acme.in", "homemade pie!"),
        ("jo@acme.in", "major tom 77"),
        ("ab@x.in", "fabulous cat"),
        ("ops@x.in", "tropical shops"),
        ("info@x.in", "information age"),
        ("raj@x.in", "strawberry jam"),
    ],
)
def test_ordinary_passwords_are_not_mistaken_for_the_email(email, password):
    assert password_problem(password, email) is None


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("priya@acme.in", "priya12345"),
        ("priya@acme.in", "Priya@acme.in"),
        ("priya@acme.in", "priyapriya1"),
        ("priya+orders@acme.in", "priya2024!!"),
        ("meera@iyertraders.example", "meera12345"),
        ("sales@x.in", "salesteam2024"),
    ],
)
def test_passwords_made_from_the_email_are_refused(email, password):
    assert password_problem(password, email) == EMAIL_RULE


def test_signup_accepts_an_ordinary_password_for_a_short_email(client):
    response = client.post("/signup", data={**SIGNUP, "email": "a@acme.in", "password": "tangerine-sky"})
    assert response.headers["Location"].endswith("/verify")


def test_invitee_with_a_short_email_can_choose_an_ordinary_password(client, company, owner, mail):
    with client.application.test_request_context():
        invite_user(company, owner, "me@acme.in", "client", "Meera")
    response = client.post(
        link_from(mail[-1], "/invite/"), data={"password": "homemade pie!", "confirm": "homemade pie!"}
    )
    assert response.headers["Location"].endswith("/portal/")


def test_reset_applies_the_email_rule(client, company):
    priya = factories.make_owner(company, email="priya@acme.in")
    with client.application.test_request_context():
        path = f"/reset-password/{make_token(RESET_PASSWORD, priya)}"
    response = client.post(path, data={"password": "priya12345", "confirm": "priya12345"})
    assert response.status_code == 200
    assert EMAIL_RULE.replace("'", "&#39;") in response.get_data(as_text=True)
    assert priya.check_password(factories.PASSWORD)


def test_change_password_applies_the_email_rule(client, login, company):
    priya = factories.make_owner(company, email="priya@acme.in")
    login(priya)
    response = client.post(
        "/account/password",
        data={"current_password": factories.PASSWORD, "password": "priya12345", "confirm": "priya12345"},
    )
    assert response.status_code == 200
    assert EMAIL_RULE.replace("'", "&#39;") in response.get_data(as_text=True)
    assert priya.check_password(factories.PASSWORD)
