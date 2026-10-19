"""Opening someone else's confirmation link while signed in says whose email was confirmed."""

from orderflow.tokens import VERIFY_EMAIL, make_token
from tests import factories

WELCOME = "Your email is confirmed. Welcome to OrderFlow!"


def _link(app, user):
    with app.test_request_context():
        return f"/verify/{make_token(VERIFY_EMAIL, user)}"


def test_a_signed_in_user_is_told_the_link_was_for_someone_else(app, client, login, owner):
    newcomer = factories.make_owner(email="newowner@example.com", verified=False)
    login(owner)

    response = client.get(_link(app, newcomer))
    assert response.headers["Location"] == "/app/"
    page = client.get("/app/").get_data(as_text=True).replace("&#39;", "'")
    assert "We confirmed newowner@example.com. You're still signed in as owner@kaveri.example" in page
    assert WELCOME not in page
    assert newcomer.is_verified


def test_an_unconfirmed_user_is_not_told_their_own_email_is_confirmed(app, client):
    first = factories.make_owner(email="a@example.com", verified=False)
    second = factories.make_owner(email="b@example.com", verified=False)
    client.post("/login", data={"email": first.email, "password": factories.PASSWORD})

    page = client.get(_link(app, second), follow_redirects=True)
    assert page.request.path == "/verify"
    body = page.get_data(as_text=True)
    assert WELCOME not in body and "We confirmed b@example.com." in body
    assert second.is_verified and not first.is_verified


def test_your_own_link_still_welcomes_you(app, client):
    newcomer = factories.make_owner(email="newowner@example.com", verified=False)
    client.post("/login", data={"email": newcomer.email, "password": factories.PASSWORD})
    page = client.get(_link(app, newcomer), follow_redirects=True)
    assert page.request.path == "/app/"
    assert WELCOME in page.get_data(as_text=True)


def test_a_signed_out_visitor_is_welcomed_and_asked_to_sign_in(app, client):
    newcomer = factories.make_owner(email="newowner@example.com", verified=False)
    page = client.get(_link(app, newcomer), follow_redirects=True)
    assert page.request.path == "/login"
    assert WELCOME in page.get_data(as_text=True)
