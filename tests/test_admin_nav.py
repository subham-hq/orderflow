"""The workspace sidebar shows how many orders are waiting for review."""

from contextlib import contextmanager

from flask import render_template_string
from flask_login import login_user
from sqlalchemy import event

from orderflow.extensions import db
from orderflow.services.orders import place_order
from tests import factories


def _count_badge(response):
    body = response.get_data(as_text=True)
    marker = 'class="nav-link__count">'
    if marker not in body:
        return None
    return body.split(marker, 1)[1].split("<", 1)[0]


@contextmanager
def _pending_order_counts():
    """Collect the SQL statements that count orders while the block runs."""
    seen = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if "count(orders.id)" in statement.lower():
            seen.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(db.engine, "before_cursor_execute", record)


def test_sidebar_counts_only_this_companys_pending_orders(client, login, company, owner, buyer, other_company):
    product = factories.make_product(company)
    login(owner)
    assert _count_badge(client.get("/app/")) is None  # nothing waiting

    place_order(buyer, {product.id: 1})
    place_order(buyer, {product.id: 2})
    place_order(other_company["client"], {other_company["product"].id: 1})

    assert _count_badge(client.get("/app/")) == "2"
    assert _count_badge(client.get("/app/products")) == "2"


def test_change_password_keeps_the_sidebar_count(client, login, company, owner, buyer):
    # The page belongs to the sign-in screens but uses the workspace layout for staff
    product = factories.make_product(company)
    place_order(buyer, {product.id: 1})
    login(owner)
    assert _count_badge(client.get("/app/settings/profile")) == "1"
    assert _count_badge(client.get("/account/password")) == "1"


def test_clients_never_get_staff_counts(client, login, company, buyer):
    product = factories.make_product(company)
    place_order(buyer, {product.id: 1})
    login(buyer)
    with _pending_order_counts() as seen:
        response = client.get("/account/password")
    assert response.status_code == 200 and _count_badge(response) is None
    assert seen == []


def test_orders_are_only_counted_for_pages_that_show_the_sidebar(app, owner):
    with app.test_request_context("/"):
        login_user(owner)
        with _pending_order_counts() as seen:
            render_template_string("<p>An email or page without the sidebar</p>")
        assert seen == []
        with _pending_order_counts() as seen:
            render_template_string('{{ nav_badges.get("orders") }}')
        assert len(seen) == 1
