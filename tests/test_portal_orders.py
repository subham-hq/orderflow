import re
from datetime import timedelta

import pytest

from orderflow.extensions import db
from orderflow.models import APPROVED, CANCELLED, FULFILLED, PENDING, REJECTED, Order, OrderEvent
from orderflow.services.orders import place_order
from orderflow.timeutil import utcnow
from tests import factories

# Helpers (also used by the other portal tests)


def make_order(buyer, items, status=PENDING, placed_at=None, notes=None, reason=None):
    """Place an order for buyer ([(product, quantity), ...]) and move it straight to status."""
    order, _ = place_order(buyer, {str(product.id): quantity for product, quantity in items}, notes=notes)
    if placed_at is not None:
        order.placed_at = placed_at
        order.events[0].created_at = placed_at
    now = utcnow()
    if status in (APPROVED, FULFILLED, REJECTED):
        order.decided_at = now
        decided = REJECTED if status == REJECTED else APPROVED
        order.events.append(OrderEvent(from_status=PENDING, to_status=decided, message=reason, created_at=now))
        if status == REJECTED:
            order.rejection_reason = reason
    if status == FULFILLED:
        order.fulfilled_at = now
        order.events.append(OrderEvent(from_status=APPROVED, to_status=FULFILLED, created_at=now))
    if status == CANCELLED:
        order.cancelled_at = now
        order.events.append(OrderEvent(actor_id=buyer.id, from_status=PENDING, to_status=CANCELLED, created_at=now))
    order.status = status
    db.session.commit()
    return order


def get_cart(client):
    with client.session_transaction() as sess:
        return dict(sess.get("cart") or {})


def set_cart(client, cart):
    with client.session_transaction() as sess:
        sess["cart"] = {str(key): value for key, value in cart.items()}


def flashes(client):
    with client.session_transaction() as sess:
        return [message for _category, message in sess.get("_flashes", [])]


def order_numbers(page):
    """Order numbers in the orders table, in the order shown."""
    return re.findall(r'class="table__primary mono text-link" href="/portal/orders/[^"]+">([^<]+)</a>', page)


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


# Access


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/portal/orders"),
        ("get", "/portal/orders/ORD-000001"),
        ("post", "/portal/orders/ORD-000001/cancel"),
        ("post", "/portal/orders/ORD-000001/reorder"),
        ("get", "/portal/orders/ORD-000001/invoice"),
    ],
)
def test_order_pages_are_only_for_signed_in_clients(client, login, owner, admin, buyer, catalog, method, path):
    make_order(buyer, [(catalog["carton"], 1)])
    response = getattr(client, method)(path)
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    for staff in (owner, admin):
        login(staff)
        assert getattr(client, method)(path).headers["Location"].endswith("/app/")
        client.post("/logout")
    assert db.session.scalar(db.select(Order.status)) == PENDING


# The list


def test_a_new_client_is_told_how_to_place_a_first_order(shopper):
    page = shopper.get("/portal/orders").get_data(as_text=True)
    assert "You haven&#39;t placed any orders yet" in page
    assert 'href="/portal/catalog"' in page
    assert 'class="tabs"' not in page
    assert "None" not in page


def test_the_list_shows_only_the_clients_own_orders(shopper, buyer, neighbour, catalog, other_company):
    mine = make_order(buyer, [(catalog["carton"], 2), (catalog["rice"], 1)])
    make_order(neighbour, [(catalog["carton"], 1)])
    make_order(other_company["client"], [(other_company["product"], 1)])

    page = shopper.get("/portal/orders").get_data(as_text=True)
    assert order_numbers(page) == [mine.number]
    assert "₹1,622.80" in page  # 2 × ₹42.50 + 18% GST, plus ₹1,450 + 5% GST
    assert '<td class="num">2</td>' in page  # two items on the order
    assert "Pending review" in page
    assert "None" not in page


@pytest.fixture
def mixed_orders(buyer, catalog):
    carton = catalog["carton"]
    start = utcnow() - timedelta(days=10)
    statuses = [PENDING, APPROVED, FULFILLED, REJECTED, CANCELLED, PENDING]
    return [
        make_order(buyer, [(carton, 1)], status=status, placed_at=start + timedelta(days=index), reason="Out of stock")
        for index, status in enumerate(statuses)
    ]


def tab_count(page, label):
    match = re.search(rf"<span>{label}</span> <span class=\"tab__count\">(\d+)</span>", page)
    assert match, label
    return int(match.group(1))


