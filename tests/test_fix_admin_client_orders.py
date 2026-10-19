"""A client's page links to all of their orders, and the order list can show one client's orders."""

import csv
import io
import re

import pytest

from orderflow.admin import orders as orders_view
from orderflow.extensions import db
from orderflow.models import APPROVED, PENDING, REJECTED
from tests import factories
from tests.test_admin_orders_support import ist, make_order

ROW_LINK = re.compile(r'<a class="text-link mono" href="/app/orders/([A-Z]+-\d+)">')


@pytest.fixture
def signed_in(login, owner):
    login(owner)
    return owner


@pytest.fixture
def product(company):
    return factories.make_product(company, sku="WID", name="Widget")


@pytest.fixture
def ana_and_hana(company, product, owner):
    """Two clients whose emails overlap, so an email search would mix them up."""
    ana = factories.make_client(company, email="ana@x.example", first_name="Ana", buyer_company="Ana Stores")
    hana = factories.make_client(company, email="hana@x.example", first_name="Hana", buyer_company="Hana Mart")
    ana_orders = [
        make_order(company, ana, [(product, 1)], status, ist(2026, 10, day, 10, 0), owner)
        for day, status in enumerate([PENDING] * 11 + [APPROVED], start=1)
    ]
    hana_orders = [make_order(company, hana, [(product, 1)], PENDING, ist(2026, 10, 20 + n, 10, 0)) for n in range(2)]
    return {"ana": ana, "hana": hana, "ana_orders": ana_orders, "hana_orders": hana_orders}


def get(browser, path, **query):
    response = browser.get(path, query_string=query or None)
    assert response.status_code == 200, response.headers.get("Location")
    return response.get_data(as_text=True)


def listed(html):
    return ROW_LINK.findall(html)


def test_client_page_links_to_all_of_their_orders(client, signed_in, ana_and_hana):
    ana = ana_and_hana["ana"]
    html = get(client, f"/app/clients/{ana.id}")
    assert "Showing the latest 10 of 12 orders." in html
    assert f'<a class="text-link" href="/app/orders?client={ana.id}">View all 12 orders</a>' in html


def test_no_link_when_every_order_is_already_shown(client, signed_in, ana_and_hana):
    html = get(client, f"/app/clients/{ana_and_hana['hana'].id}")
    assert "?client=" not in html and "Showing the latest" not in html


def test_the_client_filter_lists_only_that_clients_orders(client, signed_in, ana_and_hana):
    ana = ana_and_hana["ana"]
    html = get(client, "/app/orders", client=ana.id)
    numbers = {order.number for order in ana_and_hana["ana_orders"]}
    assert set(listed(html)) == numbers  # hana@x.example's orders aren't mixed in
    assert "12 orders" in html
    assert re.search(r'All <span class="tab__count">12</span>', html)
    assert re.search(r'Pending <span class="tab__count">11</span>', html)
    assert "Showing orders from" in html and "Ana" in html and "(Ana Stores)" in html
    assert f'href="/app/clients/{ana.id}"' in html
    assert 'href="/app/orders">Show orders from every client</a>' in html
    # Tabs, the search form and the export keep the client
    assert f'href="/app/orders?status=approved&amp;client={ana.id}"' in html
    assert f'<input type="hidden" name="client" value="{ana.id}">' in html
    assert f'href="/app/orders/export.csv?client={ana.id}"' in html


def test_the_client_filter_combines_with_status_and_search(client, signed_in, ana_and_hana):
    ana = ana_and_hana["ana"]
    approved = ana_and_hana["ana_orders"][-1]
    assert listed(get(client, "/app/orders", client=ana.id, status=APPROVED)) == [approved.number]
    assert listed(get(client, "/app/orders", client=ana.id, q=approved.number)) == [approved.number]
    hana_number = ana_and_hana["hana_orders"][0].number
    assert listed(get(client, "/app/orders", client=ana.id, q=hana_number)) == []


def test_clearing_the_search_keeps_the_client(client, signed_in, ana_and_hana):
    ana = ana_and_hana["ana"]
    html = get(client, "/app/orders", client=ana.id, q="nothing", status=PENDING)
    assert f'href="/app/orders?status=pending&amp;client={ana.id}">Clear search and dates</a>' in html


def test_an_empty_tab_for_one_client(client, signed_in, ana_and_hana):
    html = get(client, "/app/orders", client=ana_and_hana["ana"].id, status=REJECTED)
    assert "No rejected orders from Ana" in html
    assert "has placed 12 orders, but none of them are rejected." in html
    assert "Check the spelling" not in html


def test_a_client_without_orders(client, signed_in, ana_and_hana, company):
    newbie = factories.make_client(company, email="new@shop.example", first_name="Neel", buyer_company="Neel Co")
    html = get(client, "/app/orders", client=newbie.id)
    assert "Neel Test (Neel Co) hasn&#39;t placed any orders yet" in html
    assert "No all orders" not in html and "None" not in re.sub(r"<[^>]+>", " ", html)
    assert listed(html) == []


def test_export_with_the_client_filter_has_only_their_orders(client, signed_in, ana_and_hana):
    ana = ana_and_hana["ana"]
    response = client.get("/app/orders/export.csv", query_string={"client": ana.id})
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True).lstrip("﻿"))))
    assert {row[5] for row in rows[1:]} == {"ana@x.example"}
    assert len(rows) == 13


@pytest.mark.parametrize("value", ["abc", "-1", "1.5", "１", "9" * 10, ""])
def test_unreadable_client_values_are_ignored(client, signed_in, ana_and_hana, value):
    html = get(client, "/app/orders", client=value)
    assert len(listed(html)) == 14 and "Showing orders from" not in html


def test_another_companys_client_shows_nothing_of_theirs(client, signed_in, ana_and_hana, other_company):
    rival = other_company
    make_order(rival["company"], rival["client"], [(rival["product"], 1)], PENDING, ist(2026, 10, 9, 9, 0))
    html = get(client, "/app/orders", client=rival["client"].id)
    assert "We couldn&#39;t find that client, so this shows orders from every client." in html
    assert "Rival" not in html and len(listed(html)) == 14
    response = client.get("/app/orders/export.csv", query_string={"client": rival["client"].id})
    assert "buyer@rival.example" not in response.get_data(as_text=True)


def test_a_staff_id_is_not_a_client(client, signed_in, ana_and_hana, owner):
    html = get(client, "/app/orders", client=owner.id)
    assert "We couldn&#39;t find that client" in html and len(listed(html)) == 14


def test_parse_order_filters_reads_the_client():
    filters = orders_view.parse_order_filters({"client": " 42 ", "status": "pending"})
    assert filters.client_id == 42 and filters.narrowed and not filters.searching
    assert filters.args() == {"status": "pending", "client": 42}
    assert filters.args(client=None) == {"status": "pending"}
    assert orders_view.parse_order_filters({"client": "4" * 10}).client_id is None


def test_order_page_links_back_to_the_client(client, signed_in, ana_and_hana):
    order = ana_and_hana["ana_orders"][0]
    html = get(client, f"/app/orders/{order.number}")
    assert f'<a class="text-link" href="/app/clients/{ana_and_hana["ana"].id}">Ana Test</a>' in html


def test_list_without_a_client_filter_runs_no_extra_query(client, signed_in, ana_and_hana):
    from sqlalchemy import event

    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        get(client, "/app/orders")
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    assert len(statements) <= 7
