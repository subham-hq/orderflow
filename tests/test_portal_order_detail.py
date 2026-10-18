from datetime import UTC, datetime

import pytest

from orderflow.extensions import db
from orderflow.models import APPROVED, CANCELLED, FULFILLED, PENDING, REJECTED, OrderItem
from orderflow.money import MAX_QUANTITY
from orderflow.portal import orders as order_views
from orderflow.services.cart import MAX_CART_LINES
from orderflow.services.orders import OrderError
from tests import factories
from tests.test_portal_orders import flashes, get_cart, make_order, set_cart


@pytest.fixture
def catalog(company):
    return {
        "carton": factories.make_product(
            company, sku="CTN-001", name="Corrugated carton", price_paise=4_250, gst_rate_bp=1800, category="Packaging"
        ),
        "rice": factories.make_product(
            company, sku="RCE-25K", name="Sona Masoori rice", price_paise=1_45_000, gst_rate_bp=500
        ),
    }


@pytest.fixture
def shopper(client, login, buyer):
    login(buyer)
    return client


@pytest.fixture
def neighbour(company):
    """Another client of the same supplier."""
    return factories.make_client(company, email="neighbour@buyer.example", first_name="Vikram")


# One order


def test_order_detail_shows_lines_totals_notes_and_history(shopper, buyer, catalog):
    order = make_order(buyer, [(catalog["carton"], 10), (catalog["rice"], 2)], notes="PO 4512\nDeliver to gate 2")
    page = shopper.get(f"/portal/orders/{order.number}").get_data(as_text=True)
    assert f"Order {order.number}" in page
    assert "Corrugated carton" in page and "CTN-001" in page
    assert "₹42.50" in page and "₹76.50" in page and "(18%)" in page
    assert "₹3,325.00" in page and "₹221.50" in page and "₹3,546.50" in page
    assert '<p class="text-pre-line">PO 4512\nDeliver to gate 2</p>' in page
    assert "Order placed by Ananya" in page
    assert "hasn&#39;t reviewed this order yet" in page
    assert f'action="/portal/orders/{order.number}/cancel"' in page
    assert "data-confirm=" in page
    assert f'action="/portal/orders/{order.number}/reorder"' in page
    assert f'href="/portal/orders/{order.number}/invoice"' in page
    assert "Asia/Kolkata" in page
    assert "None" not in page


def test_order_times_are_shown_in_the_company_time_zone(shopper, buyer, catalog):
    order = make_order(buyer, [(catalog["carton"], 1)], placed_at=datetime(2026, 3, 1, 20, 0, tzinfo=UTC))
    page = shopper.get(f"/portal/orders/{order.number}").get_data(as_text=True)
    assert "2 Mar 2026, 1:30 AM" in page  # 20:00 UTC is 1:30 the next morning in India


@pytest.mark.parametrize("status", [APPROVED, FULFILLED, REJECTED, CANCELLED])
def test_cancel_is_only_offered_while_pending(shopper, buyer, catalog, status):
    order = make_order(buyer, [(catalog["carton"], 1)], status=status, reason="Out of stock this week")
    page = shopper.get(f"/portal/orders/{order.number}").get_data(as_text=True)
    assert "/cancel" not in page
    assert "/reorder" in page


def test_a_rejected_order_shows_the_reason(shopper, buyer, catalog):
    order = make_order(buyer, [(catalog["carton"], 1)], status=REJECTED, reason="Out of stock\nuntil Monday")
    page = shopper.get(f"/portal/orders/{order.number}").get_data(as_text=True)
    assert "couldn&#39;t accept this order" in page
    assert '<span class="text-pre-line">Out of stock\nuntil Monday</span>' in page
    assert "Rejected" in page


def test_editing_a_product_does_not_change_a_past_order(shopper, buyer, catalog):
    order = make_order(buyer, [(catalog["carton"], 2)])
    catalog["carton"].price_paise = 9_999
    catalog["carton"].name = "Renamed carton"
    db.session.commit()
    page = shopper.get(f"/portal/orders/{order.number}").get_data(as_text=True)
    assert "Corrugated carton" in page and "Renamed carton" not in page
    assert "₹42.50" in page and "₹99.99" not in page


