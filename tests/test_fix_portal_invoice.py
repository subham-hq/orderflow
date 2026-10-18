"""Rejected and cancelled orders have no valid proforma invoice, in the portal as on the staff side.

The order page doesn't offer one, and the invoice page itself (shared by both
sides, so an old link or a typed URL still works) says plainly that it is not
valid, on screen and on paper.
"""

import pytest

from orderflow.models import APPROVED, CANCELLED, FULFILLED, PENDING, REJECTED
from tests import factories
from tests.design_helpers import Page
from tests.test_design_system import token_block
from tests.test_portal_orders import make_order


@pytest.fixture
def carton(company):
    return factories.make_product(company, sku="CTN-001", name="Corrugated carton", price_paise=4_250)


@pytest.fixture
def shopper(client, login, buyer):
    login(buyer)
    return client


def invoice_links(body, number):
    return [href for href in Page(body).hrefs() if href.endswith(f"/orders/{number}/invoice")]


@pytest.mark.parametrize("status", [REJECTED, CANCELLED])
def test_the_portal_order_page_offers_no_invoice_for_a_closed_order(shopper, buyer, carton, status):
    order = make_order(buyer, [(carton, 2)], status=status, reason="Out of stock")
    body = shopper.get(f"/portal/orders/{order.number}").get_data(as_text=True)
    assert invoice_links(body, order.number) == []
    assert "Proforma invoice" not in body
    assert f'action="/portal/orders/{order.number}/reorder"' in body  # the other actions stay


@pytest.mark.parametrize("status", [PENDING, APPROVED, FULFILLED])
def test_the_portal_order_page_offers_the_invoice_while_the_order_stands(shopper, buyer, carton, status):
    order = make_order(buyer, [(carton, 2)], status=status)
    body = shopper.get(f"/portal/orders/{order.number}").get_data(as_text=True)
    assert invoice_links(body, order.number) == [f"/portal/orders/{order.number}/invoice"]


@pytest.mark.parametrize(
    "status, reason",
    [
        (REJECTED, "Kaveri Supplies didn&#39;t accept order {number}, so this proforma invoice can&#39;t be used"),
        (CANCELLED, "Order {number} was cancelled, so this proforma invoice can&#39;t be used"),
    ],
)
def test_a_closed_orders_invoice_says_it_is_not_valid(shopper, buyer, carton, status, reason):
    order = make_order(buyer, [(carton, 2)], status=status, reason="Out of stock")
    response = shopper.get(f"/portal/orders/{order.number}/invoice")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert f"<title>Proforma invoice {order.number} (not valid)" in body
    assert f'<p class="alert__title">Not valid: this order was {status}</p>' in body
    assert reason.format(number=order.number) in body
    assert f"The order was {status}, so this proforma invoice is not valid." in body
    notice = Page(body).with_class("invoice__void")
    assert len(notice) == 1 and "no-print" not in notice[0]["class"]  # it prints too
    assert f'href="/portal/orders/{order.number}"' in body  # a way back


@pytest.mark.parametrize("status", [PENDING, APPROVED, FULFILLED])
def test_a_standing_orders_invoice_has_no_notice(shopper, buyer, carton, status):
    order = make_order(buyer, [(carton, 2)], status=status)
    body = shopper.get(f"/portal/orders/{order.number}/invoice").get_data(as_text=True)
    assert "Not valid" not in body and "(not valid)" not in body and "invoice__void" not in body
    assert "It is not a tax invoice.</p>" in body


def test_the_staff_invoice_page_says_the_same(client, login, owner, buyer, carton):
    order = make_order(buyer, [(carton, 2)], status=REJECTED, reason="Out of stock")
    login(owner)
    body = client.get(f"/app/orders/{order.number}/invoice").get_data(as_text=True)
    assert '<p class="alert__title">Not valid: this order was rejected</p>' in body
    assert f'href="/app/orders/{order.number}"' in body


def test_the_notice_prints_in_black_and_white():
    print_rules = token_block("@media print")
    rule = print_rules[print_rules.index(".invoice__void {") :]
    rule = rule[: rule.index("}")]
    assert "border: 2px solid #000000" in rule and "color: #000000" in rule
