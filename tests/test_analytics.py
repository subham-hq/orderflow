from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event

from orderflow.extensions import db
from orderflow.models import APPROVED, CANCELLED, FULFILLED, PENDING, REJECTED
from orderflow.services import analytics
from tests import factories
from tests.test_admin_orders_support import ist, make_order


@contextmanager
def count_queries():
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(db.engine, "before_cursor_execute", record)


@pytest.fixture
def widget(company):
    # ₹1,000 + 18% GST = ₹1,180 per unit
    return factories.make_product(company, sku="WID", name="Widget", price_paise=1_000_00, gst_rate_bp=1800)


@pytest.fixture
def gadget(company):
    # ₹500, no GST
    return factories.make_product(company, sku="GAD", name="Gadget", price_paise=500_00, gst_rate_bp=0)


# percent_change and month windows --------------------------------------------


@pytest.mark.parametrize(
    "current, previous, expected",
    [(150, 100, 50), (50, 100, -50), (100, 100, 0), (0, 100, -100), (1, 3, -67), (2, 3, -33), (300, 100, 200)],
)
def test_percent_change(current, previous, expected):
    assert analytics.percent_change(current, previous) == expected


@pytest.mark.parametrize("current", [0, 100])
def test_percent_change_without_baseline_is_none(current):
    assert analytics.percent_change(current, 0) is None


def test_month_window_follows_the_company_time_zone():
    # 00:30 on 1 November in India is still 31 October in UTC
    window = analytics.month_window(ist(2026, 11, 1, 0, 30), "Asia/Kolkata")
    assert window.label == "November" and window.previous_label == "October"
    assert window.start == datetime(2026, 10, 31, 18, 30, tzinfo=UTC)
    assert window.end == datetime(2026, 11, 30, 18, 30, tzinfo=UTC)
    assert window.previous_start == datetime(2026, 9, 30, 18, 30, tzinfo=UTC)
    assert window.previous_cutoff == window.previous_start + timedelta(minutes=30)

    # 23:30 on 31 October in India (18:00 UTC) is October
    assert analytics.month_window(ist(2026, 10, 31, 23, 30), "Asia/Kolkata").label == "October"


def test_month_window_wraps_the_year_and_clips_short_months():
    january = analytics.month_window(ist(2026, 1, 15, 9, 0), "Asia/Kolkata")
    assert january.previous_label == "December"
    assert january.previous_start == ist(2025, 12, 1)

    # 31 March is compared with all of February, not with 3 March
    march = analytics.month_window(ist(2026, 3, 31, 10, 0), "Asia/Kolkata")
    assert march.previous_start == ist(2026, 2, 1)
    assert march.previous_cutoff == march.start == ist(2026, 3, 1)


def test_unknown_time_zone_falls_back_to_india():
    assert analytics.local_midnight(date(2026, 10, 1), "Not/AZone") == ist(2026, 10, 1)


# KPIs ------------------------------------------------------------------------


def test_kpis_count_revenue_by_local_month_across_the_ist_boundary(company, owner, buyer, widget, other_company):
    now = ist(2026, 11, 1, 6, 0)
    make_order(company, buyer, [(widget, 1)], APPROVED, ist(2026, 10, 31, 23, 30), owner)  # October
    make_order(company, buyer, [(widget, 2)], FULFILLED, ist(2026, 11, 1, 0, 30), owner)  # November (31 Oct in UTC)
    make_order(company, buyer, [(widget, 5)], PENDING, ist(2026, 11, 1, 1, 0))
    make_order(company, buyer, [(widget, 7)], REJECTED, ist(2026, 11, 1, 2, 0), owner)
    make_order(company, buyer, [(widget, 9)], CANCELLED, ist(2026, 11, 1, 3, 0))
    rival = other_company
    make_order(rival["company"], rival["client"], [(rival["product"], 50)], APPROVED, ist(2026, 11, 1, 1, 0))

    result = analytics.kpis(company, now)
    assert result.month.label == "November"
    assert result.revenue_paise == 2 * 1_180_00  # only the fulfilled order
    assert result.previous_month_revenue_paise == 1_180_00
    assert result.previous_revenue_paise == 0  # nothing by 06:00 on 1 October
    assert result.revenue_change is None
    assert result.orders_this_month == 4  # every status counts as an order placed
    assert result.pending_orders == 1


