import re
from datetime import date, timedelta

import pytest

from orderflow.admin import dashboard as dashboard_view
from orderflow.extensions import db
from orderflow.models import APPROVED, CANCELLED, FULFILLED, PENDING, REJECTED
from orderflow.services.analytics import DayRevenue, Kpis, SetupProgress, month_window
from tests import factories
from tests.test_admin_orders_support import ist, make_order

NOW = ist(2026, 10, 18, 9, 30)  # a Sunday morning in India


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(dashboard_view, "utcnow", lambda: NOW)


@pytest.fixture
def stocked(company, owner, admin, buyer):
    """A company with a catalog, a few clients and orders in every status."""
    company.address = "14, 2nd Cross, Peenya, Bengaluru"
    db.session.commit()
    carton = factories.make_product(company, sku="CTN-1", name="Carton, 5-ply", price_paise=4_250, gst_rate_bp=1800)
    tape = factories.make_product(company, sku="TAP-1", name="BOPP tape", price_paise=6_800, gst_rate_bp=1800)
    hotel = factories.make_client(company, email="joseph@coastline.example", buyer_company="Coastline Hotels")
    orders = {
        "fulfilled": make_order(company, buyer, [(carton, 100)], FULFILLED, ist(2026, 10, 2, 11, 0), owner),
        "approved": make_order(company, hotel, [(tape, 10)], APPROVED, ist(2026, 10, 12, 15, 0), admin),
        "old_pending": make_order(company, hotel, [(carton, 4)], PENDING, ist(2026, 10, 10, 9, 0)),
        "pending": make_order(company, buyer, [(tape, 3)], PENDING, ist(2026, 10, 17, 18, 0)),
        "rejected": make_order(company, buyer, [(tape, 999)], REJECTED, ist(2026, 10, 5, 9, 0), owner),
        "cancelled": make_order(company, hotel, [(tape, 999)], CANCELLED, ist(2026, 10, 6, 9, 0)),
        "september": make_order(company, buyer, [(carton, 50)], FULFILLED, ist(2026, 9, 3, 9, 0), owner),
    }
    return {"carton": carton, "tape": tape, "hotel": hotel, "orders": orders}


def page(client, path="/app/"):
    response = client.get(path)
    assert response.status_code == 200, response.headers.get("Location")
    return response.get_data(as_text=True)


def assert_no_inline_code(html):
    assert 'style="' not in html
    assert "<style" not in html
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert not re.search(r"\son[a-z]+=", html)
    assert "None" not in re.sub(r"<[^>]+>", " ", html)


# The page -------------------------------------------------------------------


@pytest.mark.parametrize("who", ["owner", "admin"])
def test_dashboard_shows_this_months_numbers(client, login, stocked, request, who):
    login(request.getfixturevalue(who))
    html = page(client)

    assert "Good morning" in html
    assert "Sunday, 18 October" in html
    # October revenue: the fulfilled carton order (₹4,250.00 + GST) and the approved tape order
    october = 100 * 4_250 + 76_500 + 10 * 6_800 + 12_240
    assert dashboard_view.format_inr_compact(october) in html
    assert "Revenue in October" in html
    # September by the 18th: one fulfilled order of 50 cartons -> October is up
    assert "Up " in html and "on the same days in September" in html
    assert 'href="/app/orders?status=pending"' in html
    assert re.search(r'stat__label">Awaiting review</span>\s*<span class="stat__value">2<', html)
    assert re.search(r'stat__label">Orders in October</span>\s*<span class="stat__value">6<', html)
    assert "/app/orders?from=2026-10-01&amp;to=2026-10-18" in html
    assert re.search(r'stat__label">Active clients</span>\s*<span class="stat__value">2<', html)
    assert_no_inline_code(html)


def test_dashboard_lists_pending_orders_oldest_first_with_links(client, login, owner, stocked):
    login(owner)
    html = page(client)
    queue = html.split('id="queue-title"')[1].split("</section>")[0]
    old, new = stocked["orders"]["old_pending"], stocked["orders"]["pending"]
    assert queue.index(old.number) < queue.index(new.number)
    assert f'href="/app/orders/{old.number}"' in queue
    assert "Coastline Hotels" in queue
    assert stocked["orders"]["approved"].number not in queue
    assert "1 approved order to fulfil" in queue
    assert 'href="/app/orders?status=approved"' in queue


