"""Small helpers shared by the portal views."""

from flask import abort, request
from flask_login import current_user

from ..extensions import db
from ..models import Order

# Highest page number accepted from a URL; anything larger is clamped
MAX_PAGE = 10_000
# Longest search text used in a query
MAX_SEARCH_LENGTH = 100


def get_client_order_or_404(number, for_update=False):
    """The signed-in client's own order with this number, or a 404.

    Filters by company, client and number together, so another client's or
    another company's order is indistinguishable from one that doesn't exist.
    for_update locks the row (on Postgres) until the request commits, so two
    status changes sent at once are applied one after the other.
    """
    if not number or len(number) > 32:
        abort(404)
    select = db.select(Order).where(
        Order.company_id == current_user.company_id,
        Order.client_id == current_user.id,
        Order.number == number,
    )
    if for_update:
        select = select.with_for_update().execution_options(populate_existing=True)
    order = db.session.scalar(select)
    if order is None:
        abort(404)
    return order


def page_arg():
    """The ?page= number, defaulting to 1 and clamped to a sane range."""
    page = request.args.get("page", type=int) or 1
    return min(max(page, 1), MAX_PAGE)


def search_arg(name="q", max_length=MAX_SEARCH_LENGTH):
    """A trimmed search string from the query string ("" when absent)."""
    value = request.args.get(name, "", type=str).strip()
    return value[:max_length]


def like_pattern(text):
    """A LIKE pattern matching text anywhere, with % and _ in the text taken literally."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def paginate(select, page, per_page):
    """Paginate a select; a page past the end shows the last page instead of nothing."""
    result = db.paginate(select, page=page, per_page=per_page, error_out=False)
    if result.pages and page > result.pages:
        result = db.paginate(select, page=result.pages, per_page=per_page, error_out=False)
    return result
