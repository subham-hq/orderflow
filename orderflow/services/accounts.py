"""Companies, users, sign-up and invitations."""

from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..mail import external_url, send_email
from ..models import ADMIN, CLIENT, OWNER, Bulletin, Company, Order, Product, User
from ..timeutil import as_utc, utcnow
from ..tokens import ACCEPT_INVITE, VERIFY_EMAIL, make_token

MIN_PASSWORD_LENGTH = 10

# An unconfirmed sign-up older than this no longer holds its email address
ABANDONED_SIGNUP_AGE = timedelta(hours=24)

# Passwords people reach for first; refused outright
COMMON_PASSWORDS = {
    "password",
    "password1",
    "password12",
    "password123",
    "passw0rd",
    "qwerty",
    "qwerty123",
    "qwertyuiop",
    "123456",
    "1234567",
    "12345678",
    "123456789",
    "1234567890",
    "0987654321",
    "111111",
    "000000",
    "abc123",
    "abcdef",
    "iloveyou",
    "admin",
    "admin123",
    "welcome",
    "welcome1",
    "welcome123",
    "letmein",
    "monkey",
    "dragon",
    "football",
    "baseball",
    "sunshine",
    "princess",
    "orderflow",
    "orderflow1",
    "orderflow123",
    "changeme",
}


class AccountError(ValueError):
    """A problem the user can fix, with a message safe to show them."""


# Shown when someone signs up with an address that already has an account.
# Forgot password works for everyone: it sends a reset link, or a fresh
# invitation to someone who was invited but never set a password.
EMAIL_EXISTS = (
    "An account with this email already exists. Sign in, or use Forgot password if you don't know the password."
)


def normalize_email(value):
    return (value or "").strip().lower()


def clean(value):
    """Trim a text value; empty becomes None."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def password_problem(password, email=None):
    """Return a message describing why a password is too weak, or None."""
    password = password or ""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > 128:
        return "Use 128 characters or fewer."
    if password.lower() in COMMON_PASSWORDS or len(set(password)) < 4:
        return "That password is too easy to guess. Try a short phrase instead."
    if email and normalize_email(email).split("@")[0] in password.lower() and len(password) < 16:
        return "Don't base your password on your email address."
    return None


def find_user_by_email(email):
    email = normalize_email(email)
    if not email:
        return None
    return db.session.scalar(db.select(User).where(func.lower(User.email) == email))


def email_taken(email):
    return find_user_by_email(email) is not None


def _is_abandoned_signup(user):
    """An owner who signed up over a day ago, never confirmed the email, and has an empty workspace."""
    if user.role != OWNER or user.is_verified:
        return False
    if as_utc(user.created_at) > utcnow() - ABANDONED_SIGNUP_AGE:
        return False
    for model in (Product, Order, Bulletin):
        if db.session.scalar(db.select(model.id).where(model.company_id == user.company_id).limit(1)) is not None:
            return False
    teammate = db.session.scalar(
        db.select(User.id).where(User.company_id == user.company_id, User.id != user.id).limit(1)
    )
    return teammate is None


def release_abandoned_signup(email):
    """Free an email address held by a sign-up nobody ever confirmed.

    Anyone can sign up with an address that isn't theirs. Since they can't
    confirm it, that sign-up stops holding the address after a day: the next
    sign-up or invitation for it removes the empty workspace and carries on.
    Returns True if one was removed. The caller commits.
    """
    user = find_user_by_email(email)
    if user is None or not _is_abandoned_signup(user):
        return False
    company_id = user.company_id
    # Still unconfirmed in the WHERE, in case they confirm at this very moment
    deleted = db.session.execute(db.delete(User).where(User.id == user.id, User.email_verified_at.is_(None))).rowcount
    if not deleted:
        return False
    db.session.execute(db.delete(Company).where(Company.id == company_id))
    return True


def create_company_with_owner(company_name, first_name, last_name, email, password):
    """Create a new business account and its owner (unverified)."""
    email = normalize_email(email)
    problem = password_problem(password, email)
    if problem:
        raise AccountError(problem)
    release_abandoned_signup(email)
    if email_taken(email):
        raise AccountError(EMAIL_EXISTS)

    company = Company(name=clean(company_name))
    owner = User(
        company=company,
        email=email,
        first_name=clean(first_name),
        last_name=clean(last_name),
        role=OWNER,
    )
    owner.set_password(password)
    db.session.add_all([company, owner])
    try:
        db.session.commit()
    except IntegrityError:
        # The same email signed up a moment ago in another request (a double
        # submit or a second tab). The address is the only unique value here.
        db.session.rollback()
        raise AccountError(EMAIL_EXISTS) from None
    return owner


def send_verification_email(user):
    token = make_token(VERIFY_EMAIL, user)
    return send_email(
        user.email,
        f"Confirm your email for {user.company.name}",
        "verify_email",
        user=user,
        link=external_url("auth.verify_email", token=token),
    )


def mark_verified(user):
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()


def invite_user(company, inviter, email, role, first_name, last_name=None, buyer_company=None, phone=None):
    """Create an invited user (no password yet) and email them a link to join.

    Returns (user, sent): sent is False when the invitation email couldn't be
    handed to the mail service. The account is created either way, so the
    invitation can be sent again later.
    """
    if role not in (ADMIN, CLIENT):
        raise AccountError("Choose a valid role.")
    email = normalize_email(email)
    release_abandoned_signup(email)
    if email_taken(email):
        raise AccountError("Someone with this email already has an OrderFlow account.")

    user = User(
        company=company,
        email=email,
        first_name=clean(first_name),
        last_name=clean(last_name),
        buyer_company=clean(buyer_company),
        phone=clean(phone),
        role=role,
        invited_by=inviter,
    )
    db.session.add(user)
    db.session.flush()
    sent = send_invite_email(user)
    db.session.commit()
    return user, sent


def send_invite_email(user):
    token = make_token(ACCEPT_INVITE, user)
    inviter_name = user.invited_by.full_name if user.invited_by else user.company.name
    if user.role == CLIENT:
        subject = f"{user.company.name} invited you to order on OrderFlow"
    else:
        subject = f"{inviter_name} invited you to join {user.company.name} on OrderFlow"
    sent = send_email(
        user.email,
        subject,
        "invite",
        user=user,
        inviter_name=inviter_name,
        link=external_url("auth.accept_invite", token=token),
    )
    if sent:
        user.invite_sent_at = utcnow()
    return sent


def accept_invite(user, password):
    problem = password_problem(password, user.email)
    if problem:
        raise AccountError(problem)
    user.set_password(password)
    mark_verified(user)
    user.end_all_sessions()  # makes the invite link single-use
    db.session.commit()
    return user


def staff_emails(company):
    """Active, verified staff who should hear about new orders."""
    rows = db.session.scalars(
        db.select(User).where(
            User.company_id == company.id,
            User.role.in_((OWNER, ADMIN)),
            User.active.is_(True),
            User.email_verified_at.is_not(None),
        )
    )
    return [user.email for user in rows]
