import re
from datetime import date, timedelta

import pytest
from sqlalchemy import event

from orderflow.admin import orders as orders_view
from orderflow.extensions import db
from orderflow.models import APPROVED, CANCELLED, FULFILLED, PENDING, REJECTED
from tests import factories
from tests.test_admin_orders_support import ist, make_order

ROW_LINK = re.compile(r'<a class="text-link mono" href="/app/orders/([A-Z]+-\d+)">')


@pytest.fixture
def widget(company):
    return factories.make_product(company, sku="WID", name="Widget", price_paise=1_000_00, gst_rate_bp=1800)


@pytest.fixture
def shop(company, owner, buyer, widget):
    """Orders from two clients, one in each status, placed on different days in October."""
    hotel = factories.make_client(
        company,
        email="joseph@coastline.example",
        first_name="Joseph",
        last_name="Mathew",
        buyer_company="Coastline Hotels",
    )
    orders = {
        PENDING: make_order(company, buyer, [(widget, 1)], PENDING, ist(2026, 10, 1, 10, 0)),
        APPROVED: make_order(company, hotel, [(widget, 2)], APPROVED, ist(2026, 10, 2, 10, 0), owner),
        FULFILLED: make_order(company, buyer, [(widget, 3)], FULFILLED, ist(2026, 10, 3, 10, 0), owner),
        REJECTED: make_order(company, hotel, [(widget, 4)], REJECTED, ist(2026, 10, 4, 10, 0), owner),
        CANCELLED: make_order(company, buyer, [(widget, 5)], CANCELLED, ist(2026, 10, 5, 10, 0)),
    }
    return {"hotel": hotel, "orders": orders}


def get(client, path, **query):
    response = client.get(path, query_string=query or None)
    assert response.status_code == 200, response.headers.get("Location")
    return response.get_data(as_text=True)


def listed(html):
    """Order numbers in the table, top to bottom."""
    return ROW_LINK.findall(html)


def tab_counts(html):
    return dict(re.findall(r'class="tab[^"]*"[^>]*>(\w+) <span class="tab__count">([\d,]+)</span>', html))


def assert_clean(html):
    assert 'style="' not in html and "<style" not in html
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert not re.search(r"\son[a-z]+=", html)
    assert "None" not in re.sub(r"<[^>]+>", " ", html)


# The list ----------------------------------------------------------------------


@pytest.mark.parametrize("who", ["owner", "admin"])
def test_lists_every_order_newest_first(client, login, shop, request, who):
    login(request.getfixturevalue(who))
    html = get(client, "/app/orders")
    orders = shop["orders"]
    assert listed(html) == [orders[s].number for s in (CANCELLED, REJECTED, FULFILLED, APPROVED, PENDING)]
    assert tab_counts(html) == {
        "All": "5",
        "Pending": "1",
        "Approved": "1",
        "Fulfilled": "1",
        "Rejected": "1",
        "Cancelled": "1",
    }
    assert '<a class="tab is-active" href="/app/orders" aria-current="page">All' in html
    assert html.count('aria-current="page"') == 2  # the tab and the sidebar link
    assert "5 orders" in html
    assert_clean(html)


def test_rows_show_client_date_items_total_and_status(client, login, owner, company, buyer, widget):
    gadget = factories.make_product(company, sku="GAD", price_paise=500_00, gst_rate_bp=0)
    order = make_order(company, buyer, [(widget, 2), (gadget, 1)], APPROVED, ist(2026, 10, 31, 23, 30), owner)
    login(owner)
    html = get(client, "/app/orders")
    row = html.split("<tbody>")[1].split("</tr>")[0]
    assert f'href="/app/orders/{order.number}">{order.number}</a>' in row
    assert '<span class="table__primary">Buyer Pvt Ltd</span>' in row
    assert f'<span class="table__secondary">{buyer.full_name}</span>' in row
    # 23:30 on 31 October in India, though it is 18:00 UTC
    assert '<time datetime="2026-10-31T23:30+0530">31 Oct 2026</time>' in row
    assert '<td class="num">2</td>' in row  # two lines
    assert "₹2,860.00" in row  # 2 x ₹1,180 + ₹500
    assert "badge--approved" in row and "Approved" in row


