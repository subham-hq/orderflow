"""The home page's "Open orders" tile opens a list of exactly the orders it counts."""

import html
import re
from datetime import timedelta

import pytest

from orderflow.models import APPROVED, CANCELLED, FULFILLED, PENDING, REJECTED
from orderflow.timeutil import utcnow
from tests import factories
from tests.test_portal_orders import make_order, order_numbers, tab_count


@pytest.fixture
def shopper(client, login, buyer):
    login(buyer)
    return client


@pytest.fixture
def carton(company):
    return factories.make_product(company, sku="CTN-001", name="Corrugated carton", price_paise=4_250)


@pytest.fixture
def mixed(buyer, carton):
    start = utcnow() - timedelta(days=10)
    statuses = [PENDING, APPROVED, FULFILLED, REJECTED, CANCELLED, PENDING]
    return {
        index: make_order(buyer, [(carton, 1)], status=status, placed_at=start + timedelta(days=index), reason="No")
        for index, status in enumerate(statuses)
    }


def tile(page, label):
    match = re.search(
        rf'<a class="stat[^"]*" href="([^"]+)">\s*<span class="stat__label">{label}</span>\s*'
        r'<span class="stat__value">([^<]+)</span>',
        page,
    )
    assert match, label
    return html.unescape(match.group(1)), match.group(2)


def test_the_open_orders_tile_links_to_the_orders_it_counts(shopper, mixed):
    href, value = tile(shopper.get("/portal/").get_data(as_text=True), "Open orders")
    assert (href, value) == ("/portal/orders?status=open", "3")

    listed = shopper.get(href).get_data(as_text=True)
    assert order_numbers(listed) == [mixed[5].number, mixed[1].number, mixed[0].number]  # newest first
    assert len(order_numbers(listed)) == int(value)
    assert 'href="/portal/orders?status=open" aria-current="page"' in listed


def test_total_orders_still_opens_every_order(shopper, mixed):
    href, value = tile(shopper.get("/portal/").get_data(as_text=True), "Total orders")
    assert (href, value) == ("/portal/orders", "6")
    assert len(order_numbers(shopper.get(href).get_data(as_text=True))) == 6


def test_the_open_tab_counts_pending_and_approved_orders(shopper, mixed):
    page = shopper.get("/portal/orders").get_data(as_text=True)
    assert tab_count(page, "Open") == 3
    assert tab_count(page, "All") == 6
    labels = re.findall(r'<a class="tab[^"]*" href="[^"]*"[^>]*>\s*<span>([^<]+)</span>', page)
    assert labels == ["All", "Open", "Pending", "Approved", "Fulfilled", "Closed"]


def test_searching_within_the_open_tab_keeps_it(shopper, mixed):
    page = shopper.get("/portal/orders", query_string={"status": "open", "q": mixed[1].number}).get_data(as_text=True)
    assert order_numbers(page) == [mixed[1].number]
    assert 'name="status" value="open"' in page
    assert tab_count(page, "Open") == 1


def test_an_empty_open_tab_says_what_belongs_there(shopper, buyer, carton):
    make_order(buyer, [(carton, 1)], status=FULFILLED)
    page = shopper.get("/portal/orders?status=open").get_data(as_text=True)
    assert order_numbers(page) == []
    assert "No open orders" in page
    assert "See all orders" in page


def test_the_open_tab_never_shows_other_clients_orders(shopper, buyer, company, carton, other_company):
    neighbour = factories.make_client(company, email="neighbour@buyer.example")
    theirs = make_order(neighbour, [(carton, 1)])
    make_order(other_company["client"], [(other_company["product"], 1)], status=APPROVED)
    mine = make_order(buyer, [(carton, 1)], status=APPROVED)
    page = shopper.get("/portal/orders?status=open").get_data(as_text=True)
    assert order_numbers(page) == [mine.number]
    assert theirs.number != mine.number and tab_count(page, "Open") == 1
    assert tile(shopper.get("/portal/").get_data(as_text=True), "Open orders")[1] == "1"
