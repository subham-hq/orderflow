import csv
import io
from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from orderflow.admin import orders as orders_view
from orderflow.extensions import db
from orderflow.models import APPROVED, CANCELLED, FULFILLED, PENDING, REJECTED
from tests import factories
from tests.test_admin_orders_support import ist, make_order

HEADER = [
    "Order number",
    "Placed",
    "Status",
    "Client",
    "Buyer company",
    "Client email",
    "Items",
    "Subtotal (₹)",
    "GST (₹)",
    "Total (₹)",
    "Decided at",
    "Fulfilled at",
]
BOM = b"\xef\xbb\xbf"


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    # 20:00 UTC on 31 October is already 1 November in India
    monkeypatch.setattr(orders_view, "utcnow", lambda: datetime(2026, 10, 31, 20, 0, tzinfo=UTC))


@pytest.fixture
def widget(company):
    # ₹1,000 + 18% GST
    return factories.make_product(company, sku="WID", name="Widget", price_paise=1_000_00, gst_rate_bp=1800)


@pytest.fixture
def gadget(company):
    return factories.make_product(company, sku="GAD", name="Gadget", price_paise=12_345_678_99, gst_rate_bp=500)


def download(client, query=""):
    response = client.get("/app/orders/export.csv" + query)
    assert response.status_code == 200, response.headers.get("Location")
    return response


def rows(response):
    data = response.get_data()
    assert data.startswith(BOM)
    return list(csv.reader(io.StringIO(data[len(BOM) :].decode("utf-8"), newline="")))


def numbers(response):
    return [row[0] for row in rows(response)[1:]]


# The file ------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["owner", "admin"])
def test_export_is_a_utf8_csv_download_named_for_the_local_date(client, login, request, company, buyer, widget, who):
    make_order(company, buyer, [(widget, 1)])
    login(request.getfixturevalue(who))
    response = download(client)
    assert response.mimetype == "text/csv"
    assert response.headers["Content-Type"] == "text/csv; charset=utf-8"
    assert response.headers["Content-Disposition"] == 'attachment; filename="orders-2026-11-01.csv"'
    assert response.is_streamed
    assert response.get_data().startswith(BOM)
    assert rows(response)[0] == HEADER
    assert response.headers["Cache-Control"] == "no-store"


def test_rows_hold_every_column_in_plain_formats(client, login, owner, admin, company, buyer, widget, gadget):
    pending = make_order(company, buyer, [(widget, 2)], PENDING, ist(2026, 10, 31, 23, 30))
    fulfilled = make_order(company, buyer, [(widget, 1), (gadget, 1)], FULFILLED, ist(2026, 10, 2, 9, 5), admin)
    login(owner)
    table = rows(download(client))
    assert table[1] == [
        pending.number,
        "2026-10-31T23:30:00+05:30",  # India time, not 18:00 UTC
        "Pending review",
        buyer.full_name,
        "Buyer Pvt Ltd",
        "buyer@sunrise.example",
        "1",
        "2000.00",
        "360.00",
        "2360.00",
        "",
        "",
    ]
    # ₹1,000 + 18% and ₹1,23,45,678.99 + 5%, with no thousands separators or symbol
    assert table[2] == [
        fulfilled.number,
        "2026-10-02T09:05:00+05:30",
        "Fulfilled",
        buyer.full_name,
        "Buyer Pvt Ltd",
        "buyer@sunrise.example",
        "2",
        "12346678.99",
        "617463.95",
        "12964142.94",
        "2026-10-02T10:05:00+05:30",
        "2026-10-02T11:05:00+05:30",
    ]
    assert len(table) == 3


def test_times_follow_the_company_time_zone(client, login, owner, company, buyer, widget):
    company.timezone = "UTC"
    db.session.commit()
    make_order(company, buyer, [(widget, 1)], APPROVED, ist(2026, 10, 31, 23, 30), owner)
    login(owner)
    response = download(client)
    assert response.headers["Content-Disposition"] == 'attachment; filename="orders-2026-10-31.csv"'
    row = rows(response)[1]
    assert row[1] == "2026-10-31T18:00:00+00:00"
    assert row[10] == "2026-10-31T19:00:00+00:00"


def test_an_empty_export_has_just_the_header(client, login, owner):
    login(owner)
    assert rows(download(client)) == [HEADER]


def test_export_never_includes_another_companys_orders(client, login, owner, company, buyer, widget, other_company):
    ours = make_order(company, buyer, [(widget, 1)])
    rival = other_company
    make_order(rival["company"], rival["client"], [(rival["product"], 1)])
    make_order(rival["company"], rival["client"], [(rival["product"], 1)])
    login(owner)
    response = download(client)
    assert numbers(response) == [ours.number]
    assert b"rival" not in response.get_data()


# Filters --------------------------------------------------------------------------------


@pytest.fixture
def mixed(company, owner, buyer, widget):
    hotel = factories.make_client(company, email="joseph@coastline.example", buyer_company="Coastline Hotels")
    return {
        PENDING: make_order(company, buyer, [(widget, 1)], PENDING, ist(2026, 10, 1, 10, 0)),
        APPROVED: make_order(company, hotel, [(widget, 1)], APPROVED, ist(2026, 10, 15, 10, 0), owner),
        FULFILLED: make_order(company, buyer, [(widget, 1)], FULFILLED, ist(2026, 10, 31, 23, 30), owner),
        REJECTED: make_order(company, hotel, [(widget, 1)], REJECTED, ist(2026, 11, 1, 0, 30), owner),
        CANCELLED: make_order(company, buyer, [(widget, 1)], CANCELLED, ist(2026, 9, 30, 10, 0)),
    }


