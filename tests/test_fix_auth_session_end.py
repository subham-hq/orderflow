"""When a session has ended, the sign-in page says so and leads back to a page that opens."""

import pytest

from orderflow.extensions import db
from orderflow.models import PENDING
from orderflow.services.orders import place_order
from tests import factories

SIGN_IN_MESSAGE = "Please sign in to continue."
SIGNED_OUT_MESSAGE = "You were signed out, so that didn&#39;t go through. Sign in and try again."


def _end_sessions(user):
    """What a password change on another device, a reset or a suspension does."""
    user.end_all_sessions()
    db.session.commit()


@pytest.mark.parametrize("path", ["/app/", "/app/orders", "/portal/", "/portal/orders", "/account/password"])
def test_signed_out_visitors_are_told_to_sign_in(client, path):
    response = client.get(path)
    assert response.headers["Location"].startswith("/login?next=")
    page = client.get(response.headers["Location"])
    assert SIGN_IN_MESSAGE in page.get_data(as_text=True)


def test_an_ended_session_explains_itself_and_returns_to_the_page(client, login, buyer):
    login(buyer)
    _end_sessions(buyer)
    response = client.get("/portal/orders?status=pending")
    assert response.headers["Location"] == "/login?next=/portal/orders?status%3Dpending"
    assert SIGN_IN_MESSAGE in client.get(response.headers["Location"]).get_data(as_text=True)


def test_a_suspended_client_is_told_to_sign_in(client, login, buyer):
    login(buyer)
    buyer.active = False
    _end_sessions(buyer)
    page = client.get("/portal/", follow_redirects=True)
    assert page.request.path == "/login"
    assert SIGN_IN_MESSAGE in page.get_data(as_text=True)


def test_a_form_sent_after_the_session_ended_comes_back_to_its_page(client, login, company, owner, buyer):
    product = factories.make_product(company)
    order, _ = place_order(buyer, {product.id: 1})
    detail = f"/app/orders/{order.number}"
    login(owner)
    _end_sessions(owner)  # e.g. the password was changed on another device

    response = client.post(
        f"{detail}/status", data={"to_status": "approved"}, headers={"Referer": f"http://localhost{detail}"}
    )
    assert response.headers["Location"] == f"/login?next={detail}"
    assert SIGNED_OUT_MESSAGE in client.get(response.headers["Location"]).get_data(as_text=True)

    signed_in = client.post(response.headers["Location"], data={"email": owner.email, "password": factories.PASSWORD})
    assert signed_in.headers["Location"] == detail
    assert client.get(detail).status_code == 200
    db.session.refresh(order)
    assert order.status == PENDING  # nothing happened while signed out


def test_a_form_sent_after_the_session_ended_without_a_referrer_goes_home(client, login, company, buyer):
    product = factories.make_product(company)
    login(buyer)
    _end_sessions(buyer)

    response = client.post("/portal/cart/add", data={"product_id": product.id, "quantity": 1})
    assert response.headers["Location"] == "/login"
    signed_in = client.post("/login", data={"email": buyer.email, "password": factories.PASSWORD})
    assert signed_in.headers["Location"] == "/portal/"


@pytest.mark.parametrize(
    "referrer",
    [
        "https://evil.example/app/orders",  # another site
        "https://localhost/app/orders",  # another scheme
        "http://localhost/app/team/invite",  # an address that only accepts POST
        "http://localhost/no-such-page",
        "not a url",
    ],
)
def test_only_pages_on_this_site_are_used_from_the_referrer(client, login, owner, referrer):
    login(owner)
    _end_sessions(owner)
    response = client.post("/app/team/invite", data={"email": "x@example.com"}, headers={"Referer": referrer})
    assert response.headers["Location"] == "/login"


@pytest.mark.parametrize("target", ["/portal/checkout", "/portal/cart/add", "/app/team/invite", "/no-such-page"])
def test_sign_in_ignores_a_next_page_that_cannot_be_opened(client, buyer, target):
    response = client.post(
        "/login", query_string={"next": target}, data={"email": buyer.email, "password": factories.PASSWORD}
    )
    assert response.headers["Location"] == "/portal/"


def test_sign_in_follows_a_next_page_missing_its_trailing_slash(client, buyer):
    response = client.post(
        "/login", query_string={"next": "/portal"}, data={"email": buyer.email, "password": factories.PASSWORD}
    )
    assert response.headers["Location"] == "/portal"


def test_next_from_a_form_must_also_be_a_page(client, login, company, owner):
    product = factories.make_product(company)
    login(owner)
    response = client.post(f"/app/products/{product.id}/status", data={"active": "0", "next": "/app/team/invite"})
    assert response.headers["Location"] == "/app/products"
    response = client.post(f"/app/products/{product.id}/status", data={"active": "1", "next": "/app/products?page=2"})
    assert response.headers["Location"] == "/app/products?page=2"
