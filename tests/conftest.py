import re

import pytest
from flask.testing import FlaskClient

from orderflow import create_app
from orderflow.extensions import db
from orderflow.mail import outbox
from tests import factories


class FreshContextClient(FlaskClient):
    """Run every request in its own app context, as in production.

    Tests hold an app context open for setup. Without this, Flask would reuse
    it for requests, so `g` (and the user Flask-Login caches there) would leak
    from one request, or one test client, into the next.
    """

    def open(self, *args, **kwargs):
        with self.application.app_context():
            response = super().open(*args, **kwargs)
        db.session.expire_all()  # let the test see what the request changed
        return response


@pytest.fixture
def app():
    app = create_app("testing")
    app.test_client_class = FreshContextClient
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def mail(app):
    box = outbox()
    box.clear()
    return box


@pytest.fixture
def company(app):
    return factories.make_company("Kaveri Supplies")


@pytest.fixture
def owner(company):
    return factories.make_owner(company, email="owner@kaveri.example", first_name="Priya")


@pytest.fixture
def admin(company):
    return factories.make_admin(company, email="ops@kaveri.example", first_name="Arjun")


@pytest.fixture
def buyer(company):
    return factories.make_client(company, email="buyer@sunrise.example", first_name="Ananya")


@pytest.fixture
def other_company(app):
    """A second, unrelated tenant for isolation tests."""
    company = factories.make_company("Rival Traders")
    owner = factories.make_owner(company, email="owner@rival.example")
    client_user = factories.make_client(company, email="buyer@rival.example")
    product = factories.make_product(company, sku="RIVAL-1")
    return {"company": company, "owner": owner, "client": client_user, "product": product}


@pytest.fixture
def login(client):
    """Sign a user in through the real login form."""

    def _login(user, password=factories.PASSWORD):
        response = client.post("/login", data={"email": user.email, "password": password})
        assert response.status_code == 302, response.get_data(as_text=True)
        return response

    return _login


def link_from(message, path_fragment):
    """Pull the first link containing path_fragment out of an email, as a path."""
    match = re.search(r"https?://[^/\s]+(\S*" + re.escape(path_fragment) + r"\S*)", message.text)
    assert match, f"No link with {path_fragment!r} in email: {message.text}"
    return match.group(1)
