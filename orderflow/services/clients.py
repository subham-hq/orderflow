"""Client and teammate accounts: status, list filters, suspension and removal.

An account is Invited until the person accepts their invitation and sets a
password, Active after that, and Suspended whenever staff turn it off.
Suspending ends every session at once (User.end_all_sessions), so a
suspended person loses access on their very next request.
"""

from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import CLIENT, REVENUE_STATUSES, Order, User
from ..timeutil import as_utc, utcnow
from .accounts import send_invite_email

ACTIVE = "active"
INVITED = "invited"
SUSPENDED = "suspended"
ACCOUNT_STATUSES = (ACTIVE, INVITED, SUSPENDED)

# A second "Resend invitation" within this window doesn't send another email
INVITE_RESEND_COOLDOWN = timedelta(minutes=1)


class ClientError(ValueError):
    """A request that can't be carried out, with a message safe to show."""


def account_status(user):
    if not user.active:
        return SUSPENDED
    if not user.has_accepted_invite:
        return INVITED
    return ACTIVE


def where_status(stmt, status):
    """Narrow a User select to one account status."""
    if status == ACTIVE:
        return stmt.where(User.active.is_(True), User.password_hash.is_not(None))
    if status == INVITED:
        return stmt.where(User.active.is_(True), User.password_hash.is_(None))
    if status == SUSPENDED:
        return stmt.where(User.active.is_(False))
    return stmt


def status_counts(stmt):
    """{"": total, "active": n, "invited": n, "suspended": n} for a User select."""
    accepted = User.password_hash.is_not(None)
    counts_stmt = stmt.with_only_columns(User.active, accepted.label("accepted"), func.count(User.id)).group_by(
        User.active, accepted
    )
    counts = dict.fromkeys(("", *ACCOUNT_STATUSES), 0)
    for active, has_password, count in db.session.execute(counts_stmt):
        if not active:
            counts[SUSPENDED] += count
        elif has_password:
            counts[ACTIVE] += count
        else:
            counts[INVITED] += count
        counts[""] += count
    return counts


def display_name(user):
    """ "Ananya Rao (Sunrise Retail LLP)" for clients, the full name otherwise."""
    if user.is_client and user.buyer_company:
        return f"{user.full_name} ({user.buyer_company})"
    return user.full_name


def order_counts(company_id, user_ids):
    """{client id: number of orders} for the given clients."""
    if not user_ids:
        return {}
    rows = db.session.execute(
        db.select(Order.client_id, func.count(Order.id))
        .where(Order.company_id == company_id, Order.client_id.in_(list(user_ids)))
        .group_by(Order.client_id)
    )
    return dict(rows.all())


def order_count(user):
    return order_counts(user.company_id, [user.id]).get(user.id, 0)


def ordered_total_paise(user):
    """What the client has bought: approved and fulfilled orders."""
    total = db.session.scalar(
        db.select(func.coalesce(func.sum(Order.total_paise), 0)).where(
            Order.company_id == user.company_id,
            Order.client_id == user.id,
            Order.status.in_(REVENUE_STATUSES),
        )
    )
    return int(total or 0)


def recent_orders(user, limit=10):
    return db.session.scalars(
        db.select(Order)
        .where(Order.company_id == user.company_id, Order.client_id == user.id)
        .order_by(Order.placed_at.desc(), Order.id.desc())
        .limit(limit)
    ).all()


def set_active(user, active):
    """Suspend (active=False) or reactivate an account. Returns True if it changed.

    Suspending also ends every session the person has open.
    """
    if bool(user.active) == active:
        return False
    user.active = active
    if not active:
        user.end_all_sessions()
    return True


def invite_recently_sent(user, now=None):
    if user.invite_sent_at is None:
        return False
    now = now or utcnow()
    return now - as_utc(user.invite_sent_at) < INVITE_RESEND_COOLDOWN


def resend_invitation(user):
    """Email a fresh invitation when it makes sense. Returns (flash category, message)."""
    if user.has_accepted_invite:
        return "info", f"{user.full_name} has already accepted their invitation and can sign in."
    if not user.active:
        return "warning", f"{user.full_name} is suspended. Reactivate them before sending a new invitation."
    if invite_recently_sent(user):
        return "info", (
            f"We sent an invitation to {user.email} less than a minute ago. Give it a few minutes to arrive, "
            "and ask them to check their spam folder."
        )
    if send_invite_email(user):
        db.session.commit()
        return "success", f"Sent a new invitation to {user.email}."
    db.session.rollback()
    return "danger", f"We couldn't send the invitation to {user.email} just now. Please try again in a few minutes."


def delete_client(user):
    """Delete a client who has never placed an order. Raises ClientError otherwise."""
    if user.role != CLIENT:
        raise ClientError("Only client accounts can be deleted here.")
    if order_count(user):
        raise ClientError(
            f"{display_name(user)} has placed orders, so their account can't be deleted. "
            "Suspend it instead: they won't be able to sign in, and their orders stay on record."
        )
    db.session.delete(user)
    try:
        db.session.commit()
    except IntegrityError:
        # An order arrived between the check and the delete
        db.session.rollback()
        raise ClientError(
            f"{display_name(user)} has just placed an order, so their account can't be deleted. Suspend it instead."
        ) from None
