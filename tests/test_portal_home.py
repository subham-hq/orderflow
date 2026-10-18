import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from orderflow.extensions import db
from orderflow.models import APPROVED, CANCELLED, FULFILLED, PENDING, REJECTED, Bulletin
from orderflow.portal.home import month_bounds
from orderflow.timeutil import local_input_value, parse_local_datetime, utcnow
from tests import factories
from tests.test_portal_orders import make_order, set_cart


@pytest.fixture
def shopper(client, login, buyer):
    login(buyer)
    return client


@pytest.fixture
def carton(company):
    return factories.make_product(
        company, sku="CTN-001", name="Corrugated carton", price_paise=10_000, gst_rate_bp=1800
    )


def home(client):
    response = client.get("/portal/")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def bulletin(company, title, body="Details", **fields):
    item = Bulletin(company=company, title=title, body=body, **fields)
    db.session.add(item)
    db.session.commit()
    return item


def stat_value(page, label):
    match = re.search(rf'<span class="stat__label">{label}</span>\s*<span class="stat__value">([^<]+)</span>', page)
    assert match, label
    return match.group(1)


def test_home_is_only_for_signed_in_clients(client, login, owner, admin):
    assert "/login" in client.get("/portal/").headers["Location"]
    for staff in (owner, admin):
        login(staff)
        assert client.get("/portal/").headers["Location"].endswith("/app/")
        client.post("/logout")


def test_a_new_client_is_welcomed_and_shown_how_to_start(shopper):
    page = home(shopper)
    assert "Welcome, Ananya" in page
    assert "You&#39;re ordering from Kaveri Supplies." in page
    assert "Buyer Pvt Ltd" in page
    assert "Place your first order" in page
    assert 'href="/portal/catalog"' in page
    assert 'class="stat' not in page
    assert "Recent orders" not in page
    assert "View cart" not in page
    assert "None" not in page


def test_a_client_without_a_business_name_still_sees_a_clean_page(client, login, company):
    solo = factories.make_client(company, email="solo@buyer.example", buyer_company=None, last_name=None)
    login(solo)
    page = home(client)
    assert "None" not in page
    assert 'class="page-header__eyebrow"' not in page


def test_quick_actions_offer_the_cart_when_it_has_items(shopper, carton):
    set_cart(shopper, {carton.id: 12})
    page = home(shopper)
    assert 'href="/portal/cart"' in page and "View cart (12)" in page
    assert "Review your cart" in page


# Bulletins


def test_only_published_unexpired_bulletins_show_newest_first(shopper, company, other_company):
    now = utcnow()
    bulletin(company, "Older notice", created_at=now - timedelta(days=3))
    bulletin(company, "Newest notice", created_at=now - timedelta(hours=1), expires_at=now + timedelta(days=2))
    bulletin(company, "No expiry", created_at=now - timedelta(days=1))
    bulletin(company, "Draft", is_published=False)
    bulletin(company, "Expired", expires_at=now - timedelta(minutes=1))
    bulletin(other_company["company"], "Rival announcement")

    page = home(shopper)
    titles = re.findall(r'<h3 class="card__title" id="bulletin-\d+-title">([^<]+)</h3>', page)
    assert titles == ["Newest notice", "No expiry", "Older notice"]
    assert "Announcements from Kaveri Supplies" in page


def test_a_single_bulletin_keeps_its_line_breaks(shopper, company):
    bulletin(company, "Diwali dispatch", body="Orders approved by 25 October ship first.\nWarehouse closed 31 Oct.")
    page = home(shopper)
    assert "Announcement from Kaveri Supplies" in page
    assert '<p class="text-pre-line">Orders approved by 25 October ship first.\nWarehouse closed 31 Oct.</p>' in page


def test_bulletin_expiry_is_compared_in_the_right_time_zone(shopper, company):
    # Expiry entered by the supplier in India time, an hour either side of now
    tz = company.timezone
    soon = parse_local_datetime(local_input_value(utcnow() + timedelta(hours=1), tz), tz)
    earlier = parse_local_datetime(local_input_value(utcnow() - timedelta(hours=1), tz), tz)
    bulletin(company, "Still on", expires_at=soon)
    bulletin(company, "Just ended", expires_at=earlier)
    page = home(shopper)
    assert "Still on" in page and "Just ended" not in page


def test_bulletin_markup_is_escaped(shopper, company):
    bulletin(company, "<b>Bold</b>", body="<script>alert(1)</script>")
    page = home(shopper)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