def test_other_clients_and_companies_orders_are_not_found(shopper, buyer, neighbour, catalog, other_company):
    theirs = make_order(neighbour, [(catalog["carton"], 1)])
    rival = make_order(other_company["client"], [(other_company["product"], 1)])
    assert rival.number == "ORD-000001"  # the same number exists in the other company only

    for number in (theirs.number, rival.number, "ORD-424242", "x" * 40, "%2E%2E"):
        for path in (f"/portal/orders/{number}", f"/portal/orders/{number}/invoice"):
            assert shopper.get(path).status_code == 404, path
        assert shopper.post(f"/portal/orders/{number}/cancel").status_code == 404
        assert shopper.post(f"/portal/orders/{number}/reorder").status_code == 404
    assert theirs.status == PENDING and rival.status == PENDING
    assert get_cart(shopper) == {}


# Cancelling


def test_a_pending_order_can_be_cancelled_once(shopper, buyer, owner, catalog, mail):
    order = make_order(buyer, [(catalog["carton"], 1)])
    mail.clear()
    response = shopper.post(f"/portal/orders/{order.number}/cancel")
    assert response.headers["Location"] == f"/portal/orders/{order.number}"
    db.session.refresh(order)
    assert order.status == CANCELLED and order.cancelled_at is not None
    assert [event.to_status for event in order.events] == [PENDING, CANCELLED]
    assert flashes(shopper) == [f"Order {order.number} is cancelled. We've let Kaveri Supplies know."]
    assert [message.to for message in mail] == [[owner.email]]

    # A second click changes nothing and says so
    shopper.post(f"/portal/orders/{order.number}/cancel")
    db.session.refresh(order)
    assert len(order.events) == 2
    assert flashes(shopper)[-1] == f"Order {order.number} is already cancelled."

    page = shopper.get(f"/portal/orders/{order.number}").get_data(as_text=True)
    assert "This order was cancelled" in page and "/cancel" not in page


@pytest.mark.parametrize("status", [APPROVED, FULFILLED, REJECTED])
def test_orders_past_pending_cannot_be_cancelled(shopper, buyer, catalog, status):
    order = make_order(buyer, [(catalog["carton"], 1)], status=status, reason="No stock")
    events = len(order.events)
    response = shopper.post(f"/portal/orders/{order.number}/cancel")
    assert response.status_code == 302
    db.session.refresh(order)
    assert order.status == status and len(order.events) == events
    assert flashes(shopper) == [
        f"Order {order.number} can't be cancelled because it's already {status}. "
        "Contact Kaveri Supplies if something needs to change."
    ]


def test_cancel_needs_a_post(shopper, buyer, catalog):
    order = make_order(buyer, [(catalog["carton"], 1)])
    assert shopper.get(f"/portal/orders/{order.number}/cancel").status_code == 405
    assert order.status == PENDING


def test_a_refused_status_change_is_reported(shopper, buyer, catalog, monkeypatch):
    order = make_order(buyer, [(catalog["carton"], 1)])

    def refuse(*args, **kwargs):
        raise OrderError("This order can't be changed right now.")

    monkeypatch.setattr(order_views, "transition_order", refuse)
    shopper.post(f"/portal/orders/{order.number}/cancel")
    assert flashes(shopper) == ["This order can't be changed right now."]
    assert order.status == PENDING


# Reordering


def test_reorder_puts_the_items_back_in_the_cart(shopper, buyer, catalog):
    carton, rice = catalog["carton"], catalog["rice"]
    order = make_order(buyer, [(carton, 10), (rice, 2)], status=FULFILLED)
    response = shopper.post(f"/portal/orders/{order.number}/reorder")
    assert response.headers["Location"] == "/portal/cart"
    assert get_cart(shopper) == {str(carton.id): 10, str(rice.id): 2}
    assert flashes(shopper) == [
        f"Added 2 items from {order.number} to your cart at the quantities you ordered. "
        "Prices are today's, so check the total before you place the order."
    ]


def test_reorder_raises_smaller_quantities_keeps_larger_ones_and_is_safe_to_repeat(shopper, buyer, catalog):
    carton, rice = catalog["carton"], catalog["rice"]
    order = make_order(buyer, [(carton, 10), (rice, 2)])
    set_cart(shopper, {carton.id: 4, rice.id: 7})
    shopper.post(f"/portal/orders/{order.number}/reorder")
    assert get_cart(shopper) == {str(carton.id): 10, str(rice.id): 7}
    assert flashes(shopper)[0].startswith(f"Added 1 item from {order.number}")

    shopper.post(f"/portal/orders/{order.number}/reorder")
    assert get_cart(shopper) == {str(carton.id): 10, str(rice.id): 7}
    assert flashes(shopper)[-1] == f"Everything from {order.number} is already in your cart."


