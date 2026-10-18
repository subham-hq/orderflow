"""Numbers for the staff dashboard.

Every function takes the company and the current time ("now", an aware
datetime) so results are reproducible in tests. Periods follow the
company's time zone: "this month" starts at local midnight on the 1st and a
day runs from local midnight to local midnight.

Revenue means the total (including GST) of orders that are approved or
fulfilled, dated by when the order was placed. Pending, rejected and
cancelled orders never count.

All aggregation happens in SQL; each function runs a fixed, small number of
queries however many orders a company has.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import contains_eager, joinedload

from ..extensions import db
from ..models import APPROVED, CLIENT, PENDING, REVENUE_STATUSES, Order, OrderEvent, OrderItem, Product, User
from ..timeutil import as_utc, get_zone

CHART_DAYS = 30
TOP_LIST_DAYS = 90


def _int(value):
    """SUM() comes back as None for no rows and as Decimal on Postgres."""
    return int(value or 0)


def local_midnight(day, tz_name):
    """The UTC instant at which `day` starts in the given time zone."""
    zone = get_zone(tz_name)
    return datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(UTC)


def local_today(now, tz_name):
    return as_utc(now).astimezone(get_zone(tz_name)).date()


def percent_change(current, previous):
    """Whole-number % change from previous to current, or None without a baseline."""
    if not previous:
        return None
    change = Decimal(current - previous) * 100 / Decimal(previous)
    return int(change.quantize(Decimal(1), rounding=ROUND_HALF_UP))


# Month-level KPIs ------------------------------------------------------------


@dataclass(frozen=True)
class MonthWindow:
    start: datetime  # start of this month (UTC)
    end: datetime  # start of next month (UTC)
    previous_start: datetime  # start of last month (UTC)
    previous_cutoff: datetime  # the same point in last month as "now" is in this one
    label: str  # "October"
    previous_label: str  # "September"


def month_window(now, tz_name):
    now = as_utc(now)
    first = local_today(now, tz_name).replace(day=1)
    next_first = (first + timedelta(days=32)).replace(day=1)
    previous_first = (first - timedelta(days=1)).replace(day=1)
    start = local_midnight(first, tz_name)
    previous_start = local_midnight(previous_first, tz_name)
    # Compare like with like: 1-18 October against 1-18 September. When last
    # month was shorter (31 March vs February), stop at the end of it.
    previous_cutoff = min(previous_start + (now - start), start)
    return MonthWindow(
        start=start,
        end=local_midnight(next_first, tz_name),
        previous_start=previous_start,
        previous_cutoff=previous_cutoff,
        label=first.strftime("%B"),
        previous_label=previous_first.strftime("%B"),
    )


@dataclass(frozen=True)
class Kpis:
    month: MonthWindow
    revenue_paise: int  # this month so far
    previous_revenue_paise: int  # last month, up to the same day and time
    previous_month_revenue_paise: int  # all of last month
    revenue_change: int | None  # % vs the same period last month; None without a baseline
    orders_this_month: int
    orders_previous_period: int
    pending_orders: int
    approved_orders: int  # approved and waiting to be fulfilled
    active_clients: int
    invited_clients: int  # invited but not signed up yet


def kpis(company, now):
    window = month_window(now, company.timezone)
    is_revenue = Order.status.in_(REVENUE_STATUSES)

    def revenue_between(start, end):
        placed = and_(Order.placed_at >= start, Order.placed_at < end, is_revenue)
        return func.sum(case((placed, Order.total_paise), else_=0))

    def count_between(start, end):
        return func.count(case((and_(Order.placed_at >= start, Order.placed_at < end), Order.id)))

    month = db.session.execute(
        select(
            revenue_between(window.start, window.end),
            revenue_between(window.previous_start, window.previous_cutoff),
            revenue_between(window.previous_start, window.start),
            count_between(window.start, window.end),
            count_between(window.previous_start, window.previous_cutoff),
        ).where(
            Order.company_id == company.id,
            Order.placed_at >= window.previous_start,
            Order.placed_at < window.end,
        )
    ).one()

    open_orders = db.session.execute(
        select(
            func.count(case((Order.status == PENDING, Order.id))),
            func.count(case((Order.status == APPROVED, Order.id))),
        ).where(Order.company_id == company.id, Order.status.in_((PENDING, APPROVED)))
    ).one()

    clients = db.session.execute(
        select(
            func.count(case((User.password_hash.is_not(None), User.id))),
            func.count(case((User.password_hash.is_(None), User.id))),
        ).where(User.company_id == company.id, User.role == CLIENT, User.active.is_(True))
    ).one()

    revenue, previous, previous_month = (_int(value) for value in month[:3])
    return Kpis(
        month=window,
        revenue_paise=revenue,
        previous_revenue_paise=previous,
        previous_month_revenue_paise=previous_month,
        revenue_change=percent_change(revenue, previous),
        orders_this_month=_int(month[3]),
        orders_previous_period=_int(month[4]),
        pending_orders=_int(open_orders[0]),
        approved_orders=_int(open_orders[1]),
        active_clients=_int(clients[0]),
        invited_clients=_int(clients[1]),
    )


# Revenue chart -----------------------------------------------------------------


@dataclass(frozen=True)
class DayRevenue:
    day: date
    revenue_paise: int
    orders: int


def revenue_by_day(company, now, days=CHART_DAYS):
    """Revenue per local day for the last `days` days, oldest first, today last."""
    today = local_today(now, company.timezone)
    dates = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    edges = [local_midnight(day, company.timezone) for day in dates]
    edges.append(local_midnight(today + timedelta(days=1), company.timezone))

    # Number each order by the day it falls in, then group by that number.
    # Comparing against precomputed UTC edges keeps this portable (no
    # database-specific time zone functions) and correct across DST changes.
    bucket = case(*[(Order.placed_at < edges[index + 1], index) for index in range(days)]).label("bucket")
    numbered = (
        select(bucket, Order.total_paise.label("total"))
        .where(
            Order.company_id == company.id,
            Order.status.in_(REVENUE_STATUSES),
            Order.placed_at >= edges[0],
            Order.placed_at < edges[-1],
        )
        .subquery()
    )
    rows = db.session.execute(
        select(numbered.c.bucket, func.sum(numbered.c.total), func.count()).group_by(numbered.c.bucket)
    ).all()
    totals = {int(index): (_int(revenue), int(count)) for index, revenue, count in rows if index is not None}
    return [DayRevenue(day, *totals.get(index, (0, 0))) for index, day in enumerate(dates)]


# Lists -------------------------------------------------------------------------


def pending_queue(company, limit=6):
    """Pending orders, oldest first: the ones that have waited longest."""
    return db.session.scalars(
        select(Order)
        .where(Order.company_id == company.id, Order.status == PENDING)
        .options(joinedload(Order.client))
        .order_by(Order.placed_at.asc(), Order.id.asc())
        .limit(limit)
    ).all()


@dataclass(frozen=True)
class TopProduct:
    sku: str
    name: str
    unit: str
    quantity: int
    revenue_paise: int


def top_products(company, now, days=TOP_LIST_DAYS, limit=5):
    """Best-selling products by revenue, from the order lines (as sold, not as listed now)."""
    since = as_utc(now) - timedelta(days=days)
    revenue = func.sum(OrderItem.line_total_paise)
    rows = db.session.execute(
        select(
            OrderItem.sku,
            func.sum(OrderItem.quantity),
            revenue,
            func.max(OrderItem.id),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.company_id == company.id,
            Order.status.in_(REVENUE_STATUSES),
            Order.placed_at >= since,
            Order.placed_at <= as_utc(now),
        )
        .group_by(OrderItem.sku)
        .order_by(revenue.desc(), OrderItem.sku)
        .limit(limit)
    ).all()
    if not rows:
        return []
    # Name each product after its most recent order line, so a renamed
    # product shows its current name once.
    latest = {
        item_id: (name, unit)
        for item_id, name, unit in db.session.execute(
            select(OrderItem.id, OrderItem.name, OrderItem.unit).where(OrderItem.id.in_([row[3] for row in rows]))
        )
    }
    return [
        TopProduct(sku, *latest[latest_id], quantity=_int(quantity), revenue_paise=_int(total))
        for sku, quantity, total, latest_id in rows
    ]


@dataclass(frozen=True)
class TopClient:
    client: User
    orders: int
    revenue_paise: int


def top_clients(company, now, days=TOP_LIST_DAYS, limit=5):
    since = as_utc(now) - timedelta(days=days)
    totals = (
        select(
            Order.client_id.label("client_id"),
            func.count(Order.id).label("orders"),
            func.sum(Order.total_paise).label("revenue"),
        )
        .where(
            Order.company_id == company.id,
            Order.status.in_(REVENUE_STATUSES),
            Order.placed_at >= since,
            Order.placed_at <= as_utc(now),
        )
        .group_by(Order.client_id)
        .subquery()
    )
    rows = db.session.execute(
        select(User, totals.c.orders, totals.c.revenue)
        .join(totals, totals.c.client_id == User.id)
        .where(User.company_id == company.id)
        .order_by(totals.c.revenue.desc(), User.id)
        .limit(limit)
    ).all()
    return [TopClient(user, _int(orders), _int(revenue)) for user, orders, revenue in rows]


def recent_activity(company, limit=8):
    """The latest order events in the company, newest first, with order, client and actor loaded."""
    return db.session.scalars(
        select(OrderEvent)
        .join(OrderEvent.order)
        .where(Order.company_id == company.id)
        .options(
            contains_eager(OrderEvent.order).joinedload(Order.client),
            joinedload(OrderEvent.actor),
        )
        .order_by(OrderEvent.created_at.desc(), OrderEvent.id.desc())
        .limit(limit)
    ).all()


# Getting started ---------------------------------------------------------------


@dataclass(frozen=True)
class SetupProgress:
    has_products: bool
    has_clients: bool
    has_company_details: bool
    has_orders: bool

    @property
    def done(self):
        return sum((self.has_products, self.has_clients, self.has_company_details, self.has_orders))

    @property
    def total(self):
        return 4

    @property
    def complete(self):
        return self.done == self.total


def setup_progress(company):
    has_products, has_clients, has_orders = db.session.execute(
        select(
            select(Product.id).where(Product.company_id == company.id).exists(),
            select(User.id).where(User.company_id == company.id, User.role == CLIENT).exists(),
            select(Order.id).where(Order.company_id == company.id).exists(),
        )
    ).one()
    # The address is what makes a proforma invoice usable.
    has_details = bool((company.address or "").strip())
    return SetupProgress(bool(has_products), bool(has_clients), has_details, bool(has_orders))