def test_kpis_compare_with_the_same_days_last_month(company, owner, buyer, widget, gadget):
    now = ist(2026, 10, 20, 12, 0)
    make_order(company, buyer, [(gadget, 2)], APPROVED, ist(2026, 9, 10, 10, 0), owner)  # ₹1,000
    make_order(company, buyer, [(gadget, 4)], FULFILLED, ist(2026, 9, 25, 10, 0), owner)  # after 20 Sept
    make_order(company, buyer, [(gadget, 3)], FULFILLED, ist(2026, 10, 2, 10, 0), owner)  # ₹1,500

    result = analytics.kpis(company, now)
    assert result.revenue_paise == 1_500_00
    assert result.previous_revenue_paise == 1_000_00
    assert result.previous_month_revenue_paise == 3_000_00
    assert result.revenue_change == 50
    assert result.orders_this_month == 1 and result.orders_previous_period == 1


def test_kpis_for_an_empty_company(company):
    result = analytics.kpis(company, ist(2026, 10, 18, 9, 0))
    assert result.revenue_paise == result.previous_revenue_paise == 0
    assert result.revenue_change is None
    assert (result.orders_this_month, result.pending_orders, result.approved_orders) == (0, 0, 0)
    assert (result.active_clients, result.invited_clients) == (0, 0)


def test_kpis_count_open_orders_of_any_age_and_clients_who_can_sign_in(company, owner, buyer, widget, other_company):
    now = ist(2026, 10, 18, 9, 0)
    make_order(company, buyer, [(widget, 1)], PENDING, ist(2025, 1, 5, 9, 0))  # long-forgotten
    make_order(company, buyer, [(widget, 1)], PENDING, ist(2026, 10, 18, 8, 0))
    make_order(company, buyer, [(widget, 1)], APPROVED, ist(2026, 6, 1, 8, 0), owner)
    make_order(company, buyer, [(widget, 1)], FULFILLED, ist(2026, 6, 1, 8, 0), owner)
    factories.make_client(company, email="suspended@example.com", active=False)
    factories.make_client(company, email="invited@example.com", password=None)
    factories.make_admin(company, email="staff@example.com")  # staff are not clients

    result = analytics.kpis(company, now)
    assert result.pending_orders == 2
    assert result.approved_orders == 1
    assert result.active_clients == 1  # buyer only; rival tenant's client not counted
    assert result.invited_clients == 1


def test_kpis_use_a_fixed_number_of_queries(company, owner, buyer, widget):
    for day in range(1, 11):
        make_order(company, buyer, [(widget, 1)], APPROVED, ist(2026, 10, day, 9, 0), owner)
    assert company.timezone  # load the company before counting
    with count_queries() as statements:
        analytics.kpis(company, ist(2026, 10, 18, 9, 0))
    assert len(statements) == 3


# Revenue chart ---------------------------------------------------------------


def test_revenue_by_day_buckets_by_local_day(company, owner, buyer, widget, gadget, other_company):
    now = ist(2026, 10, 18, 9, 0)
    make_order(company, buyer, [(gadget, 1)], APPROVED, ist(2026, 10, 18, 0, 15), owner)  # 17 Oct in UTC
    make_order(company, buyer, [(gadget, 2)], FULFILLED, ist(2026, 10, 18, 8, 0), owner)
    make_order(company, buyer, [(widget, 1)], FULFILLED, ist(2026, 10, 17, 23, 50), owner)
    make_order(company, buyer, [(gadget, 3)], APPROVED, ist(2026, 9, 19, 0, 0), owner)  # first day of the window
    make_order(company, buyer, [(gadget, 9)], APPROVED, ist(2026, 9, 18, 23, 59), owner)  # one minute too early
    make_order(company, buyer, [(gadget, 9)], PENDING, ist(2026, 10, 18, 7, 0))
    make_order(company, buyer, [(gadget, 9)], REJECTED, ist(2026, 10, 18, 7, 0), owner)
    rival = other_company
    make_order(rival["company"], rival["client"], [(rival["product"], 5)], APPROVED, ist(2026, 10, 18, 7, 0))

    days = analytics.revenue_by_day(company, now)
    assert len(days) == 30
    assert days[0].day == date(2026, 9, 19) and days[-1].day == date(2026, 10, 18)
    assert (days[-1].revenue_paise, days[-1].orders) == (1_500_00, 2)
    assert (days[-2].revenue_paise, days[-2].orders) == (1_180_00, 1)
    assert (days[0].revenue_paise, days[0].orders) == (1_500_00, 1)
    assert sum(day.revenue_paise for day in days) == 4_180_00
    assert all(day.revenue_paise == 0 for day in days[1:-2])


