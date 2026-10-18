"""Access control and tenant scoping.

Views use the role decorators below, and load records only through
get_company_object_or_404() / company_select(), which always filter by the
signed-in user's company. A record from another company is a 404, never a
403, so ids from other tenants reveal nothing.
"""

from functools import wraps
from urllib.parse import urljoin, urlsplit

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user

from .extensions import db


def home_url_for(user):
    """Where a signed-in user lands: the admin app for staff, the portal for clients."""
    if user.is_staff:
        return url_for("admin.dashboard")
    return url_for("portal.home")


def _require(check):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
            if not current_user.is_verified:
                return redirect(url_for("auth.verify_pending"))
            if not check(current_user):
                flash("That page isn't available for your account.", "warning")
                return redirect(home_url_for(current_user))
            return view(*args, **kwargs)

        return wrapped

    return decorator


staff_required = _require(lambda user: user.is_staff)
owner_required = _require(lambda user: user.is_owner)
client_required = _require(lambda user: user.is_client)
login_and_verified_required = _require(lambda user: True)


def company_select(model):
    """SELECT model rows belonging to the current user's company."""
    return db.select(model).where(model.company_id == current_user.company_id)


def get_company_object_or_404(model, object_id):
    """Load one record by primary key, only if it belongs to the current company."""
    try:
        object_id = int(object_id)
    except (TypeError, ValueError):
        abort(404)
    obj = db.session.get(model, object_id)
    if obj is None or obj.company_id != current_user.company_id:
        abort(404)
    return obj


def is_safe_next_url(target):
    """True if target is a relative path on this site (prevents open redirects)."""
    if not target:
        return False
    if target.startswith("//") or "\\" in target:
        return False
    host_url = urlsplit(request.host_url)
    joined = urlsplit(urljoin(request.host_url, target))
    return joined.scheme in {"http", "https"} and joined.netloc == host_url.netloc


def safe_next_url(default):
    target = request.values.get("next")
    return target if is_safe_next_url(target) else default
