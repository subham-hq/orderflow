"""Signed, expiring tokens for links sent by email.

Each token is tied to a purpose and to the user's current auth_version, so
a password-reset or invite link stops working once it has been used (using
it bumps auth_version) or once the user changes their password.
"""

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .extensions import db
from .models import User

VERIFY_EMAIL = "verify-email"
RESET_PASSWORD = "reset-password"
ACCEPT_INVITE = "accept-invite"

_MAX_AGE_SETTING = {
    VERIFY_EMAIL: "VERIFY_TOKEN_MAX_AGE",
    RESET_PASSWORD: "RESET_TOKEN_MAX_AGE",
    ACCEPT_INVITE: "INVITE_TOKEN_MAX_AGE",
}


def _serializer(purpose):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=f"orderflow:{purpose}")


def make_token(purpose, user):
    payload = {"uid": user.id, "v": user.auth_version, "e": user.email}
    return _serializer(purpose).dumps(payload)


class TokenError(Exception):
    def __init__(self, message, expired=False):
        super().__init__(message)
        self.expired = expired


def load_token(purpose, token):
    """Return the User a token was made for, or raise TokenError."""
    max_age = current_app.config[_MAX_AGE_SETTING[purpose]]
    try:
        payload = _serializer(purpose).loads(token, max_age=int(max_age.total_seconds()))
    except SignatureExpired:
        raise TokenError("This link has expired.", expired=True) from None
    except BadSignature:
        raise TokenError("This link is not valid.") from None

    if not isinstance(payload, dict):
        raise TokenError("This link is not valid.")
    user = db.session.get(User, payload.get("uid"))
    if user is None or user.email != payload.get("e") or user.auth_version != payload.get("v"):
        raise TokenError("This link has already been used or is no longer valid.")
    return user
