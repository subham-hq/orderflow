import re

import pytest

from orderflow.extensions import db
from orderflow.models import PENDING, Order
from orderflow.portal import cart as cart_views
from orderflow.services.orders import OrderError, place_order
from tests import factories


@pytest.fixture
def catalog(company):
    return {
        "carton": factories.make_product(
            company, sku="CTN-001", name="Corrugated carton", price_paise=4_250, gst_rate_bp=1800
        ),
        "rice": factories.make_product(
            company, sku="RCE-25K", name="Sona Masoori rice", price_paise=1_45_000, gst_rate_bp=500
        ),
    }


@pytest.fixture
def shopper(client, login, buyer):
    login(buyer)
    return client


def set_cart(client, cart):
    with client.session_transaction() as sess:
        sess["cart"] = {str(key): value for key, value in cart.items()}


def get_cart(client):
    with client.session_transaction() as sess:
        return dict(sess.get("cart") or {})


def flashes(client):
    with client.session_transaction() as sess:
        return [message for _category, message in sess.get("_flashes", [])]


def hidden_value(page, name):
    match = re.search(rf'name="{name}" type="hidden" value="([^"]*)"', page)
    assert match, f"no hidden {name} field"
    return match.group(1)


def checkout_form(client, **extra):
    """Open the cart page and return the fields its form would post."""
    page = client.get("/portal/cart").get_data(as_text=True)
    data = {
        "submission_key": hidden_value(page, "submission_key"),
        "cart_version": hidden_value(page, "cart_version"),
    }
    for product_id, quantity in re.findall(r'name="qty-(\d+)" value="([^"]*)"', page):
        data[f"qty-{product_id}"] = quantity
    data.update(extra)
    return data


def order_count():
    return db.session.scalar(db.select(db.func.count(Order.id)))


def test_cart_page_has_a_checkout_form_with_a_one_time_key(shopper, catalog):
    set_cart(shopper, {catalog["carton"].id: 1})
    page = shopper.get("/portal/cart").get_data(as_text=True)
    assert 'formaction="/portal/checkout"' in page
    assert "Notes for the supplier" in page
    assert 'maxlength="2000"' in page
    first = hidden_value(page, "submission_key")
    assert len(first) >= 16
    second = hidden_value(shopper.get("/portal/cart").get_data(as_text=True), "submission_key")
    assert first != second


def test_checkout_places_the_order_with_gst_and_clears_the_cart(shopper, catalog, buyer, mail):
    set_cart(shopper, {catalog["carton"].id: 10, catalog["rice"].id: 2})
    data = checkout_form(shopper, notes="  PO 4512\r\nDeliver to gate 2  ")
    response = shopper.post("/portal/checkout", data=data)

    assert response.status_code == 302
    assert response.headers["Location"] == "/portal/orders/ORD-000001"
    order = db.session.scalar(db.select(Order))
    assert order.client_id == buyer.id and order.status == PENDING
    assert order.subtotal_paise == 42_500 + 2_90_000
    assert order.tax_paise == 7_650 + 14_500
    assert order.total_paise == 3_54_650
    assert order.notes == "PO 4512\nDeliver to gate 2"
    assert order.submission_key == data["submission_key"]
    assert sorted((item.sku, item.quantity) for item in order.items) == [("CTN-001", 10), ("RCE-25K", 2)]
    assert get_cart(shopper) == {}
    assert flashes(shopper) == [
        "Order ORD-000001 is placed. Kaveri Supplies will review it, and we'll email you when its status changes."
    ]
    assert any(message.to == [buyer.email] for message in mail)


def test_double_submit_returns_the_same_order(shopper, catalog):
    set_cart(shopper, {catalog["carton"].id: 3})
    data = checkout_form(shopper)
    first = shopper.post("/portal/checkout", data=data)
    # A double click sends the same cookie twice: the cart is still in it
    set_cart(shopper, {catalog["carton"].id: 3})
    second = shopper.post("/portal/checkout", data=data)

    assert first.headers["Location"] == second.headers["Location"] == "/portal/orders/ORD-000001"
    assert order_count() == 1
    assert get_cart(shopper) == {}