def test_revenue_by_day_is_correct_across_a_daylight_saving_change(company, owner, buyer, gadget):
    company.timezone = "America/New_York"  # clocks go back on 1 November 2026
    db.session.commit()
    new_york = ZoneInfo("America/New_York")

    def local(*parts):
        return datetime(*parts, tzinfo=new_york).astimezone(UTC)

    make_order(company, buyer, [(gadget, 1)], APPROVED, local(2026, 11, 1, 0, 30), owner)  # EDT
    make_order(company, buyer, [(gadget, 2)], APPROVED, local(2026, 11, 1, 23, 30), owner)  # EST
    make_order(company, buyer, [(gadget, 4)], APPROVED, local(2026, 11, 2, 0, 30), owner)

    days = {day.day: day.revenue_paise for day in analytics.revenue_by_day(company, local(2026, 11, 5, 12, 0))}
    assert days[date(2026, 11, 1)] == 1_500_00
    assert days[date(2026, 11, 2)] == 2_000_00


def test_revenue_by_day_with_no_orders(company):
    days = analytics.revenue_by_day(company, ist(2026, 10, 18, 9, 0), days=7)
    assert [day.day for day in days] == [date(2026, 10, 12) + timedelta(days=n) for n in range(7)]
    assert all(day.revenue_paise == 0 and day.orders == 0 for day in days)


# Lists -----------------------------------------------------------------------


def test_pending_queue_is_oldest_first_and_capped(company, owner, buyer, widget, other_company):
    created = [
        make_order(company, buyer, [(widget, 1)], PENDING, ist(2026, 10, day, 9, 0)) for day in (5, 3, 9, 1, 7, 2, 8)
    ]
    make_order(company, buyer, [(widget, 1)], APPROVED, ist(2026, 9, 1, 9, 0), owner)
    rival = other_company
    make_order(rival["company"], rival["client"], [(rival["product"], 1)], PENDING, ist(2026, 1, 1, 9, 0))

    queue = analytics.pending_queue(company)
    assert len(queue) == 6
    assert [order.placed_at.day for order in queue] == [1, 2, 3, 5, 7, 8]
    assert {order.id for order in queue} <= {order.id for order in created}


def test_top_products_use_order_line_snapshots(company, owner, buyer, widget, gadget, other_company):
    now = ist(2026, 10, 18, 9, 0)
    make_order(company, buyer, [(widget, 2), (gadget, 1)], FULFILLED, ist(2026, 10, 1, 9, 0), owner)
    make_order(company, buyer, [(gadget, 3)], APPROVED, ist(2026, 9, 1, 9, 0), owner)
    make_order(company, buyer, [(gadget, 50)], PENDING, ist(2026, 10, 1, 9, 0))
    make_order(company, buyer, [(gadget, 50)], REJECTED, ist(2026, 10, 1, 9, 0), owner)
    make_order(company, buyer, [(gadget, 50)], CANCELLED, ist(2026, 10, 1, 9, 0))
    make_order(company, buyer, [(gadget, 50)], FULFILLED, ist(2026, 7, 1, 9, 0), owner)  # older than 90 days
    rival = other_company
    make_order(rival["company"], rival["client"], [(rival["product"], 99)], APPROVED, ist(2026, 10, 1, 9, 0))

    # Renaming or repricing the product later doesn't rewrite what was sold
    widget.name, widget.price_paise = "Widget (new)", 1
    db.session.commit()

    top = analytics.top_products(company, now)
    assert [(p.sku, p.name, p.quantity, p.revenue_paise) for p in top] == [
        ("WID", "Widget", 2, 2_360_00),
        ("GAD", "Gadget", 4, 2_000_00),
    ]