@pytest.mark.parametrize(
    "query, expected",
    [
        ("", [REJECTED, FULFILLED, APPROVED, PENDING, CANCELLED]),
        ("?status=approved", [APPROVED]),
        ("?status=bogus", [REJECTED, FULFILLED, APPROVED, PENDING, CANCELLED]),
        ("?q=coastline", [REJECTED, APPROVED]),
        ("?q=%23ORD-000003", [FULFILLED]),
        ("?q=nobody", []),
        ("?from=2026-10-01&to=2026-10-31", [FULFILLED, APPROVED, PENDING]),
        ("?from=2026-11-01", [REJECTED]),
        ("?to=2026-09-30", [CANCELLED]),
        ("?from=2026-10-31&to=2026-10-01", [FULFILLED, APPROVED, PENDING]),  # swapped
        ("?from=not-a-date&to=2026-02-30", [REJECTED, FULFILLED, APPROVED, PENDING, CANCELLED]),
        ("?status=rejected&q=coast&from=2026-10-01", [REJECTED]),
        ("?page=2", [REJECTED, FULFILLED, APPROVED, PENDING, CANCELLED]),  # the export is never paged
    ],
)
def test_export_uses_the_same_filters_as_the_list(client, login, owner, mixed, query, expected):
    login(owner)
    assert numbers(download(client, query)) == [mixed[status].number for status in expected]


def test_list_links_to_an_export_with_its_filters(client, login, owner, mixed):
    login(owner)
    page = client.get("/app/orders?status=approved&q=coast&from=2026-10-01&to=bad&page=1").get_data(as_text=True)
    assert 'href="/app/orders/export.csv?status=approved&amp;q=coast&amp;from=2026-10-01"' in page
    assert "Export CSV" in page
    assert client.get("/app/orders/export.csv?status=approved&q=coast&from=2026-10-01").status_code == 200


def test_no_export_link_before_the_first_order(client, login, owner):
    login(owner)
    assert "export.csv" not in client.get("/app/orders").get_data(as_text=True)


# Safety ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ['=HYPERLINK("http://evil.example","Click")', "+91 cmd", "-2+3", "@SUM(A1:A9)", "\tTabbed", "\rReturn"],
)
def test_cells_that_look_like_formulas_are_made_inert(client, login, owner, company, widget, value):
    sneaky = factories.make_client(company, email="sneaky@example.com", first_name=value, last_name=None)
    sneaky.buyer_company = value
    db.session.commit()
    make_order(company, sneaky, [(widget, 1)])
    login(owner)
    row = rows(download(client))[1]
    assert row[3] == "'" + value
    assert row[4] == "'" + value


def test_commas_quotes_and_line_breaks_stay_inside_their_cells(client, login, owner, company, widget):
    tricky = factories.make_client(company, email="tricky@example.com", first_name="Ravi", last_name='"Sharma"')
    tricky.buyer_company = "Sharma, Sons & Co\nUnit 2 — ₹ wholesale"
    db.session.commit()
    make_order(company, tricky, [(widget, 1)])
    login(owner)
    table = rows(download(client))
    assert len(table) == 2
    assert table[1][3] == 'Ravi "Sharma"'
    assert table[1][4] == "Sharma, Sons & Co\nUnit 2 — ₹ wholesale"
    assert len(table[1]) == len(HEADER)


def test_export_streams_in_chunks_with_one_query(client, login, owner, company, buyer, widget, monkeypatch):
    for day in range(1, 21):
        make_order(company, buyer, [(widget, 1)], PENDING, ist(2026, 10, day, 9, 0))
    monkeypatch.setattr(orders_view, "EXPORT_FLUSH_BYTES", 500)
    monkeypatch.setattr(orders_view, "EXPORT_BATCH_ROWS", 7)
    login(owner)

    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        response = client.get("/app/orders/export.csv")
        chunks = list(response.response)
    finally:
        event.remove(db.engine, "before_cursor_execute", record)
    assert len(chunks) > 3
    text = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)
    table = list(csv.reader(io.StringIO(text.removeprefix("﻿"), newline="")))
    assert len(table) == 21
    assert [row[1][:10] for row in table[1:]] == [f"2026-10-{day:02d}" for day in range(20, 0, -1)]
    # user, company and the export itself; no query per order
    assert len([s for s in statements if "order_items" in s]) == 1
    assert len(statements) <= 4


# Access ------------------------------------------------------------------------------------


def test_clients_and_visitors_cannot_export(client, login, company, buyer, widget):
    make_order(company, buyer, [(widget, 1)])
    response = client.get("/app/orders/export.csv")
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    login(buyer)
    response = client.get("/app/orders/export.csv")
    assert response.status_code == 302 and response.headers["Location"] == "/portal/"
    assert b"Order number" not in response.get_data()


# Helpers ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, ""),
        ("", ""),
        ("Plain", "Plain"),
        ("=1+1", "'=1+1"),
        ("+1", "'+1"),
        ("-1", "'-1"),
        ("@x", "'@x"),
        ("\tx", "'\tx"),
        ("\rx", "'\rx"),
        (" =1", " =1"),
        (3, "3"),
    ],
)
def test_csv_cell(value, expected):
    assert orders_view.csv_cell(value) == expected


@pytest.mark.parametrize(
    "paise, expected",
    [(0, "0.00"), (None, "0.00"), (5, "0.05"), (100, "1.00"), (123456789, "1234567.89"), (-250, "-2.50")],
)
def test_rupees(paise, expected):
    assert orders_view.rupees(paise) == expected


def test_local_iso_of_nothing_is_blank():
    assert orders_view.local_iso(None, "Asia/Kolkata") == ""