def test_resubmitting_an_old_cart_page_keeps_the_new_cart(shopper, catalog):
    set_cart(shopper, {catalog["carton"].id: 3})
    data = checkout_form(shopper)
    shopper.post("/portal/checkout", data=data)
    # Later: a new cart, then the old page is submitted again from the browser history
    set_cart(shopper, {catalog["rice"].id: 1})
    response = shopper.post("/portal/checkout", data=data)

    assert response.headers["Location"] == "/portal/orders/ORD-000001"
    assert order_count() == 1
    assert get_cart(shopper) == {str(catalog["rice"].id): 1}
    assert flashes(shopper)[-1] == "You already placed this order as ORD-000001, so we didn't place it again."


def test_resubmitting_after_the_cart_was_cleared_finds_the_order(shopper, catalog):
    set_cart(shopper, {catalog["carton"].id: 3})
    data = checkout_form(shopper)
    shopper.post("/portal/checkout", data=data)
    response = shopper.post("/portal/checkout", data=data)
    assert response.headers["Location"] == "/portal/orders/ORD-000001"
    assert order_count() == 1


def test_a_race_lost_inside_place_order_is_reported_as_the_same_order(shopper, catalog, buyer, monkeypatch):
    set_cart(shopper, {catalog["carton"].id: 3})
    data = checkout_form(shopper)
    winner, _ = place_order(buyer, {str(catalog["carton"].id): 3}, submission_key="another-key-123456")
    monkeypatch.setattr(cart_views, "place_order", lambda *args, **kwargs: (winner, False))

    response = shopper.post("/portal/checkout", data=data)
    assert response.headers["Location"] == f"/portal/orders/{winner.number}"
    assert order_count() == 1
    assert get_cart(shopper) == {}


@pytest.mark.parametrize(
    "key", ["", "short", "has spaces in it, sixteen+", "x" * 65, "<script>alert(1)</script>", "a" * 22 + "\n"]
)
def test_a_missing_or_tampered_key_places_nothing_and_keeps_the_notes(shopper, catalog, key):
    set_cart(shopper, {catalog["carton"].id: 2})
    data = checkout_form(shopper, notes="Call before delivery", submission_key=key)
    response = shopper.post("/portal/checkout", data=data)

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "This page has expired. Check your cart and place the order again." in page
    assert "Call before delivery" in page
    assert "&lt;script&gt;" not in page  # the bad key isn't echoed back
    assert order_count() == 0
    assert get_cart(shopper) == {str(catalog["carton"].id): 2}


def test_notes_longer_than_the_limit_are_refused_and_kept_on_screen(shopper, catalog):
    set_cart(shopper, {catalog["carton"].id: 2})
    notes = "n" * 2001
    response = shopper.post("/portal/checkout", data=checkout_form(shopper, notes=notes))
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Keep notes to 2,000 characters or fewer." in page
    assert notes in page
    assert order_count() == 0


def test_notes_of_exactly_the_limit_are_accepted(shopper, catalog):
    set_cart(shopper, {catalog["carton"].id: 2})
    response = shopper.post("/portal/checkout", data=checkout_form(shopper, notes="n" * 2000))
    assert response.status_code == 302
    assert db.session.scalar(db.select(Order.notes)) == "n" * 2000


def test_blank_notes_are_stored_as_nothing(shopper, catalog):
    set_cart(shopper, {catalog["carton"].id: 2})
    shopper.post("/portal/checkout", data=checkout_form(shopper, notes="   \r\n  "))
    assert db.session.scalar(db.select(Order)).notes is None


def test_a_price_change_after_the_page_was_shown_asks_to_check_again(shopper, catalog):
    set_cart(shopper, {catalog["carton"].id: 2})
    data = checkout_form(shopper, notes="Urgent")
    catalog["carton"].price_paise = 5_000
    db.session.commit()

    response = shopper.post("/portal/checkout", data=data)
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Your cart changed since this page was opened." in page
    assert "₹118.00" in page  # 2 × ₹50 + 18% GST, today's price
    assert "Urgent" in page
    assert order_count() == 0

    # Placing it from the refreshed page works and charges today's price
    retry = {
        **data,
        "submission_key": hidden_value(page, "submission_key"),
        "cart_version": hidden_value(page, "cart_version"),
    }
    assert shopper.post("/portal/checkout", data=retry).status_code == 302
    assert db.session.scalar(db.select(Order.total_paise)) == 11_800


