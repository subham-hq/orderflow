"""Order management: the order list with filters, order details and status changes."""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from flask import render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_, select
from sqlalchemy.orm import contains_eager

from ..extensions import db
from ..models import APPROVED, CANCELLED, FULFILLED, ORDER_STATUSES, PENDING, REJECTED, Order, OrderItem, User
from ..permissions import staff_required
from ..services.analytics import local_midnight
from . import bp

PER_PAGE = 25
MAX_PAGE = 10_000
SEARCH_MAX_LENGTH = 100
DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# Tabs on the order list, in order: (status or None for all, label)
STATUS_TABS = (
    (None, "All"),
    (PENDING, "Pending"),
    (APPROVED, "Approved"),
    (FULFILLED, "Fulfilled"),
    (REJECTED, "Rejected"),
    (CANCELLED, "Cancelled"),
)

# What an empty status tab says
EMPTY_TAB_TEXT = {
    PENDING: "You're all caught up. New orders from your clients will appear here for review.",
    APPROVED: "Orders you approve wait here until you mark them as fulfilled.",
    FULFILLED: "Orders you've delivered will be listed here.",
    REJECTED: "Orders you reject will be listed here with the reason you gave.",
    CANCELLED: "Orders cancelled by you or your clients will be listed here.",
}


# Filters ---------------------------------------------------------------------


def parse_day(value):
    """Parse a YYYY-MM-DD date from a filter. Raises ValueError for anything else."""
    if not DATE_PATTERN.fullmatch(value):
        raise ValueError(value)
    day = date.fromisoformat(value)
    # Keep well inside the range datetime can convert across time zones
    if not 1900 <= day.year <= 9998:
        raise ValueError(value)
    return day


def parse_page(value):
    text = (value or "").strip()
    if not text.isascii() or not text.isdigit():
        return 1
    if len(text) > 5:
        return MAX_PAGE
    return min(max(int(text), 1), MAX_PAGE)


@dataclass
class OrderFilters:
    """What the list (and the CSV export) is filtered by, parsed safely from the query string."""

    status: str | None = None
    q: str = ""
    date_from: date | None = None
    date_to: date | None = None
    raw_from: str = ""
    raw_to: str = ""
    messages: list = field(default_factory=list)

    @property
    def searching(self):
        """True when anything other than the status tab narrows the list."""
        return bool(self.q or self.date_from or self.date_to)

    def args(self, **changes):
        """Query-string arguments that reproduce these filters, for links."""
        values = {
            "status": self.status,
            "q": self.q,
            "from": self.date_from.isoformat() if self.date_from else None,
            "to": self.date_to.isoformat() if self.date_to else None,
        }
        values.update(changes)
        return {key: value for key, value in values.items() if value}


def parse_order_filters(args):
    filters = OrderFilters()

    status = (args.get("status") or "").strip().lower()
    filters.status = status if status in ORDER_STATUSES else None

    # Collapse runs of spaces; "#ORD-000012" finds ORD-000012
    filters.q = " ".join((args.get("q") or "").split())[:SEARCH_MAX_LENGTH]

    for key, label in (("from", "start"), ("to", "end")):
        raw = (args.get(key) or "").strip()[:32]
        setattr(filters, f"raw_{key}", raw)
        if not raw:
            continue
        try:
            day = parse_day(raw)
        except ValueError:
            filters.messages.append(
                f"We couldn't read the {label} date “{raw}”, so it isn't applied. Pick a date or use YYYY-MM-DD."
            )
        else:
            setattr(filters, f"date_{key}", day)

    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        filters.date_from, filters.date_to = filters.date_to, filters.date_from
        filters.raw_from, filters.raw_to = filters.date_from.isoformat(), filters.date_to.isoformat()
        filters.messages.append("The start date was after the end date, so we swapped them.")
    return filters


def filter_conditions(company, filters):
    """WHERE conditions for everything except the status (tabs count across statuses).

    Queries using these must join User on Order.client_id.
    """
    conditions = [Order.company_id == company.id]
    if filters.q:
        term = filters.q.lower()
        number_term = term.removeprefix("#")
        full_name = func.lower(User.first_name + " " + func.coalesce(User.last_name, ""))
        conditions.append(
            or_(
                func.lower(Order.number).contains(number_term, autoescape=True),
                full_name.contains(term, autoescape=True),
                func.lower(func.coalesce(User.buyer_company, "")).contains(term, autoescape=True),
                func.lower(User.email).contains(term, autoescape=True),
            )
        )
    if filters.date_from:
        conditions.append(Order.placed_at >= local_midnight(filters.date_from, company.timezone))
    if filters.date_to:
        conditions.append(Order.placed_at < local_midnight(filters.date_to + timedelta(days=1), company.timezone))
    return conditions


def filtered_orders(company, filters):
    """SELECT for the orders matching the filters, newest first, with the client loaded."""
    stmt = (
        select(Order)
        .join(User, User.id == Order.client_id)
        .where(*filter_conditions(company, filters))
        .options(contains_eager(Order.client))
        .order_by(Order.placed_at.desc(), Order.id.desc())
    )
    if filters.status:
        stmt = stmt.where(Order.status == filters.status)
    return stmt


def status_counts(company, filters):
    rows = db.session.execute(
        select(Order.status, func.count(Order.id))
        .join(User, User.id == Order.client_id)
        .where(*filter_conditions(company, filters))
        .group_by(Order.status)
    )
    return {status: count for status, count in rows}


def line_counts(orders):
    """Number of lines on each order, in one query."""
    ids = [order.id for order in orders]
    if not ids:
        return {}
    rows = db.session.execute(
        select(OrderItem.order_id, func.count(OrderItem.id))
        .where(OrderItem.order_id.in_(ids))
        .group_by(OrderItem.order_id)
    )
    return dict(rows.all())


@dataclass(frozen=True)
class Tab:
    label: str
    count: int
    url: str
    active: bool


# Views -----------------------------------------------------------------------


@bp.route("/orders")
@staff_required
def orders():
    company = current_user.company
    filters = parse_order_filters(request.args)
    counts = status_counts(company, filters)

    stmt = filtered_orders(company, filters)
    page = db.paginate(stmt, page=parse_page(request.args.get("page")), per_page=PER_PAGE, error_out=False)
    if not page.items and page.page > 1 and page.total:
        # Past the last page (an old link, or a hand-typed number): show the last one
        page = db.paginate(stmt, page=page.pages, per_page=PER_PAGE, error_out=False)

    if filters.searching:
        has_orders = db.session.scalar(select(select(Order.id).where(Order.company_id == company.id).exists()))
    else:
        has_orders = bool(counts)

    tabs = [
        Tab(
            label=label,
            count=sum(counts.values()) if status is None else counts.get(status, 0),
            url=url_for("admin.orders", **filters.args(status=status)),
            active=filters.status == status,
        )
        for status, label in STATUS_TABS
    ]
    status_label = dict(STATUS_TABS).get(filters.status, "All")
    return render_template(
        "admin/orders.html",
        filters=filters,
        page=page,
        line_counts=line_counts(page.items),
        tabs=tabs,
        has_orders=has_orders,
        status_label=status_label,
        empty_tab_text=EMPTY_TAB_TEXT.get(filters.status),
        clear_url=url_for("admin.orders", **({"status": filters.status} if filters.status else {})),
    )


@bp.route("/orders/<number>")
@staff_required
def order_detail(number):
    return render_template("placeholder.html", title=f"Order {number}", layout="layouts/app.html")
