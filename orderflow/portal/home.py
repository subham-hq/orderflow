"""The client's home page: announcements, a summary of their orders and where to go next."""

from datetime import UTC, datetime

from flask import render_template
from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import APPROVED, PENDING, REVENUE_STATUSES, Bulletin, Order
from ..permissions import client_required
from ..services import cart as cart_service
from ..timeutil import get_zone, utcnow
from . import bp

RECENT_ORDERS = 5


def month_bounds(now, tz_name):
    """The start of this calendar month and of the next, in tz_name, as UTC datetimes."""
    zone = get_zone(tz_name)
    local = now.astimezone(zone)
    start = datetime(local.year, local.month, 1, tzinfo=zone)
    if local.month == 12:
        end = datetime(local.year + 1, 1, 1, tzinfo=zone)
    else:
        end = datetime(local.year, local.month + 1, 1, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def live_bulletins(company_id, now):
    """Published bulletins that haven't expired, newest first."""
    rows = db.session.scalars(
        db.select(Bulletin)
        .where(
            Bulletin.company_id == company_id,
            Bulletin.is_published.is_(True),
            or_(Bulletin.expires_at.is_(None), Bulletin.expires_at > now),
        )
        .order_by(Bulletin.created_at.desc(), Bulletin.id.desc())
    )
    # is_live() compares in UTC as well, whatever the database hands back
    return [bulletin for bulletin in rows if bulletin.is_live(now)]


@bp.route("/")
@client_required
def home():
    company = current_user.company
    now = utcnow()
    mine = [Order.company_id == current_user.company_id, Order.client_id == current_user.id]

    by_status = dict(
        db.session.execute(db.select(Order.status, func.count(Order.id)).where(*mine).group_by(Order.status)).all()
    )
    month_start, next_month_start = month_bounds(now, company.timezone)
    # Orders the supplier has accepted count as spend; pending, rejected and cancelled ones don't
    spent_this_month = db.session.scalar(
        db.select(func.coalesce(func.sum(Order.total_paise), 0)).where(
            *mine,
            Order.status.in_(REVENUE_STATUSES),
            Order.placed_at >= month_start,
            Order.placed_at < next_month_start,
        )
    )
    recent_orders = db.session.scalars(
        db.select(Order)
        .where(*mine)
        .options(selectinload(Order.items))
        .order_by(Order.placed_at.desc(), Order.id.desc())
        .limit(RECENT_ORDERS)
    ).all()

    return render_template(
        "portal/home.html",
        bulletins=live_bulletins(current_user.company_id, now),
        open_orders=by_status.get(PENDING, 0) + by_status.get(APPROVED, 0),
        total_orders=sum(by_status.values()),
        spent_this_month=int(spent_this_month or 0),
        recent_orders=recent_orders,
        cart_items=sum(cart_service.get_cart().values()),
        now=now,
    )