def test_reorder_skips_items_that_are_no_longer_available(shopper, buyer, company, catalog):
    carton, rice = catalog["carton"], catalog["rice"]
    gone = factories.make_product(company, name="Stretch film")
    order = make_order(buyer, [(carton, 3), (rice, 1), (gone, 2)])
    rice.is_active = False
    db.session.delete(gone)
    db.session.commit()

    shopper.post(f"/portal/orders/{order.number}/reorder")
    assert get_cart(shopper) == {str(carton.id): 3}
    assert flashes(shopper) == [
        f"Added 1 item from {order.number} to your cart at the quantities you ordered. "
        "Prices are today's, so check the total before you place the order.",
        "Stretch film and Sona Masoori rice are no longer available, so they weren't added.",
    ]


def test_reorder_with_one_unavailable_item_names_it(shopper, buyer, catalog):
    order = make_order(buyer, [(catalog["carton"], 3), (catalog["rice"], 1)])
    catalog["rice"].is_active = False
    db.session.commit()
    shopper.post(f"/portal/orders/{order.number}/reorder")
    assert flashes(shopper)[-1] == "Sona Masoori rice is no longer available, so it wasn't added."


def test_reorder_with_nothing_available_stays_on_the_order(shopper, buyer, catalog):
    order = make_order(buyer, [(catalog["carton"], 3)])
    catalog["carton"].is_active = False
    db.session.commit()
    response = shopper.post(f"/portal/orders/{order.number}/reorder")
    assert response.headers["Location"] == f"/portal/orders/{order.number}"
    assert get_cart(shopper) == {}
    assert flashes(shopper) == [f"None of the items on {order.number} are available any more, so nothing was added."]


def test_reorder_caps_quantities(shopper, buyer, catalog):
    order = make_order(buyer, [(catalog["carton"], 5)])
    db.session.add(
        OrderItem(
            order=order,
            product_id=catalog["carton"].id,
            sku="CTN-001",
            name="Corrugated carton",
            unit_price_paise=4_250,
            quantity=MAX_QUANTITY,
            line_subtotal_paise=0,
            line_tax_paise=0,
            line_total_paise=0,
        )
    )
    db.session.commit()
    shopper.post(f"/portal/orders/{order.number}/reorder")
    assert get_cart(shopper) == {str(catalog["carton"].id): MAX_QUANTITY}


def test_reorder_into_a_full_cart_says_what_did_not_fit(shopper, buyer, company, catalog):
    filler = [factories.make_product(company) for _ in range(MAX_CART_LINES)]
    order = make_order(buyer, [(catalog["carton"], 3)])
    set_cart(shopper, {product.id: 1 for product in filler})
    response = shopper.post(f"/portal/orders/{order.number}/reorder")
    assert response.headers["Location"] == f"/portal/orders/{order.number}"
    assert str(catalog["carton"].id) not in get_cart(shopper)
    assert flashes(shopper) == [
        "Your cart can hold up to 100 different products, so Corrugated carton couldn't be added. "
        "Place an order first, then reorder again."
    ]


def test_reorder_into_a_full_cart_with_an_unavailable_item(shopper, buyer, company, catalog):
    filler = [factories.make_product(company) for _ in range(MAX_CART_LINES)]
    order = make_order(buyer, [(catalog["carton"], 3), (catalog["rice"], 1)])
    catalog["rice"].is_active = False
    db.session.commit()
    set_cart(shopper, {product.id: 1 for product in filler})
    shopper.post(f"/portal/orders/{order.number}/reorder")
    assert flashes(shopper) == [
        "Sona Masoori rice is no longer available, so it wasn't added.",
        "Your cart can hold up to 100 different products, so Corrugated carton couldn't be added. "
        "Place an order first, then reorder again.",
    ]


# Invoice


def test_invoice_shows_the_order_with_a_way_back(shopper, buyer, company, catalog):
    company.legal_name = "Kaveri Supplies Private Limited"
    company.gstin = "29ABCDE1234F1Z5"
    db.session.commit()
    order = make_order(buyer, [(catalog["carton"], 10)], notes="PO 4512")
    response = shopper.get(f"/portal/orders/{order.number}/invoice")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Proforma invoice" in page
    assert "Kaveri Supplies Private Limited" in page and "29ABCDE1234F1Z5" in page
    assert order.number in page and "₹501.50" in page
    assert "Buyer Pvt Ltd" in page and "PO 4512" in page
    assert f'href="/portal/orders/{order.number}"' in page
    assert "None" not in page
