"""Sign-up, sign-in and password forms say what to fix, never WTForms' generic messages."""

import pytest

from orderflow.services.accounts import invite_user
from orderflow.tokens import RESET_PASSWORD, make_token
from tests import factories
from tests.conftest import link_from

GENERIC = ["This field is required.", "Invalid email address.", "Field cannot be longer than", "Field must be"]


def errors(response):
    body = response.get_data(as_text=True).replace("&#39;", "'")
    for generic in GENERIC:
        assert generic not in body
    return body


def test_signup_explains_each_problem(client):
    body = errors(
        client.post(
            "/signup",
            data={"first_name": "", "company_name": " ", "email": "ananya@sunrise-retail", "password": ""},
        )
    )
    assert "Enter your first name." in body
    assert "Enter your company name." in body
    assert "Enter a valid email address, like name@business.in." in body
    assert "Choose a password." in body
    assert "Please accept the terms to continue." in body


def test_signup_explains_values_that_are_too_long(client):
    body = errors(
        client.post(
            "/signup",
            data={
                "first_name": "a" * 81,
                "last_name": "b" * 81,
                "company_name": "c" * 121,
                "email": f"{'d' * 250}@x.in",
                "password": "correct horse battery",
                "accept_terms": "y",
            },
        )
    )
    assert "Keep your first name to 80 characters or fewer." in body
    assert "Keep your last name to 80 characters or fewer." in body
    assert "Keep the company name to 120 characters or fewer." in body
    assert "Keep the email address to 254 characters or fewer." in body


def test_signup_asks_for_a_missing_email(client):
    assert "Enter your work email address." in errors(client.post("/signup", data={"email": ""}))


@pytest.mark.parametrize("path", ["/login", "/forgot-password"])
def test_email_forms_explain_missing_and_invalid_addresses(client, path):
    assert "Enter the email address you sign in with." in errors(client.post(path, data={"email": ""}))
    assert "Enter a valid email address, like name@business.in." in errors(client.post(path, data={"email": "nope"}))


def test_login_asks_for_the_password(client):
    assert "Enter your password." in errors(client.post("/login", data={"email": "a@b.in", "password": ""}))
    body = errors(client.post("/login", data={"email": "a@b.in", "password": "x" * 129}))
    assert "Passwords here are 128 characters or fewer." in body


def test_reset_form_explains_each_problem(client, owner):
    with client.application.test_request_context():
        path = f"/reset-password/{make_token(RESET_PASSWORD, owner)}"
    body = errors(client.post(path, data={"password": "", "confirm": ""}))
    assert "Choose a new password." in body and "Enter the new password again." in body
    assert "The passwords don't match." in errors(
        client.post(path, data={"password": "a brand new password", "confirm": "something else"})
    )


def test_invite_form_explains_each_problem(client, company, owner, mail):
    with client.application.test_request_context():
        invite_user(company, owner, "new@buyer.example", "client", "Ravi")
    body = errors(client.post(link_from(mail[-1], "/invite/"), data={"password": "", "confirm": ""}))
    assert "Choose a new password." in body and "Enter the new password again." in body


def test_change_password_form_explains_each_problem(client, login, owner):
    login(owner)
    body = errors(client.post("/account/password", data={"current_password": "", "password": "", "confirm": ""}))
    assert "Enter your current password." in body
    assert "Choose a new password." in body and "Enter the new password again." in body
    body = errors(
        client.post(
            "/account/password",
            data={"current_password": "x" * 129, "password": "a different password", "confirm": "a different password"},
        )
    )
    assert "That isn't your current password." in body
    assert owner.check_password(factories.PASSWORD)
