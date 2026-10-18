"""A sign-up nobody confirmed stops holding its email address after a day.

Anyone can sign up with someone else's address. They can't confirm it, so
after 24 hours the next sign-up or invitation for that address removes the
empty workspace instead of being refused.
"""

from datetime import timedelta

import pytest

from orderflow.extensions import db
from orderflow.models import Bulletin, Company, User
from orderflow.services.accounts import AccountError, invite_user
from orderflow.timeutil import utcnow
from tests import factories
from tests.test_auth import SIGNUP

ADDRESS = SIGNUP["email"]


def _signup_by_someone_else(hours_ago, verified=False):
    squatter = factories.make_owner(email=ADDRESS, verified=verified, first_name="Squatter")
    squatter.created_at = utcnow() - timedelta(hours=hours_ago)
    db.session.commit()
    return squatter


def _users_with_address():
    return db.session.scalars(db.select(User).where(User.email == ADDRESS)).all()


def test_signup_takes_over_an_address_left_unconfirmed_for_a_day(client, mail):
    squatter = _signup_by_someone_else(hours_ago=25)
    old_company_name = squatter.company.name

    response = client.post("/signup", data=SIGNUP)
    assert response.headers["Location"].endswith("/verify")

    [owner] = _users_with_address()
    assert owner.first_name == "Meera" and owner.company.name == "Iyer Traders"
    assert db.session.scalars(db.select(Company.name)).all() == ["Iyer Traders"]
    assert old_company_name != "Iyer Traders"
    assert len(mail) == 1 and mail[0].to == [ADDRESS]


def test_a_recent_unconfirmed_signup_still_holds_its_address(client):
    squatter = _signup_by_someone_else(hours_ago=23)
    response = client.post("/signup", data=SIGNUP)
    assert response.status_code == 200
    assert "already exists" in response.get_data(as_text=True)
    assert _users_with_address() == [squatter]


def test_a_confirmed_owner_is_never_removed(client):
    owner = _signup_by_someone_else(hours_ago=24 * 30, verified=True)
    response = client.post("/signup", data=SIGNUP)
    assert "already exists" in response.get_data(as_text=True)
    assert _users_with_address() == [owner]


def test_a_workspace_with_a_bulletin_is_kept(client):
    squatter = _signup_by_someone_else(hours_ago=48)
    db.session.add(Bulletin(company=squatter.company, title="Hello", body="Hi"))
    db.session.commit()
    assert "already exists" in client.post("/signup", data=SIGNUP).get_data(as_text=True)
    assert _users_with_address() == [squatter]


def test_a_workspace_with_a_teammate_is_kept(client):
    squatter = _signup_by_someone_else(hours_ago=48)
    factories.make_admin(squatter.company, email="teammate@example.com")
    assert "already exists" in client.post("/signup", data=SIGNUP).get_data(as_text=True)
    assert _users_with_address() == [squatter]


def test_a_workspace_with_products_is_kept(client):
    squatter = _signup_by_someone_else(hours_ago=48)
    factories.make_product(squatter.company)
    assert "already exists" in client.post("/signup", data=SIGNUP).get_data(as_text=True)
    assert _users_with_address() == [squatter]


def test_an_invitation_takes_over_an_address_left_unconfirmed_for_a_day(app, company, owner, mail):
    squatter = _signup_by_someone_else(hours_ago=25)
    squatter_company = squatter.company.name
    with app.test_request_context():
        invited, sent = invite_user(company, owner, ADDRESS, "client", "Meera", buyer_company="Iyer Traders")
    assert sent
    assert _users_with_address() == [invited]
    assert invited.company_id == company.id
    assert db.session.scalars(db.select(Company.name).where(Company.name == squatter_company)).all() == []


def test_an_invitation_to_a_recent_unconfirmed_signup_is_refused(app, company, owner):
    _signup_by_someone_else(hours_ago=2)
    with app.test_request_context(), pytest.raises(AccountError):
        invite_user(company, owner, ADDRESS, "client", "Meera")
