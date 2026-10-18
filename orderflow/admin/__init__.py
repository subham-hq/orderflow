"""The supplier's workspace: orders, catalog, clients, team and settings."""

from flask import Blueprint
from flask_login import current_user

bp = Blueprint("admin", __name__)


@bp.context_processor
def inject_nav_badges():
    """Show how many orders are waiting for review next to Orders in the sidebar."""
    if not current_user.is_authenticated or not current_user.is_staff:
        return {}
    from ..extensions import db
    from ..models import PENDING, Order

    pending = db.session.scalar(
        db.select(db.func.count(Order.id)).where(Order.company_id == current_user.company_id, Order.status == PENDING)
    )
    return {"nav_badges": {"orders": pending or 0}}


from . import bulletins, catalog, clients, dashboard, orders, settings, team  # noqa: E402,F401
