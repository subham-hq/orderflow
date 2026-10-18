from datetime import timedelta

import pytest

from orderflow.extensions import db
from orderflow.models import CLIENT, User
from orderflow.services.orders import place_order
from orderflow.timeutil import utcnow
from tests import factories
from tests.conftest import link_from

INVITE = {
    "first_name": "Meera",
    "last_name": "Iyer",
    "buyer_company": "Iyer Traders",
    "email": "meera@iyertraders.example",
    "phone": "+91 98450 12345",
}


@pytest.fixture
def signed_in(login, owner):
    login(owner)
    return owner


@pytest.fixture
def invited(company, owner):
    """A client who hasn't accepted their invitation yet."""
    return factories.make_client(
        company,
        email="invited@sunrise.example",
        first_name="Ravi",
        buyer_company="Sunrise Retail",
        password=None,
        verified=False,
        invited_by=owner,
        invite_sent_at=utcnow() - timedelta(days=2),
    )


def text(response):
    return response.get_data(as_text=True)


def get_user(user_id):
    return db.session.get(User, user_id)


def clients_named(first_name):
    return db.session.scalars(db.select(User).where(User.first_name == first_name)).all()


def sign_in(app, user):
    """A second browser, signed in as user."""
    browser = app.test_client()
    response = browser.post("/login", data={"email": user.email, "password": factories.PASSWORD})
    assert response.status_code == 302
    return browser


def make_order(buyer, company, status=None, price_paise=10_000, quantity=1):
    product = factories.make_product(company, price_paise=price_paise, gst_rate_bp=0)
    order, _ = place_order(buyer, {product.id: quantity})
    if status:
        order.status = status
        db.session.commit()
    return order


# Access ----------------------------------------------------------------------

ENDPOINTS = [("get", "/app/clients/invite"), ("post", "/app/clients/invite")]
CHANGES = {**INVITE, "active": "0"}


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_anonymous_users_are_sent_to_sign_in(client, buyer, mail, method, path):
    response = getattr(client, method)(path.format(id=buyer.id), data=CHANGES)
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    buyer = get_user(buyer.id)
    assert buyer.active and buyer.first_name == "Ananya" and mail == []


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_clients_are_sent_back_to_their_portal(client, login, company, buyer, mail, method, path):
    other = factories.make_client(company, email="other@buyer.example", first_name="Other")
    login(buyer)
    response = getattr(client, method)(path.format(id=other.id), data=CHANGES)
    assert response.status_code == 302 and response.headers["Location"] == "/portal/"
    other = get_user(other.id)
    assert other.active and other.first_name == "Other" and mail == []
    assert clients_named("Meera") == []


def test_admins_can_invite_clients(client, login, admin, mail):
    login(admin)
    assert client.post("/app/clients/invite", data=INVITE).status_code == 302
    [meera] = clients_named("Meera")
    assert meera.invited_by_id == admin.id and len(mail) == 1


# Invite ----------------------------------------------------------------------


def test_invite_form(client, signed_in):
    body = text(client.get("/app/clients/invite"))
    for name in ("first_name", "last_name", "buyer_company", "email", "phone"):
        assert f'name="{name}"' in body
    assert "Send invitation" in body and 'type="email"' in body and "None" not in body


def test_inviting_a_client_creates_an_invited_account_and_emails_them(client, signed_in, company, mail):
    response = client.post("/app/clients/invite", data={**INVITE, "email": "  Meera@IyerTraders.Example "})
    [meera] = clients_named("Meera")
    assert response.status_code == 302 and response.headers["Location"] == f"/app/clients/{meera.id}"
    assert (meera.company_id, meera.role, meera.email) == (company.id, CLIENT, "meera@iyertraders.example")
    assert (meera.last_name, meera.buyer_company, meera.phone) == ("Iyer", "Iyer Traders", "+91 98450 12345")
    assert meera.invited_by_id == signed_in.id and meera.invite_sent_at is not None
    assert not meera.has_accepted_invite and meera.active
    assert len(mail) == 1 and mail[0].to == ["meera@iyertraders.example"]
    assert "/invite/" in mail[0].text
    page = text(client.get(response.headers["Location"]))
    assert "Invitation sent to meera@iyertraders.example." in page
    assert "hasn't accepted their invitation yet" in page


def test_invited_client_can_accept_and_reach_the_portal(app, client, signed_in, mail):
    client.post("/app/clients/invite", data=INVITE)
    browser = app.test_client()
    link = link_from(mail[0], "/invite/")
    response = browser.post(link, data={"password": "a brand new phrase", "confirm": "a brand new phrase"})
    assert response.headers["Location"] == "/portal/"
    page = text(client.get("/app/clients?status=active"))
    assert "meera@iyertraders.example" in page


def test_optional_fields_can_be_blank(client, signed_in):
    client.post("/app/clients/invite", data={**INVITE, "last_name": " ", "phone": ""})
    [meera] = clients_named("Meera")
    assert meera.last_name is None and meera.phone is None


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"first_name": "  "}, "Enter their first name."),
        ({"buyer_company": ""}, "Enter the name of the business they buy for."),
        ({"email": ""}, "Enter their email address."),
        ({"email": "not-an-email"}, "Enter a valid email address"),
        ({"email": "meera@"}, "Enter a valid email address"),
        ({"phone": "call me"}, "Enter a phone number"),
        ({"phone": "12"}, "Enter a phone number"),
        ({"phone": "१२३४५६७८९०"}, "Enter a phone number"),
        ({"first_name": "x" * 81}, "Keep the name to 80 characters or fewer."),
        ({"buyer_company": "x" * 161}, "Keep the business name to 160 characters or fewer."),
        ({"email": "a" * 250 + "@x.in"}, "Keep the email address to 254 characters or fewer."),
    ],
)
def test_invite_validation_keeps_what_was_typed(client, signed_in, mail, changes, message):
    data = {**INVITE, **changes}
    response = client.post("/app/clients/invite", data=data)
    body = text(response)
    assert response.status_code == 200 and message in body
    assert 'aria-invalid="true"' in body
    for name, value in INVITE.items():
        if name not in changes:
            assert f'value="{value}"' in body
    assert clients_named("Meera") == [] and mail == []


def test_email_used_by_another_company_is_refused(client, signed_in, other_company, mail):
    response = client.post("/app/clients/invite", data={**INVITE, "email": "BUYER@rival.example"})
    assert "Someone with this email already has an OrderFlow account." in text(response)
    assert clients_named("Meera") == [] and mail == []


def test_email_of_an_existing_client_points_to_them(client, signed_in, buyer):
    body = text(client.post("/app/clients/invite", data={**INVITE, "email": buyer.email}))
    assert "buyer@sunrise.example is already one of your clients (Ananya Test (Buyer Pvt Ltd))" in body
    assert 'value="Meera"' in body


def test_email_of_a_teammate_is_refused(client, signed_in, admin):
    body = text(client.post("/app/clients/invite", data={**INVITE, "email": admin.email}))
    assert "belongs to someone on your team" in body
    assert get_user(admin.id).role == "admin"


def test_invite_race_on_the_same_email_is_reported(client, signed_in, monkeypatch, mail):
    from sqlalchemy.exc import IntegrityError

    from orderflow.admin import clients as views

    def racing_invite(*args, **kwargs):
        raise IntegrityError("INSERT", {}, Exception("duplicate email"))

    monkeypatch.setattr(views, "invite_user", racing_invite)
    response = client.post("/app/clients/invite", data=INVITE)
    assert response.status_code == 200
    assert "Someone with this email already has an OrderFlow account." in text(response)