def test_dashboard_chart_is_accessible_svg(client, login, owner, stocked):
    login(owner)
    html = page(client)
    assert '<svg class="chart__svg" viewBox="0 0 720 240" role="img" aria-label="Revenue over the last 30 days:' in html
    assert html.count('class="chart__bar') == 30
    assert 'class="chart__bar chart__bar--empty"' in html
    assert "<title>2 Oct: ₹5,015.00 from 1 order</title>" in html
    assert "<caption>Revenue per day for the last 30 days</caption>" in html
    assert '<th scope="row">18 Oct 2026</th>' in html
    assert 'class="chart__label chart__label--x"' in html and ">18 Oct</text>" in html
    assert 'class="chart__label chart__label--y"' in html


def test_dashboard_top_lists_and_activity(client, login, owner, stocked):
    login(owner)
    html = page(client)
    products = html.split('id="top-products-title"')[1].split("</section>")[0]
    assert products.index("Carton, 5-ply") < products.index("BOPP tape")
    assert "CTN-1" in products and 'class="meter__fill" width="100"' in products
    clients = html.split('id="top-clients-title"')[1].split("</section>")[0]
    assert "Buyer Pvt Ltd" in clients and "Coastline Hotels" in clients

    activity = html.split('id="activity-title"')[1].split("</section>")[0]
    assert activity.count('class="timeline__item') == 8
    pending = stocked["orders"]["pending"]
    assert f'placed <a class="text-link mono" href="/app/orders/{pending.number}">{pending.number}</a>' in activity
    assert 'datetime="2026-10-17T18:00+0530"' in activity


def test_dashboard_never_shows_another_companys_data(client, login, owner, stocked, other_company):
    rival = other_company
    make_order(rival["company"], rival["client"], [(rival["product"], 7)], PENDING, ist(2026, 10, 1, 9, 0))
    login(owner)
    html = page(client)
    assert "Rival Traders" not in html
    assert "RIVAL-1" not in html
    assert re.search(r'stat__label">Awaiting review</span>\s*<span class="stat__value">2<', html)


def test_new_company_sees_the_getting_started_checklist_and_empty_states(client, login, owner):
    login(owner)
    html = page(client)
    assert "Get your workspace ready" in html
    assert "0 of 4 done" in html
    for title, link in [
        ("Add your first product", "/app/products"),
        ("Invite your first client", "/app/clients"),
        ("Add your company details", "/app/settings"),
    ]:
        assert title in html and f'href="{link}"' in html
    assert "Receive your first order" in html
    assert 'data-copy="http://localhost/login"' in html
    # The address is on the page too, for browsers that can't copy
    assert 'Clients sign in at <span class="mono">http://localhost/login</span>' in html

    assert "₹0" in html
    assert "No approved orders placed this month yet" in html
    assert "You&#39;re all caught up" in html
    assert "No approved orders in the last 30 days" in html
    assert 'class="chart__svg"' not in html
    assert "Your best sellers will appear here" in html
    assert "Clients who order the most will appear here" in html
    assert "each step is listed here" in html
    assert_no_inline_code(html)


def test_checklist_marks_finished_steps_done_and_hides_when_done(client, login, owner, company, buyer):
    login(owner)
    product = factories.make_product(company)
    html = page(client)
    assert "2 of 4 done" in html  # a product and a client
    items = re.findall(r'<li class="checklist__item( is-done)?">(.*?)</li>', html, re.S)
    assert [bool(done) for done, _ in items] == [True, True, False, False]
    titles = [re.sub(r"<[^>]+>", "", re.search(r'checklist__title">(.*?)</p>', body).group(1)) for _, body in items]
    assert titles == [
        "Done: Add your first product",
        "Done: Invite your first client",
        "Add your company details",
        "Receive your first order",
    ]
    # Finished steps have no buttons; the first step left gets the primary one
    assert 'href="/app/products"' not in items[0][1] and 'href="/app/clients"' not in items[1][1]
    assert "btn--primary btn--sm" in items[2][1] and 'href="/app/settings"' in items[2][1]
    assert "data-copy" in items[3][1]

    company.address = "Plot 7, MIDC, Pune"
    make_order(company, buyer, [(product, 1)], PENDING, ist(2026, 10, 18, 9, 0))
    html = page(client)
    assert "Get your workspace ready" not in html


def test_dashboard_is_for_verified_staff_only(client, login, buyer, company):
    assert client.get("/app/").headers["Location"].startswith("/login")
    login(buyer)
    assert client.get("/app/").headers["Location"] == "/portal/"

    client.post("/logout")
    unverified = factories.make_admin(company, email="new@kaveri.example", verified=False)
    login(unverified)
    assert client.get("/app/").headers["Location"].endswith("/verify")


