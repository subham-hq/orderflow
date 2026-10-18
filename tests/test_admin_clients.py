from datetime import timedelta

import pytest

from orderflow.extensions import db
from orderflow.models import User
from orderflow.services.orders import place_order
from orderflow.timeutil import utcnow
from tests import factories

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

ENDPOINTS = [("get", "/app/clients"), ("get", "/app/clients?q=a&status=invited")]
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


def test_admins_can_see_clients(client, login, admin, buyer):
    login(admin)
    response = client.get("/app/clients")
    assert response.status_code == 200 and buyer.email in text(response)


# List ------------------------------------------------------------------------


def test_empty_list_explains_what_to_do(client, signed_in, other_company):
    body = text(client.get("/app/clients"))
    assert "Invite your first client" in body and "/app/clients/invite" in body
    assert other_company["client"].email not in body
    assert 'aria-current="page"' in body


def test_list_shows_each_client_with_status_orders_and_last_sign_in(
    client, signed_in, company, buyer, invited, admin, other_company
):
    suspended = factories.make_client(company, email="gone@buyer.example", buyer_company="Zed Mart", active=False)
    buyer.last_login_at = utcnow() - timedelta(days=3)
    db.session.commit()
    make_order(buyer, company)
    make_order(buyer, company)
    body = text(client.get("/app/clients"))
    assert "Buyer Pvt Ltd" in body and "Ananya Test" in body and "buyer@sunrise.example" in body
    assert "Sunrise Retail" in body and "Ravi Test" in body
    assert body.index("Buyer Pvt Ltd") < body.index("Sunrise Retail") < body.index("Zed Mart")
    assert ">Active</span>" in body and ">Invited</span>" in body and ">Suspended</span>" in body
    assert "3 days ago" in body and "Never" in body and "Invited 2 days ago" in body
    assert '<td class="num">2</td>' in body and '<td class="num">0</td>' in body
    assert suspended.email in body
    assert admin.email not in body and signed_in.email not in body  # staff are on the Team page
    assert other_company["client"].email not in body
    assert "None" not in body
    assert "Showing 1–3 of 3 clients" in body


@pytest.mark.parametrize(
    ("query", "found"),
    [
        ("ananya", "buyer@sunrise.example"),
        ("Ananya Test", "buyer@sunrise.example"),
        ("RAVI", "invited@sunrise.example"),
        ("@sunrise.example", "invited@sunrise.example"),
        ("pvt", "buyer@sunrise.example"),
        ("sunrise retail", "invited@sunrise.example"),
    ],
)
def test_search_by_name_email_and_business(client, signed_in, buyer, invited, query, found):
    body = text(client.get("/app/clients", query_string={"q": query}))
    assert found in body
    if query in ("ananya", "pvt", "Ananya Test"):
        assert "invited@sunrise.example" not in body


def test_search_treats_wildcards_literally_and_never_crosses_companies(client, signed_in, company, other_company):
    factories.make_client(company, email="a_b@x.example", first_name="Under")
    factories.make_client(company, email="ab@x.example", first_name="Plain")
    body = text(client.get("/app/clients?q=a_b"))
    assert "a_b@x.example" in body and "ab@x.example" not in body
    body = text(client.get("/app/clients?q=rival"))
    assert "No clients match" in body and "buyer@rival.example" not in body


def test_status_filter_and_counts(client, signed_in, company, buyer, invited):
    factories.make_client(company, email="gone@buyer.example", active=False)
    body = text(client.get("/app/clients?status=invited"))
    assert "invited@sunrise.example" in body and "buyer@sunrise.example" not in body
    assert 'aria-current="page">Invited <span class="muted">1</span>' in body
    assert 'All <span class="muted">3</span>' in body and 'Suspended <span class="muted">1</span>' in body
    body = text(client.get("/app/clients?status=suspended"))
    assert "gone@buyer.example" in body and "invited@sunrise.example" not in body
    body = text(client.get("/app/clients?status=active"))
    assert "buyer@sunrise.example" in body and "gone@buyer.example" not in body
    body = text(client.get("/app/clients?status=bogus"))
    assert "gone@buyer.example" in body and "buyer@sunrise.example" in body


def test_suspended_invitee_counts_as_suspended(client, signed_in, invited):
    invited.active = False
    db.session.commit()
    body = text(client.get("/app/clients?status=suspended"))
    assert "invited@sunrise.example" in body and 'Invited <span class="muted">0</span>' in body


def test_search_and_status_combine(client, signed_in, company, buyer, invited):
    body = text(client.get("/app/clients?q=sunrise&status=active"))
    assert "buyer@sunrise.example" in body and "invited@sunrise.example" not in body
    assert 'name="status" value="active"' in body
    assert "/app/clients?q=sunrise&amp;status=invited" in body


def test_list_paginates_by_25(client, signed_in, company):
    for n in range(27):
        factories.make_client(company, email=f"c{n:02d}@buyers.example", buyer_company=f"Shop {n:02d}")
    first = text(client.get("/app/clients"))
    assert "Shop 24" in first and "Shop 25" not in first and "Page 1 of 2" in first
    second = text(client.get("/app/clients?page=2"))
    assert "Shop 26" in second and "Shop 00" not in second
    assert "Shop 26" in text(client.get("/app/clients?page=999"))
    assert "Shop 00" in text(client.get("/app/clients?page=zero"))