def test_top_products_show_the_latest_name_and_respect_the_limit(company, owner, buyer, widget, gadget):
    now = ist(2026, 10, 18, 9, 0)
    make_order(company, buyer, [(widget, 1)], APPROVED, ist(2026, 10, 1, 9, 0), owner)
    widget.name = "Widget, heavy duty"
    db.session.commit()
    make_order(company, buyer, [(widget, 1)], APPROVED, ist(2026, 10, 2, 9, 0), owner)
    make_order(company, buyer, [(gadget, 1)], APPROVED, ist(2026, 10, 2, 9, 0), owner)

    top = analytics.top_products(company, now, limit=1)
    assert [(p.sku, p.name, p.quantity) for p in top] == [("WID", "Widget, heavy duty", 2)]
    assert analytics.top_products(factories.make_company(), now) == []


def test_top_clients_rank_by_revenue(company, owner, buyer, widget, gadget, other_company):
    now = ist(2026, 10, 18, 9, 0)
    second = factories.make_client(company, email="second@example.com", buyer_company="Second Co")
    quiet = factories.make_client(company, email="quiet@example.com")
    make_order(company, buyer, [(gadget, 1)], APPROVED, ist(2026, 10, 1, 9, 0), owner)
    make_order(company, buyer, [(gadget, 1)], FULFILLED, ist(2026, 10, 2, 9, 0), owner)
    make_order(company, second, [(widget, 3)], FULFILLED, ist(2026, 10, 3, 9, 0), owner)
    make_order(company, quiet, [(widget, 9)], PENDING, ist(2026, 10, 3, 9, 0))
    make_order(company, quiet, [(widget, 9)], FULFILLED, ist(2026, 6, 3, 9, 0), owner)  # too old
    rival = other_company
    make_order(rival["company"], rival["client"], [(rival["product"], 99)], APPROVED, ist(2026, 10, 1, 9, 0))

    top = analytics.top_clients(company, now)
    assert [(t.client.email, t.orders, t.revenue_paise) for t in top] == [
        ("second@example.com", 1, 3_540_00),
        (buyer.email, 2, 1_000_00),
    ]
    assert [t.client.email for t in analytics.top_clients(company, now, limit=1)] == ["second@example.com"]


def test_recent_activity_is_newest_first_and_loads_everything_up_front(company, owner, admin, buyer, widget):
    for day in range(1, 6):
        make_order(company, buyer, [(widget, 1)], FULFILLED, ist(2026, 10, day, 9, 0), admin)
    rival_company = factories.make_company("Elsewhere")
    rival_client = factories.make_client(rival_company, email="far@example.com")
    make_order(rival_company, rival_client, [(factories.make_product(rival_company), 1)], PENDING, ist(2026, 10, 9))

    events = analytics.recent_activity(company)
    assert len(events) == 8
    times = [e.created_at for e in events]
    assert times == sorted(times, reverse=True)
    assert events[0].to_status == FULFILLED and events[0].order.placed_at.day == 5
    assert all(e.order.company_id == company.id for e in events)

    db.session.expire_all()
    assert company.id  # load the company before counting
    with count_queries() as statements:
        events = analytics.recent_activity(company)
        labels = [(e.order.number, e.order.client.full_name, e.actor.full_name if e.actor else "") for e in events]
    assert len(labels) == 8
    assert len(statements) == 1


def test_setup_progress(company, widget):
    fresh = factories.make_company("Brand New")
    progress = analytics.setup_progress(fresh)
    assert (progress.done, progress.total, progress.complete) == (0, 4, False)

    factories.make_product(fresh)
    client = factories.make_client(fresh, email="first@example.com", password=None)  # invited counts
    fresh.address = "  "
    db.session.commit()
    progress = analytics.setup_progress(fresh)
    assert progress.has_products and progress.has_clients
    assert not progress.has_company_details  # blank address doesn't count
    assert not progress.has_orders

    fresh.address = "12 MG Road, Bengaluru"
    db.session.commit()
    make_order(fresh, client, [(factories.make_product(fresh), 1)])
    progress = analytics.setup_progress(fresh)
    assert progress.complete and progress.done == 4