# Chart layout and copy -------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [(0, 0), (-5, 0), (1, 1), (7, 10), (10, 10), (11, 20), (21, 25), (26, 50), (51, 100), (4_73_000, 5_00_000)],
)
def test_nice_ceiling(value, expected):
    assert dashboard_view.nice_ceiling(value) == expected


def test_bar_chart_layout():
    days = [DayRevenue(date(2026, 10, 1) + timedelta(days=n), 0, 0) for n in range(30)]
    days[5] = DayRevenue(days[5].day, 4_000_00, 2)
    days[29] = DayRevenue(days[29].day, 1_000_00, 1)
    chart = dashboard_view.build_bar_chart(days)

    assert chart.axis_max_paise == 5_000_00
    assert [tick.label for tick in chart.y_ticks] == ["₹0", "₹2.5K", "₹5K"]
    plot_height = chart.bottom - chart.top
    assert chart.bars[5].height == pytest.approx(plot_height * 0.8, abs=0.01)
    assert chart.bars[5].y == pytest.approx(chart.bottom - plot_height * 0.8, abs=0.01)
    assert chart.bars[0].height == 1.5  # empty days are a thin stub on the baseline
    assert all(bar.x >= chart.left and bar.x + bar.width <= chart.right for bar in chart.bars)
    assert [tick.label for tick in chart.x_ticks] == ["2 Oct", "9 Oct", "16 Oct", "23 Oct", "30 Oct"]
    assert chart.total_paise == 5_000_00 and chart.total_orders == 3
    assert chart.summary == ("Revenue over the last 30 days: ₹5,000.00 from 3 orders. Best day: 6 Oct, ₹4,000.00.")
    assert chart.bars[5].label == "6 Oct: ₹4,000.00 from 2 orders"
    assert chart.bars[29].label == "30 Oct: ₹1,000.00 from 1 order"
    assert chart.bars[0].label == "1 Oct: no revenue"


def test_tiny_amounts_still_get_a_visible_bar():
    days = [DayRevenue(date(2026, 10, 1), 1_00_000_00, 1), DayRevenue(date(2026, 10, 2), 1, 1)]
    chart = dashboard_view.build_bar_chart(days)
    assert chart.bars[1].height == 2


def test_empty_chart():
    chart = dashboard_view.build_bar_chart([DayRevenue(date(2026, 10, 1), 0, 0)])
    assert chart.is_empty
    assert chart.summary == "No revenue in the last 1 day."
    assert [tick.label for tick in chart.y_ticks] == ["₹0"]


@pytest.mark.parametrize(
    "hour, expected",
    [
        (0, "Good morning"),
        (11, "Good morning"),
        (12, "Good afternoon"),
        (16, "Good afternoon"),
        (17, "Good evening"),
        (23, "Good evening"),
    ],
)
def test_greeting(hour, expected):
    assert dashboard_view.greeting(hour) == expected


def _kpis(revenue, change):
    return Kpis(
        month=month_window(NOW, "Asia/Kolkata"),
        revenue_paise=revenue,
        previous_revenue_paise=0,
        previous_month_revenue_paise=0,
        revenue_change=change,
        orders_this_month=0,
        orders_previous_period=0,
        pending_orders=0,
        approved_orders=0,
        active_clients=0,
        invited_clients=0,
    )


@pytest.mark.parametrize(
    "revenue, change, expected",
    [
        (0, None, "No approved orders placed this month yet"),
        (500, None, "Nothing to compare with in September yet"),
        (500, 12, "Up 12% on the same days in September"),
        (500, 1250, "Up 1,250% on the same days in September"),
        (500, -40, "Down 40% on the same days in September"),
        (500, 0, "Level with the same days in September"),
    ],
)
def test_revenue_hint(revenue, change, expected):
    assert dashboard_view.revenue_hint(_kpis(revenue, change)) == expected


def test_checklist_steps_follow_progress(app):
    with app.test_request_context():
        assert dashboard_view.checklist_steps(SetupProgress(True, True, True, True)) == []
        steps = dashboard_view.checklist_steps(SetupProgress(False, True, False, False))
        later = dashboard_view.checklist_steps(SetupProgress(True, True, False, False))
    assert [step.title for step in steps] == [
        "Add your first product",
        "Invite your first client",
        "Add your company details",
        "Receive your first order",
    ]
    assert [step.done for step in steps] == [False, True, False, False]
    assert [step.is_next for step in steps] == [True, False, False, False]
    assert [step.is_next for step in later] == [False, False, True, False]
    assert steps[-1].action_url is None
