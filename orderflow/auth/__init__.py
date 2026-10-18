"""Sign up, sign in, email verification, password reset and invitations."""

from flask import Blueprint

bp = Blueprint("auth", __name__)

from . import routes  # noqa: E402,F401
