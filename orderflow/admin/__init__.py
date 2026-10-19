"""The supplier's workspace: orders, catalog, clients, team and settings."""

from collections.abc import Mapping

from flask import Blueprint, has_request_context
from flask_login import current_user

bp = Blueprint("admin", __name__)


class NavBadges(Mapping):
    """Counts shown next to items in the workspace sidebar, like {"orders": 4}.

    Counted the first time a page reads them, so pages (and emails) that don't
    show the sidebar never run the query.
    """

    def __init__(self, company_id):
        self._company_id = company_id
        self._counts = None

    def _load(self):
        if self._counts is None:
            from ..extensions import db
            from ..models import PENDING, Order

            pending = db.session.scalar(
                db.select(db.func.count(Order.id)).where(Order.company_id == self._company_id, Order.status == PENDING)
            )
            self._counts = {"orders": pending or 0}
        return self._counts

    def __getitem__(self, key):
        return self._load()[key]

    def __iter__(self):
        return iter(self._load())

    def __len__(self):
        return len(self._load())


@bp.app_context_processor
def inject_nav_badges():
    """Give every page a staff member sees the sidebar counts, wherever its view lives.

    Most workspace pages are in this blueprint, but some (like Change
    password) belong to others and still use the workspace layout. A view can
    pass its own nav_badges to override these.
    """
    if not has_request_context() or not current_user.is_authenticated or not current_user.is_staff:
        return {}  # emails rendered outside a request have no signed-in user
    return {"nav_badges": NavBadges(current_user.company_id)}


from . import bulletins, catalog, clients, dashboard, orders, settings, team  # noqa: E402,F401