def test_client_without_a_buyer_company_shows_their_name_once(client, login, owner, company, widget):
    person = factories.make_client(company, email="solo@example.com", first_name="Ravi", last_name=None)
    person.buyer_company = None
    db.session.commit()
    make_order(company, person, [(widget, 1)])
    login(owner)
    row = get(client, "/app/orders").split("<tbody>")[1].split("</tr>")[0]
    assert '<span class="table__primary">Ravi</span>' in row
    assert "table__secondary" not in row.split("</td>")[1]


@pytest.mark.parametrize("status", [PENDING, APPROVED, FULFILLED, REJECTED, CANCELLED])
def test_status_tabs_filter_the_list(client, login, owner, shop, status):
    login(owner)
    html = get(client, f"/app/orders?status={status}")
    assert listed(html) == [shop["orders"][status].number]
    assert f'href="/app/orders?status={status}" aria-current="page"' in html
    assert tab_counts(html)["All"] == "5"  # counts don't change with the tab


@pytest.mark.parametrize("status, expected", [("bogus", 5), ("PENDING ", 1), ("", 5), ("pending'--", 5)])
def test_unknown_status_shows_all_orders(client, login, owner, shop, status, expected):
    login(owner)
    assert len(listed(get(client, "/app/orders", status=status))) == expected


def test_empty_tab_explains_what_will_appear(client, login, owner, company, buyer, widget):
    make_order(company, buyer, [(widget, 1)], PENDING, ist(2026, 10, 1, 10, 0))
    login(owner)
    html = get(client, "/app/orders?status=fulfilled")
    assert listed(html) == []
    assert "No fulfilled orders" in html
    assert "Orders you&#39;ve delivered will be listed here." in html
    assert re.search(r"\b1 order\s*</p>", get(client, "/app/orders?status=pending"))


def test_new_company_sees_an_empty_state_with_a_next_step(client, login, owner):
    login(owner)
    html = get(client, "/app/orders")
    assert "No orders yet" in html
    assert 'href="/app/clients"' in html and "Invite a client" in html
    assert 'class="tabs"' not in html and "<table" not in html
    assert_clean(html)


def test_never_lists_another_companys_orders(client, login, owner, shop, other_company):
    rival = other_company
    rival_order = make_order(rival["company"], rival["client"], [(rival["product"], 1)])
    login(owner)
    html = get(client, "/app/orders")
    assert len(listed(html)) == 5
    assert tab_counts(html)["All"] == "5"
    assert listed(get(client, "/app/orders", q=rival["client"].email)) == []
    # Order numbers restart per company, so the rival's first order shares our first number
    assert rival_order.number == shop["orders"][PENDING].number
    html = get(client, "/app/orders", q=rival_order.number)
    assert listed(html) == [shop["orders"][PENDING].number]
    assert "Rival" not in html


# Search --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query, expected",
    [
        ("ORD-000002", [APPROVED]),
        ("ord-000002", [APPROVED]),
        ("#ORD-000002", [APPROVED]),
        ("  ord-000002  ", [APPROVED]),
        ("coastline", [REJECTED, APPROVED]),
        ("COASTLINE HOTELS", [REJECTED, APPROVED]),
        ("joseph mathew", [REJECTED, APPROVED]),
        ("Joseph   Mathew", [REJECTED, APPROVED]),
        ("mathew", [REJECTED, APPROVED]),
        ("buyer@sunrise", [CANCELLED, FULFILLED, PENDING]),
        ("SUNRISE.EXAMPLE", [CANCELLED, FULFILLED, PENDING]),
        ("ananya", [CANCELLED, FULFILLED, PENDING]),
        ("buyer pvt", [CANCELLED, FULFILLED, PENDING]),
    ],
)
def test_search_matches_number_client_company_and_email(client, login, owner, shop, query, expected):
    login(owner)
    html = get(client, "/app/orders", q=query)
    assert listed(html) == [shop["orders"][status].number for status in expected]