# Stats and recent orders


def test_stats_count_open_orders_and_only_accepted_spend_this_month(shopper, buyer, company, carton, other_company):
    start, _ = month_bounds(utcnow(), company.timezone)
    this_month = start + timedelta(minutes=30)
    make_order(buyer, [(carton, 1)], status=PENDING, placed_at=this_month)  # ₹118
    make_order(buyer, [(carton, 2)], status=APPROVED, placed_at=this_month)  # ₹236
    make_order(buyer, [(carton, 3)], status=FULFILLED, placed_at=this_month)  # ₹354
    make_order(buyer, [(carton, 4)], status=REJECTED, placed_at=this_month, reason="No stock")
    make_order(buyer, [(carton, 5)], status=CANCELLED, placed_at=this_month)
    # Fulfilled, but placed half an hour before the month began in India
    make_order(buyer, [(carton, 6)], status=FULFILLED, placed_at=start - timedelta(minutes=30))
    neighbour = factories.make_client(company, email="neighbour@buyer.example")
    make_order(neighbour, [(carton, 7)], status=FULFILLED, placed_at=this_month)
    make_order(other_company["client"], [(other_company["product"], 8)], status=FULFILLED, placed_at=this_month)

    page = home(shopper)
    assert "Welcome back, Ananya" in page
    assert stat_value(page, "Open orders") == "2"
    assert stat_value(page, "Spent this month") == "₹590.00"  # approved ₹236 + fulfilled ₹354
    assert stat_value(page, "Total orders") == "6"
    month_name = start.astimezone(ZoneInfo(company.timezone)).strftime("%B %Y")
    assert f"Approved and fulfilled orders placed in {month_name}" in page


def test_spend_is_zero_when_nothing_was_accepted_this_month(shopper, buyer, carton):
    make_order(buyer, [(carton, 1)], status=PENDING)
    page = home(shopper)
    assert stat_value(page, "Spent this month") == "₹0.00"
    assert stat_value(page, "Open orders") == "1"


def test_recent_orders_show_the_latest_five(shopper, buyer, carton):
    start = utcnow() - timedelta(days=20)
    orders = [make_order(buyer, [(carton, 1)], placed_at=start + timedelta(days=n)) for n in range(7)]
    orders[-1].status = APPROVED
    db.session.commit()
    page = home(shopper)
    numbers = re.findall(r'href="/portal/orders/([^"]+)">', page)
    assert numbers == [order.number for order in reversed(orders)][:5]
    assert "badge--approved" in page and "badge--pending" in page
    assert 'href="/portal/orders">See all orders' in page
    assert "None" not in page


def test_recent_orders_are_only_the_clients_own(shopper, buyer, company, carton):
    neighbour = factories.make_client(company, email="neighbour@buyer.example")
    mine = make_order(buyer, [(carton, 1)])
    theirs = make_order(neighbour, [(carton, 1)])
    page = home(shopper)
    assert mine.number in page and theirs.number not in page
    assert stat_value(page, "Total orders") == "1"


# Month boundaries


@pytest.mark.parametrize(
    "now, tz, start, end",
    [
        # 18 Oct 2026 in India
        (
            datetime(2026, 10, 18, 6, 0, tzinfo=UTC),
            "Asia/Kolkata",
            datetime(2026, 9, 30, 18, 30, tzinfo=UTC),
            datetime(2026, 10, 31, 18, 30, tzinfo=UTC),
        ),
        # 30 Sep 2026, 19:00 UTC is already 1 October in India
        (
            datetime(2026, 9, 30, 19, 0, tzinfo=UTC),
            "Asia/Kolkata",
            datetime(2026, 9, 30, 18, 30, tzinfo=UTC),
            datetime(2026, 10, 31, 18, 30, tzinfo=UTC),
        ),
        # December rolls over to January
        (
            datetime(2026, 12, 15, 12, 0, tzinfo=UTC),
            "UTC",
            datetime(2026, 12, 1, tzinfo=UTC),
            datetime(2027, 1, 1, tzinfo=UTC),
        ),
        # An unknown zone falls back to India time
        (
            datetime(2026, 10, 18, 6, 0, tzinfo=UTC),
            "Not/AZone",
            datetime(2026, 9, 30, 18, 30, tzinfo=UTC),
            datetime(2026, 10, 31, 18, 30, tzinfo=UTC),
        ),
    ],
)
def test_month_bounds(now, tz, start, end):
    assert month_bounds(now, tz) == (start, end)
