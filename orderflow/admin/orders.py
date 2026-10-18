"""Order management: the order list with filters, order details, status changes and CSV export."""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from flask import Response, abort, flash, redirect, render_template, request, stream_with_context, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from sqlalchemy import func, or_, select
from sqlalchemy.orm import contains_eager, joinedload, selectinload
from wtforms import HiddenField, TextAreaField
from wtforms.validators import DataRequired, Length

from ..extensions import db
from ..models import (
    APPROVED,
    CANCELLED,
    FULFILLED,
    ORDER_STATUSES,
    PENDING,
    REJECTED,
    Order,
    OrderEvent,
    OrderItem,
    User,
)
from ..permissions import staff_required
from ..services.analytics import local_midnight
from ..services.orders import STATUS_LABELS, OrderError, allowed_transitions, transition_order
from ..timeutil import to_local, utcnow
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


def apply_filters(stmt, company, filters):
    """Narrow a SELECT over Order to the filtered orders (joining the client), newest first."""
    stmt = stmt.join(User, User.id == Order.client_id).where(*filter_conditions(company, filters))
    if filters.status:
        stmt = stmt.where(Order.status == filters.status)
    return stmt.order_by(Order.placed_at.desc(), Order.id.desc())


def filtered_orders(company, filters):
    """SELECT for the orders matching the filters, newest first, with the client loaded."""
    return apply_filters(select(Order).options(contains_eager(Order.client)), company, filters)


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


# Order details and status changes ----------------------------------------------

NOTE_MAX_LENGTH = 1000
NUMBER_MAX_LENGTH = Order.number.type.length

# How each change reads in messages: "ORD-000012 is fulfilled, so it can't be rejected."
CHANGE_VERBS = {
    PENDING: "moved back to pending review",
    APPROVED: "approved",
    REJECTED: "rejected",
    FULFILLED: "marked as fulfilled",
    CANCELLED: "cancelled",
}

SUCCESS_MESSAGES = {
    APPROVED: "Approved {number}. Mark it as fulfilled once the goods are delivered.",
    REJECTED: "Rejected {number}. Your reason is saved on the order for the client to see.",
    FULFILLED: "Marked {number} as fulfilled.",
    CANCELLED: "Cancelled {number}.",
}


def clean_note(value):
    """Trim a note and use plain newlines, so the length matches what the textarea counted."""
    if not isinstance(value, str):
        return value
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


NOTE_LENGTH = Length(max=NOTE_MAX_LENGTH, message=f"Keep this to {NOTE_MAX_LENGTH:,} characters or fewer.")


class StatusChangeForm(FlaskForm):
    """Approve, mark as fulfilled or cancel, with an optional note for the client."""

    to_status = HiddenField()
    message = TextAreaField("Note for the client (optional)", filters=[clean_note], validators=[NOTE_LENGTH])


class RejectForm(StatusChangeForm):
    message = TextAreaField(
        "Reason for rejecting",
        filters=[clean_note],
        validators=[
            DataRequired(message="Add a reason so the client knows why the order was rejected."),
            NOTE_LENGTH,
        ],
    )


def note_form_for(allowed):
    """The blank form with a text box shown on the page: reject a pending order, or cancel an approved one."""
    if REJECTED in allowed:
        form = RejectForm(formdata=None)
        form.to_status.data = REJECTED
    elif CANCELLED in allowed:
        form = StatusChangeForm(formdata=None)
        form.to_status.data = CANCELLED
    else:
        return None
    return form


def get_order_or_404(number, *options, lock=False):
    """Load one of the current company's orders by its number, or 404."""
    if len(number) > NUMBER_MAX_LENGTH or "\x00" in number:
        # Can't be an order number; Postgres would also refuse a NUL in the query
        abort(404)
    stmt = select(Order).where(Order.company_id == current_user.company_id, Order.number == number)
    if options:
        stmt = stmt.options(*options)
    if lock:
        # Two people clicking at once can't both change the order (Postgres row lock)
        stmt = stmt.with_for_update()
    order = db.session.scalar(stmt)
    if order is None:
        abort(404)
    return order


def not_allowed_message(order, to_status):
    if to_status == order.status:
        return f"{order.number} is already {STATUS_LABELS[order.status].lower()}, so nothing changed."
    if order.status == PENDING and to_status == FULFILLED:
        return f"{order.number} hasn't been approved yet. Approve it before marking it as fulfilled."
    if order.status == PENDING and to_status == CANCELLED:
        return f"{order.number} is still pending review. To turn it down, reject it with a reason for the client."
    current = STATUS_LABELS[order.status].lower()
    return f"{order.number} is {current}, so it can't be {CHANGE_VERBS[to_status]}."


def render_order(order, note_form=None):
    allowed = allowed_transitions(order, current_user)
    if note_form is None:
        note_form = note_form_for(allowed)
    cancel_event = next((event for event in reversed(order.events) if event.to_status == CANCELLED), None)
    return render_template(
        "admin/order_detail.html",
        order=order,
        client=order.client,
        allowed=allowed,
        note_form=note_form,
        cancel_event=cancel_event,
        line_count=len(order.items),
        transition_url=url_for("admin.order_transition", number=order.number),
    )


