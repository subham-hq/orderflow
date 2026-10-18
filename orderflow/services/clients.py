"""Client and teammate accounts: status, list filters, suspension and removal.

An account is Invited until the person accepts their invitation and sets a
password, Active after that, and Suspended whenever staff turn it off.
Suspending ends every session at once (User.end_all_sessions), so a
suspended person loses access on their very next request.

Invitations go to addresses staff type in, so each workspace can send only
INVITATIONS_PER_DAY of them, and the names they carry (company, inviter,
invitee) must be plain text without web addresses.

Changing the email someone signs in with takes two steps: a link is sent to
the new address, and the change happens when the person opens it while
signed in to their account and confirms. Staff can ask for the change on a
client's behalf, but only the client can confirm it, so staff can't take an
account over by pointing it at an address they control.
"""

import logging
import re
from datetime import UTC, datetime, timedelta

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from limits import parse as parse_rate_limit
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..extensions import db, limiter
from ..mail import external_url, send_email
from ..models import CLIENT, REVENUE_STATUSES, Order, User
from ..timeutil import as_utc, format_datetime, utcnow
from ..tokens import TokenError
from .accounts import find_user_by_email, normalize_email, send_invite_email

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
            "Remove it first, so emails from your workspace can't be mistaken for spam."
        )
    if inviter is not None and has_link_or_markup(inviter.full_name):
        return (
            f"The inviter's name ({inviter.full_name}) includes a web address or the symbols < and >. "
            "Change it in their profile first."
        )
    if invitee is not None and any(has_link_or_markup(text) for text in (invitee.first_name, invitee.buyer_company)):
        return (
            f"{display_name(invitee)} has a web address or the symbols < and > in their name or business name. "
            "Edit their details first."
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


# Changing the email someone signs in with ------------------------------------------

_EMAIL_CHANGE_SALT = "orderflow:change-email"


def _email_change_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=_EMAIL_CHANGE_SALT)


def make_email_change_token(user, new_email):
    """A token for switching user to new_email. Like the other emailed links it
    stops working once the user's email or password changes."""
    payload = {"uid": user.id, "v": user.auth_version, "e": user.email, "new": normalize_email(new_email)}
    return _email_change_serializer().dumps(payload)


def load_email_change_token(token):
    """Return (user, new email) for a token, or raise TokenError."""
    max_age = current_app.config["VERIFY_TOKEN_MAX_AGE"]
    try:
        payload = _email_change_serializer().loads(token, max_age=int(max_age.total_seconds()))
    except SignatureExpired:
        raise TokenError("This link has expired.", expired=True) from None
    except BadSignature:
        raise TokenError("This link is not valid.") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("uid"), int) or not payload.get("new"):
        raise TokenError("This link is not valid.")
    user = db.session.get(User, payload["uid"])
    if user is None or user.email != payload.get("e") or user.auth_version != payload.get("v"):
        raise TokenError("This link has already been used or is no longer valid.")
    return user, payload["new"]


def send_email_change_link(user, new_email, requested_by):
    """Email new_email a link that moves user's sign-in to it. Returns True if it was sent."""
    return send_email(
        new_email,
        "Confirm your new email for OrderFlow",
        "email_change",
        user=user,
        requested_by_self=requested_by.id == user.id,
        link=external_url("admin.confirm_email_change", token=make_email_change_token(user, new_email)),
    )


def mask_email(email):
    """ravi@stores.example -> r***@stores.example, for telling an old address where the account went."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def _taken_since(new_email):
    return ClientError(
        f"{new_email} is now used by another OrderFlow account, so we can't switch to it. "
        "Ask for a new link with a different address."
    )


def email_change_problem(user, new_email):
    """Why user can't switch to new_email now (someone else has it), or None."""
    existing = find_user_by_email(new_email)
    if existing is not None and existing.id != user.id:
        return str(_taken_since(normalize_email(new_email)))
    return None


def change_email(user, new_email):
    """Move user's sign-in to new_email (they proved they can read it). Returns
    the old address. Raises ClientError if someone else has it now."""
    new_email = normalize_email(new_email)
    taken = _taken_since(new_email)
    if email_change_problem(user, new_email):
        raise taken
    old_email = user.email
    user.email = new_email
    user.email_verified_at = utcnow()
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()  # registered a moment ago
        raise taken from None
    return old_email


def send_email_changed_notice(user, old_email):
    """Tell the old address where the account went, in case the change wasn't wanted."""
    return send_email(
        old_email,
        "Your OrderFlow sign-in email has changed",
        "email_changed",
        user=user,
        new_email_hint=mask_email(user.email),
        contact_email=current_app.config.get("CONTACT_EMAIL"),
    )


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