@pytest.mark.parametrize("query", ["%", "_", "%%", "ORD_000001", "\\", "/", "'", '"><script>', "ॐ", "x" * 500])
def test_search_treats_input_as_plain_text(client, login, owner, shop, query):
    login(owner)
    html = get(client, "/app/orders", q=query)
    assert listed(html) == []
    assert "No orders match your search" in html
    assert "<script>" not in html


def test_search_box_keeps_what_was_typed_and_is_capped(client, login, owner, shop):
    login(owner)
    assert 'value="Coast line"' in get(client, "/app/orders", q="Coast  line")
    assert f'value="{"a" * 100}"' in get(client, "/app/orders", q="a" * 150)


def test_tab_counts_follow_the_search(client, login, owner, shop):
    login(owner)
    html = get(client, "/app/orders?q=coastline&status=rejected")
    assert tab_counts(html) == {
        "All": "2",
        "Pending": "0",
        "Approved": "1",
        "Fulfilled": "0",
        "Rejected": "1",
        "Cancelled": "0",
    }
    assert listed(html) == [shop["orders"][REJECTED].number]
    # Tabs keep the search, and the form keeps the tab
    assert 'href="/app/orders?status=approved&amp;q=coastline"' in html
    assert '<input type="hidden" name="status" value="rejected">' in html


def test_no_results_offers_to_clear_the_search(client, login, owner, shop):
    login(owner)
    html = get(client, "/app/orders?q=nothing-like-this&status=pending")
    assert "No orders match your search" in html
    assert 'href="/app/orders?status=pending"' in html and "Clear search and dates" in html
    assert_clean(html)


# Date range ------------------------------------------------------------------------


@pytest.fixture
def month_end(company, owner, buyer, widget):
    """Orders either side of midnight on 31 October, India time."""
    return {
        "late": make_order(company, buyer, [(widget, 1)], PENDING, ist(2026, 10, 31, 23, 30)),  # 18:00 UTC
        "early": make_order(company, buyer, [(widget, 1)], PENDING, ist(2026, 11, 1, 0, 30)),  # 19:00 UTC, 31 Oct
        "before": make_order(company, buyer, [(widget, 1)], PENDING, ist(2026, 10, 30, 23, 59)),
    }


def test_date_range_is_inclusive_and_in_the_company_time_zone(client, login, owner, month_end):
    login(owner)
    late, early, before = month_end["late"].number, month_end["early"].number, month_end["before"].number
    assert listed(get(client, "/app/orders?from=2026-10-31&to=2026-10-31")) == [late]
    assert listed(get(client, "/app/orders?from=2026-11-01")) == [early]
    assert listed(get(client, "/app/orders?to=2026-10-31")) == [late, before]
    assert listed(get(client, "/app/orders?from=2026-10-30&to=2026-11-01")) == [early, late, before]
    html = get(client, "/app/orders?from=2026-10-31&to=2026-10-31")
    assert 'name="from" value="2026-10-31"' in html and 'name="to" value="2026-10-31"' in html
    assert "Clear search and dates" in html


def test_date_range_follows_a_different_company_time_zone(client, login, owner, company, month_end):
    company.timezone = "UTC"
    db.session.commit()
    login(owner)
    # In UTC both of the India-time orders either side of midnight fall on 31 October
    html = get(client, "/app/orders?from=2026-10-31&to=2026-10-31")
    assert listed(html) == [month_end["early"].number, month_end["late"].number]


