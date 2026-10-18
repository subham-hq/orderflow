"""The client portal: browse the catalog, place and track orders."""

from flask import Blueprint, flash
from flask_login import current_user

from ..money import MAX_QUANTITY
from ..services import cart as cart_service

bp = Blueprint("portal", __name__)


@bp.before_request
def keep_cart_in_sync():
    """Take products the supplier has hidden or deleted out of the cart, and say so.

    Runs before every portal page, so the cart badge and "In cart" counts
    only ever include products the client can still order.
    """
    if current_user.is_authenticated and current_user.is_client and current_user.is_verified:
        message = cart_service.sync_message(cart_service.sync_cart(current_user.company_id))
        if message:
            flash(message, "warning")


@bp.context_processor
def portal_context():
    return {"MAX_QUANTITY": MAX_QUANTITY}


from . import cart, catalog, home, orders, routes  # noqa: E402,F401