def test_changed_quantities_are_saved_and_shown_before_ordering(shopper, catalog):
    carton = catalog["carton"]
    set_cart(shopper, {carton.id: 2})
    data = checkout_form(shopper, **{f"qty-{carton.id}": "5", "notes": "Keep me"})
    response = shopper.post("/portal/checkout", data=data)
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "We&#39;ve updated your quantities. Check the new total, then place your order." in page
    assert "Keep me" in page
    assert get_cart(shopper) == {str(carton.id): 5}
    assert order_count() == 0


@pytest.mark.parametrize("quantity", ["-1", "2.5", "²", "٣", "1e3", "100001", "9" * 5000])
def test_bad_quantities_at_checkout_place_nothing(shopper, catalog, quantity):
    carton = catalog["carton"]
    set_cart(shopper, {carton.id: 2})
    response = shopper.post("/portal/checkout", data=checkout_form(shopper, **{f"qty-{carton.id}": quantity}))
    assert response.status_code == 200
    assert "Some quantities need fixing" in response.get_data(as_text=True)
    assert order_count() == 0
    assert get_cart(shopper) == {str(carton.id): 2}


def test_checkout_with_an_empty_cart_says_so(shopper, catalog):
    set_cart(shopper, {catalog["carton"].id: 2})
    data = checkout_form(shopper)
    set_cart(shopper, {})
    response = shopper.post("/portal/checkout", data=data)
    assert response.headers["Location"] == "/portal/cart"
    assert "Your cart is empty. Add at least one product to place an order." in flashes(shopper)
    assert order_count() == 0


def test_zeroing_every_line_at_checkout_places_nothing(shopper, catalog):
    carton = catalog["carton"]
    set_cart(shopper, {carton.id: 2})
    response = shopper.post("/portal/checkout", data=checkout_form(shopper, **{f"qty-{carton.id}": "0"}))
    assert response.headers["Location"] == "/portal/cart"
    assert order_count() == 0


def test_a_product_hidden_before_checkout_is_not_ordered(shopper, catalog):
    set_cart(shopper, {catalog["carton"].id: 2, catalog["rice"].id: 1})
    data = checkout_form(shopper)
    catalog["rice"].is_active = False
    db.session.commit()

    response = shopper.post("/portal/checkout", data=data)
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Sona Masoori rice is no longer available" in page
    assert order_count() == 0
    assert get_cart(shopper) == {str(catalog["carton"].id): 2}


def test_another_companys_product_in_a_tampered_cart_is_never_ordered(shopper, catalog, other_company):
    set_cart(shopper, {catalog["carton"].id: 2})
    data = checkout_form(shopper)
    set_cart(shopper, {catalog["carton"].id: 2, other_company["product"].id: 9})
    response = shopper.post("/portal/checkout", data=data)
    assert response.status_code == 302
    order = db.session.scalar(db.select(Order))
    assert [item.sku for item in order.items] == ["CTN-001"]


def test_an_order_error_is_shown_on_the_cart_with_the_notes_kept(shopper, catalog, monkeypatch):
    def refuse(*args, **kwargs):
        raise OrderError("Something about this order needs fixing.")

    monkeypatch.setattr(cart_views, "place_order", refuse)
    set_cart(shopper, {catalog["carton"].id: 2})
    response = shopper.post("/portal/checkout", data=checkout_form(shopper, notes="Gate 2"))
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Something about this order needs fixing." in page
    assert "Gate 2" in page
    assert get_cart(shopper) == {str(catalog["carton"].id): 2}


def test_updating_the_cart_keeps_the_notes_typed_so_far(shopper, catalog):
    carton = catalog["carton"]
    set_cart(shopper, {carton.id: 2})
    response = shopper.post("/portal/cart", data={f"qty-{carton.id}": "4", "notes": "Pack in twos"})
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Pack in twos" in page
    assert "Quantities updated." in page
    assert get_cart(shopper) == {str(carton.id): 4}


def test_checkout_is_only_for_signed_in_clients(client, login, owner, catalog):
    response = client.post("/portal/checkout", data={})
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    login(owner)
    set_cart(client, {catalog["carton"].id: 1})
    response = client.post("/portal/checkout", data={"submission_key": "a" * 22})
    assert response.headers["Location"].endswith("/app/")
    assert order_count() == 0
