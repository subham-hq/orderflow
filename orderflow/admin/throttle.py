"""Rate limits for workspace actions that send email to an address staff type in.

Each limited view is counted on its own, per signed-in person. The daily
allowance shared by the whole workspace is in services.clients.
"""

from flask_limiter.util import get_remote_address
from flask_login import current_user

from ..extensions import limiter


def _person():
    # Limits are checked before the view's sign-in check runs, so a visitor
    # who isn't signed in is counted by address instead
    if current_user.is_authenticated:
        return f"user:{current_user.id}"
    return get_remote_address()


def per_person(limit):
    """Allow each signed-in person `limit` POSTs to this view, like "10 per minute"."""
    return limiter.limit(limit, key_func=_person, methods=["POST"])
