import pytest

from orderflow.extensions import db
from orderflow.models import APPROVED, CANCELLED, FULFILLED, PENDING, REJECTED, Order
from orderflow.services.orders import (
    OrderError,
    allowed_transitions,
    parse_quantity,
    place_order,
    price_cart,
    transition_order,
)
from tests import factories


@pytest.fixture
def catalog(company):
    return {
        "carton": factories.make_product(company, sku="CTN", price_paise=4_250, gst_rate_bp=1800),
        "rice": factories.make_product(company, sku="RICE", price_paise=1_45_000, gst_rate_bp=500),
        "hidden": factories.make_product(company, sku="OLD", price_paise=999, is_active=False),
    }


def place(buyer, cart, **kwargs):
    return place_order(buyer, cart, **kwargs)


def test_price_cart_applies_gst_per_line_and_drops_unavailable_products(company, catalog, other_company):
    cart = {catalog["carton"].id: 10, catalog["rice"].id: 2, catalog["hidden"].id: 5, other_company["product"].id: 1}
    priced = price_cart(company.id, cart)
    assert {line.product.sku for line in priced.lines} == {"CTN", "RICE"}
    assert priced.subtotal_paise == 42_500 + 2_90_000
    assert priced.tax_paise == 7_650 + 14_500
    assert priced.total_paise == priced.subtotal_paise + priced.tax_paise
    assert priced.removed == [catalog["hidden"].name]  # other tenant's product is silently ignored


@pytest.mark.parametrize("value, expected", [("3", 3), ("", 0), (" 12 ", 12), ("0", 0)])
def test_parse_quantity(value, expected):
    assert parse_quantity(value) == expected


@pytest.mark.parametrize("value", ["-1", "2.5", "²", "٣", "1e3", "abc", "100001", "9" * 40])
def test_parse_quantity_rejects_bad_input(value):
    with pytest.raises(OrderError):
        parse_quantity(value)


def test_place_order_snapshots_lines_and_numbers_orders_per_company(app, company, buyer, catalog, other_company, mail):
    order, created = place(buyer, {catalog["carton"].id: 3}, notes="  PO#:4512  ")
    assert created
    assert order.number == "ORD-000001"
    assert order.status == PENDING
    assert order.notes == "PO#:4512"  # the colon text that crashed v1
    assert order.total_paise == 3 * 4_250 + 2_295
    item = order.items[0]
    assert (item.sku, item.name, item.unit_price_paise, item.gst_rate_bp) == (
        "CTN",
        catalog["carton"].name,
        4_250,
        1800,
    )
    assert [e.to_status for e in order.events] == [PENDING]

    second, _ = place(buyer, {catalog["rice"].id: 1})
    assert second.number == "ORD-000002"
    rival_order, _ = place(other_company["client"], {other_company["product"].id: 1})
    assert rival_order.number == "ORD-000001"  # numbers are per company

    # Editing the product later never changes the order
    catalog["carton"].name = "Renamed"
    catalog["carton"].price_paise = 1
    db.session.commit()
    db.session.refresh(item)
    assert item.name != "Renamed" and item.unit_price_paise == 4_250

    subjects = [m.subject for m in mail]
    assert any("We received your order ORD-000001" in s for s in subjects)


def test_same_submission_key_returns_the_existing_order(company, buyer, catalog):
    first, created = place(buyer, {catalog["carton"].id: 1}, submission_key="abc123")
    again, created_again = place(buyer, {catalog["carton"].id: 1}, submission_key="abc123")
    assert created and not created_again
    assert first.id == again.id
    assert db.session.scalar(db.select(db.func.count(Order.id))) == 1


def test_empty_or_unavailable_cart_cannot_be_ordered(company, buyer, catalog):
    with pytest.raises(OrderError):
        place(buyer, {})
    with pytest.raises(OrderError):
        place(buyer, {catalog["hidden"].id: 2})


def test_status_rules(app, company, owner, admin, buyer, catalog, other_company, mail):
    order, _ = place(buyer, {catalog["carton"].id: 1})
    assert set(allowed_transitions(order, owner)) == {APPROVED, REJECTED}
    assert allowed_transitions(order, buyer) == (CANCELLED,)
    assert allowed_transitions(order, other_company["owner"]) == ()

    with pytest.raises(OrderError):
        transition_order(order, owner, FULFILLED)  # must be approved first
    with pytest.raises(OrderError):
        transition_order(order, owner, REJECTED)  # needs a reason
    transition_order(order, admin, APPROVED)
    assert order.decided_by_id == admin.id and order.decided_at is not None
    with pytest.raises(OrderError):
        transition_order(order, buyer, CANCELLED)  # too late for the client
    with pytest.raises(OrderError):
        transition_order(order, owner, PENDING)
    transition_order(order, owner, FULFILLED)
    assert order.fulfilled_by_id == owner.id
    with pytest.raises(OrderError):
        transition_order(order, owner, APPROVED)  # fulfilled is final

    assert [e.to_status for e in order.events] == [PENDING, APPROVED, FULFILLED]
    assert any("approved" in m.subject for m in mail)


def test_rejection_records_reason_and_client_can_cancel_pending(app, company, owner, buyer, catalog):
    rejected, _ = place(buyer, {catalog["carton"].id: 1})
    cancelled, _ = place(buyer, {catalog["rice"].id: 1})
    transition_order(rejected, owner, REJECTED, "Out of stock until Monday")
    transition_order(cancelled, buyer, CANCELLED)
    assert rejected.rejection_reason == "Out of stock until Monday"
    assert cancelled.status == CANCELLED and cancelled.cancelled_at is not None