@bp.route("/orders/<number>")
@staff_required
def order_detail(number):
    order = get_order_or_404(
        number,
        joinedload(Order.client),
        joinedload(Order.decided_by),
        joinedload(Order.fulfilled_by),
        selectinload(Order.items),
        selectinload(Order.events).joinedload(OrderEvent.actor),
    )
    return render_order(order)


@bp.route("/orders/<number>/status", methods=["POST"])
@staff_required
def order_transition(number):
    order = get_order_or_404(number, lock=True)
    detail_url = url_for("admin.order_detail", number=order.number)

    to_status = (request.form.get("to_status") or "").strip().lower()
    if to_status not in ORDER_STATUSES:
        flash("We didn't recognise that change. Use the buttons on this page to update the order.", "danger")
        return redirect(detail_url)

    allowed = allowed_transitions(order, current_user)
    if to_status not in allowed:
        tone = "info" if to_status == order.status else "warning"
        flash(not_allowed_message(order, to_status), tone)
        return redirect(detail_url)

    form = RejectForm() if to_status == REJECTED else StatusChangeForm()
    if not form.validate_on_submit():
        shown = note_form_for(allowed)
        if shown is not None and shown.to_status.data == to_status:
            # Show the page again with what they typed and the problem next to it
            form.to_status.data = to_status
            return render_order(order, note_form=form)
        flash(next(iter(form.errors.values()))[0], "danger")
        return redirect(detail_url)

    try:
        transition_order(order, current_user, to_status, form.message.data)
    except OrderError as error:
        db.session.rollback()
        flash(str(error), "danger")
        return redirect(detail_url)

    flash(SUCCESS_MESSAGES[to_status].format(number=order.number), "success")
    return redirect(detail_url)


@bp.route("/orders/<number>/invoice")
@staff_required
def invoice(number):
    order = get_order_or_404(number, joinedload(Order.client), selectinload(Order.items))
    return render_template(
        "shared/invoice.html",
        order=order,
        back_url=url_for("admin.order_detail", number=order.number),
    )


# CSV export ------------------------------------------------------------------------

CSV_COLUMNS = (
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
)
# Spreadsheets run a cell that starts with one of these as a formula
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
EXPORT_BATCH_ROWS = 500
EXPORT_FLUSH_BYTES = 64 * 1024


def csv_cell(value):
    """Text for one cell, made inert if a spreadsheet would treat it as a formula."""
    text = "" if value is None else str(value)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def rupees(paise):
    """12345 -> "123.45": plain decimal rupees for spreadsheets (no symbol or separators)."""
    paise = int(paise or 0)
    whole, rem = divmod(abs(paise), 100)
    return f"{'-' if paise < 0 else ''}{whole}.{rem:02d}"


def local_iso(value, tz_name):
    """A stored UTC time as ISO 8601 in the company time zone, e.g. 2026-10-31T23:30:00+05:30."""
    local = to_local(value, tz_name)
    return local.isoformat(timespec="seconds") if local else ""


def export_rows(company, filters):
    """The orders matching the filters as CSV rows (header first), read from the database in batches."""
    tz_name = company.timezone
    lines = (
        select(func.count(OrderItem.id))
        .where(OrderItem.order_id == Order.id)
        .correlate(Order)
        .scalar_subquery()
        .label("lines")
    )
    stmt = apply_filters(
        select(
            Order.number,
            Order.placed_at,
            Order.status,
            User.first_name,
            User.last_name,
            User.buyer_company,
            User.email,
            lines,
            Order.subtotal_paise,
            Order.tax_paise,
            Order.total_paise,
            Order.decided_at,
            Order.fulfilled_at,
        ),
        company,
        filters,
    )
    return _export_rows(stmt.execution_options(yield_per=EXPORT_BATCH_ROWS), tz_name)


def _export_rows(stmt, tz_name):
    yield CSV_COLUMNS
    for row in db.session.execute(stmt):
        name = " ".join(part for part in (row.first_name, row.last_name) if part)
        yield tuple(
            csv_cell(value)
            for value in (
                row.number,
                local_iso(row.placed_at, tz_name),
                STATUS_LABELS.get(row.status, row.status),
                name,
                row.buyer_company,
                row.email,
                row.lines,
                rupees(row.subtotal_paise),
                rupees(row.tax_paise),
                rupees(row.total_paise),
                local_iso(row.decided_at, tz_name),
                local_iso(row.fulfilled_at, tz_name),
            )
        )


def stream_csv(rows):
    """Encode rows as CSV text in chunks, starting with a BOM so Excel reads the file as UTF-8."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    buffer.write("\ufeff")
    for row in rows:
        writer.writerow(row)
        if buffer.tell() >= EXPORT_FLUSH_BYTES:
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
    yield buffer.getvalue()


@bp.route("/orders/export.csv")
@staff_required
def orders_export():
    company = current_user.company
    filters = parse_order_filters(request.args)
    today = to_local(utcnow(), company.timezone).date()
    return Response(
        stream_with_context(stream_csv(export_rows(company, filters))),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="orders-{today.isoformat()}.csv"'},
    )