def test_tabs_show_counts_and_filter_by_status(shopper, mixed_orders):
    page = shopper.get("/portal/orders").get_data(as_text=True)
    counts = {label: tab_count(page, label) for label in ("All", "Pending", "Approved", "Fulfilled", "Closed")}
    assert counts == {"All": 6, "Pending": 2, "Approved": 1, "Fulfilled": 1, "Closed": 2}
    # Newest first
    assert order_numbers(page) == [order.number for order in reversed(mixed_orders)]
    assert 'href="/portal/orders" aria-current="page"' in page

    closed = shopper.get("/portal/orders?status=closed").get_data(as_text=True)
    assert order_numbers(closed) == [mixed_orders[4].number, mixed_orders[3].number]
    assert 'href="/portal/orders?status=closed" aria-current="page"' in closed

    pending = shopper.get("/portal/orders?status=pending").get_data(as_text=True)
    assert order_numbers(pending) == [mixed_orders[5].number, mixed_orders[0].number]
    assert order_numbers(shopper.get("/portal/orders?status=approved").get_data(as_text=True)) == [
        mixed_orders[1].number
    ]
    assert order_numbers(shopper.get("/portal/orders?status=fulfilled").get_data(as_text=True)) == [
        mixed_orders[2].number
    ]


@pytest.mark.parametrize("status", ["bogus", "", "PENDING", "rejected", "%00x"])
def test_an_unknown_status_shows_all_orders(shopper, mixed_orders, status):
    response = shopper.get(f"/portal/orders?status={status}")
    if status == "%00x":
        assert response.status_code == 400  # NUL bytes are refused before any page runs
        return
    assert len(order_numbers(response.get_data(as_text=True))) == 6


def test_an_empty_tab_says_what_belongs_there(shopper, buyer, catalog):
    make_order(buyer, [(catalog["carton"], 1)], status=FULFILLED)
    page = shopper.get("/portal/orders?status=pending").get_data(as_text=True)
    assert "No orders waiting for review" in page
    assert "See all orders" in page


def test_search_finds_orders_by_number(shopper, mixed_orders):
    target = mixed_orders[1].number  # ORD-000002
    for q in (target, target.lower(), "000002", f"#{target}", f"  {target}  "):
        page = shopper.get("/portal/orders", query_string={"q": q}).get_data(as_text=True)
        assert order_numbers(page) == [target], q
    page = shopper.get("/portal/orders", query_string={"q": "000002"}).get_data(as_text=True)
    assert tab_count(page, "All") == 1 and tab_count(page, "Approved") == 1 and tab_count(page, "Pending") == 0
    assert 'href="/portal/orders?status=approved&amp;q=000002"' in page  # tabs keep the search


def test_search_within_a_tab(shopper, mixed_orders):
    page = shopper.get("/portal/orders", query_string={"q": "00000", "status": "closed"}).get_data(as_text=True)
    assert order_numbers(page) == [mixed_orders[4].number, mixed_orders[3].number]
    assert 'name="status" value="closed"' in page  # the search box keeps the tab


@pytest.mark.parametrize("q", ["ORD-999999", "%", "_", "\\", "'; DROP TABLE orders; --", "x" * 5000, "٣"])
def test_search_with_no_match_says_so(shopper, mixed_orders, q):
    response = shopper.get("/portal/orders", query_string={"q": q})
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert order_numbers(page) == []
    assert "No orders match" in page
    assert "Clear search" in page


def test_search_does_not_reach_other_clients_orders(shopper, buyer, neighbour, catalog):
    theirs = make_order(neighbour, [(catalog["carton"], 1)])
    page = shopper.get("/portal/orders", query_string={"q": theirs.number}).get_data(as_text=True)
    assert order_numbers(page) == []


def test_pagination_shows_twenty_per_page_newest_first(shopper, buyer, catalog):
    start = utcnow() - timedelta(days=30)
    orders = [make_order(buyer, [(catalog["carton"], 1)], placed_at=start + timedelta(hours=n)) for n in range(25)]
    newest_first = [order.number for order in reversed(orders)]

    first = shopper.get("/portal/orders").get_data(as_text=True)
    assert order_numbers(first) == newest_first[:20]
    assert "Page 1 of 2" in first
    assert 'href="/portal/orders?page=2"' in first
    assert "25 orders · showing 1–20" in first

    second = shopper.get("/portal/orders?page=2").get_data(as_text=True)
    assert order_numbers(second) == newest_first[20:]

    # Out-of-range and malformed page numbers never break the page
    assert order_numbers(shopper.get("/portal/orders?page=999").get_data(as_text=True)) == newest_first[20:]
    for bad in ("abc", "-3", "0", "²", "9" * 5000):
        assert order_numbers(shopper.get(f"/portal/orders?page={bad}").get_data(as_text=True)) == newest_first[:20]

    # Page links keep the status tab and search
    pending = shopper.get("/portal/orders?status=pending&q=ORD").get_data(as_text=True)
    assert 'href="/portal/orders?status=pending&amp;q=ORD&amp;page=2"' in pending


def test_search_text_is_escaped_when_shown_back(shopper, buyer, catalog):
    make_order(buyer, [(catalog["carton"], 1)])
    page = shopper.get("/portal/orders", query_string={"q": "<b>x</b>"}).get_data(as_text=True)
    assert "<b>x</b>" not in page
    assert "&lt;b&gt;x&lt;/b&gt;" in page
