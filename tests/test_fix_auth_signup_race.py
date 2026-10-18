"""Two sign-ups for the same email at the same moment: one wins, the other gets a normal form error."""

import os
import threading

import pytest

from orderflow.extensions import db
from orderflow.models import Company, User
from orderflow.services import accounts
from tests import factories
from tests.test_auth import SIGNUP


def test_signup_that_loses_the_race_shows_the_duplicate_email_error(client, monkeypatch):
    # The other request has already created the account by the time this one saves
    factories.make_owner(email=SIGNUP["email"])
    monkeypatch.setattr(accounts, "email_taken", lambda email: False)
    companies_before = db.session.scalar(db.select(db.func.count(Company.id)))

    response = client.post("/signup", data=SIGNUP)
    assert response.status_code == 200
    assert "An account with this email already exists." in response.get_data(as_text=True)
    assert db.session.scalar(db.select(db.func.count(Company.id))) == companies_before
    assert db.session.scalar(db.select(db.func.count(User.id)).where(User.email == SIGNUP["email"])) == 1


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs a database that takes parallel writes")
def test_parallel_signups_for_one_email_never_fail(app):
    attempts = 6
    barrier = threading.Barrier(attempts)
    statuses = []

    def sign_up():
        with app.app_context():  # each thread is its own worker
            client = app.test_client()
            barrier.wait()
            try:
                statuses.append(client.post("/signup", data=SIGNUP).status_code)
            except Exception:
                statuses.append(500)  # testing re-raises what would be a 500 page

    threads = [threading.Thread(target=sign_up) for _ in range(attempts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(statuses) == [200] * (attempts - 1) + [302]
    assert db.session.scalar(db.select(db.func.count(Company.id))) == 1
