"""Client and teammate accounts: status, list filters, suspension and removal.

An account is Invited until the person accepts their invitation and sets a
password, Active after that, and Suspended whenever staff turn it off.
Suspending ends every session at once (User.end_all_sessions), so a
suspended person loses access on their very next request.

Invitations go to addresses staff type in, so each workspace can send only
INVITATIONS_PER_DAY of them, and the names they carry (company, inviter,
invitee) must be plain text without web addresses.
"""

import logging
import re
from datetime import UTC, datetime, timedelta

from limits import parse as parse_rate_limit
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..extensions import db, limiter
from ..models import CLIENT, REVENUE_STATUSES, Order, User
from ..timeutil import as_utc, format_datetime, utcnow
from .accounts import send_invite_email

log = logging.getLogger(__name__)

ACTIVE = "active"
INVITED = "invited"
SUSPENDED = "suspended"
ACCOUNT_STATUSES = (ACTIVE, INVITED, SUSPENDED)

# A second "Resend invitation" within this window doesn't send another email
INVITE_RESEND_COOLDOWN = timedelta(minutes=1)

# Emails a workspace may send to addresses its staff type in (invitations,
# resent invitations and email-change links), per day
INVITATIONS_PER_DAY = 30
_DAILY_INVITATIONS = parse_rate_limit(f"{INVITATIONS_PER_DAY} per day")

# Text a mail app would show as a link: a scheme (https://), www., or a
# dotted name ending in a common top-level domain (pay-fees.com, x.co.in).
_LINK_TLDS = (
    "com|net|org|info|biz|in|co|io|ai|app|dev|xyz|online|site|website|shop|store|top|link|click|live|ly|gl|"
    "ru|cn|tk|ml|ga|cf|gq|cc|ws|pw|icu|buzz|club|vip|help|support|pay|money|bank|finance|tech|space|page|"
    "email|digital|cloud|pro|uk|eu|us"
)
_WEB_ADDRESS = re.compile(
    rf"[a-z][a-z0-9+.-]*://|\bwww\.|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:{_LINK_TLDS})\b", re.IGNORECASE
)


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
    """A client's name with their business, like Ananya Rao (Sunrise Retail LLP); staff get their full name."""
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


# Invitation limits ------------------------------------------------------------


def has_link_or_markup(text):
    """True for text that could carry a link or markup into an email."""
    return bool(text) and ("<" in text or ">" in text or _WEB_ADDRESS.search(text) is not None)


def invitation_text_problem(company, inviter=None, invitee=None):
    """Why an invitation from this company and inviter (to this invitee) can't be
    sent because a name in it looks like a web address, or None."""
    if has_link_or_markup(company.name):
        return (
            "Your company name in Settings includes a web address or the symbols < and >. "
            "Remove it before sending invitations, so they can't be mistaken for spam."
        )
    if inviter is not None and has_link_or_markup(inviter.full_name):
        return (
            f"The inviter's name ({inviter.full_name}) includes a web address or the symbols < and >. "
            "Change it in their profile before sending invitations."
        )
    if invitee is not None and any(has_link_or_markup(text) for text in (invitee.first_name, invitee.buyer_company)):
        return (
            f"{display_name(invitee)} has a web address or the symbols < and > in their name or business name. "
            "Edit their details before sending the invitation."
        )
    return None


def _allowance_key(company_id):
    return ("invitation-emails", f"company:{company_id}")


def invitations_allowed(company_id):
    """True while the company may still send invitation emails today (always when rate limits are off)."""
    if not limiter.enabled:
        return True
    try:
        return limiter.limiter.test(_DAILY_INVITATIONS, *_allowance_key(company_id))
    except Exception:  # the limit store is down: don't block invitations
        log.exception("Couldn't check the invitation allowance for company %s", company_id)
        return True


def count_invitation(company_id):
    """Count one email sent against the company's daily allowance."""
    if not limiter.enabled:
        return
    try:
        limiter.limiter.hit(_DAILY_INVITATIONS, *_allowance_key(company_id))
    except Exception:
        log.exception("Couldn't count an invitation for company %s", company_id)


def invitation_limit_message(company):
    """What to tell staff once the daily allowance is used up."""
    message = f"Your workspace has sent its {_DAILY_INVITATIONS.amount:,} invitation emails for today."
    try:
        reset = limiter.limiter.get_window_stats(_DAILY_INVITATIONS, *_allowance_key(company.id)).reset_time
    except Exception:
        return message + " You can send more tomorrow."
    when = format_datetime(datetime.fromtimestamp(reset, UTC), company.timezone)
    return message + f" You can send more after {when}."


def invitation_blocked(company, inviter=None, invitee=None):
    """Why an invitation email can't go out now (a link in a name it would
    carry, or today's allowance used up), or None."""
    problem = invitation_text_problem(company, inviter, invitee)
    if problem is None and not invitations_allowed(company.id):
        problem = invitation_limit_message(company)
    return problem


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
    problem = invitation_blocked(user.company, user.invited_by, user)
    if problem:
        return "warning", problem
    if send_invite_email(user):
        db.session.commit()
        count_invitation(user.company_id)
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