@pytest.mark.parametrize(
    "value",
    [
        "2026-02-30",
        "2026-13-01",
        "abc",
        "31/10/2026",
        "2026-10-1",
        "٢٠٢٦-١٠-٠١",
        "２０２６-１０-０１",
        "0001-01-01",
        "99999-01-01",
        "9999-12-31",
        "2026-10-31T00:00",
        "-2026-10-01",
    ],
)
def test_bad_dates_are_ignored_with_a_message(client, login, owner, month_end, value):
    login(owner)
    html = get(client, "/app/orders", **{"from": value})
    assert len(listed(html)) == 3
    assert "We couldn&#39;t read the start date" in html
    html = get(client, "/app/orders", to=value)
    assert len(listed(html)) == 3
    assert "We couldn&#39;t read the end date" in html


def test_reversed_dates_are_swapped(client, login, owner, month_end):
    login(owner)
    html = get(client, "/app/orders?from=2026-11-01&to=2026-10-31")
    assert "The start date was after the end date, so we swapped them." in html
    assert listed(html) == [month_end["early"].number, month_end["late"].number]
    assert 'name="from" value="2026-10-31"' in html and 'name="to" value="2026-11-01"' in html


def test_parse_order_filters_directly():
    filters = orders_view.parse_order_filters({"status": "Approved", "q": "  a  b ", "from": "2026-10-01", "to": ""})
    assert (filters.status, filters.q, filters.date_from, filters.date_to) == (APPROVED, "a b", date(2026, 10, 1), None)
    assert filters.searching
    assert filters.args() == {"status": APPROVED, "q": "a b", "from": "2026-10-01"}
    assert filters.args(status=None, page=2) == {"q": "a b", "from": "2026-10-01", "page": 2}
    assert not orders_view.parse_order_filters({}).searching


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2", 2),
        (" 3 ", 3),
        ("0", 1),
        ("-3", 1),
        ("abc", 1),
        ("", 1),
        (None, 1),
        ("²", 1),
        ("١", 1),
        ("123456789", 10_000),
        ("10001", 10_000),
    ],
)
def test_parse_page(value, expected):
    assert orders_view.parse_page(value) == expected


# Pagination --------------------------------------------------------------------------


@pytest.fixture
def many(company, buyer, widget):
    start = ist(2026, 9, 1, 9, 0)
    return [make_order(company, buyer, [(widget, 1)], PENDING, start + timedelta(hours=n)) for n in range(30)]


def test_pages_of_25(client, login, owner, many):
    login(owner)
    newest_first = [order.number for order in reversed(many)]
    html = get(client, "/app/orders")
    assert listed(html) == newest_first[:25]
    assert "Showing 1–25 of 30 orders" in html
    assert "Page 1 of 2" in html
    assert 'href="/app/orders?page=2" rel="next"' in html

    html = get(client, "/app/orders?page=2")
    assert listed(html) == newest_first[25:]
    assert "Showing 26–30 of 30 orders" in html
    assert 'rel="prev"' in html


def test_page_links_keep_the_filters(client, login, owner, many):
    login(owner)
    html = get(client, "/app/orders?status=pending&q=buyer&from=2026-09-01")
    assert 'href="/app/orders?status=pending&amp;q=buyer&amp;from=2026-09-01&amp;page=2"' in html


@pytest.mark.parametrize("page, expected", [("999", 5), ("99999999999999", 5), ("-1", 25), ("0", 25), ("abc", 25)])
def test_out_of_range_pages_never_fail(client, login, owner, many, page, expected):
    login(owner)
    assert len(listed(get(client, "/app/orders", page=page))) == expected


def test_list_runs_a_fixed_number_of_queries(client, login, owner, many):
    login(owner)
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        get(client, "/app/orders")
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    # user, company, tab counts, page count, page rows, line counts
    assert len(statements) <= 6


# Access ------------------------------------------------------------------------------


def test_only_signed_in_staff_can_see_orders(client, login, buyer):
    response = client.get("/app/orders")
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    login(buyer)
    response = client.get("/app/orders")
    assert response.headers["Location"] == "/portal/"


def test_suspended_staff_lose_access_immediately(client, login, admin, shop):
    login(admin)
    assert client.get("/app/orders").status_code == 200
    admin.active = False
    db.session.commit()
    response = client.get("/app/orders")
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
