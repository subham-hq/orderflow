"""Company settings and each person's own profile."""

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..permissions import staff_required
from . import bp
from .forms import ProfileForm


def _apply(obj, changes):
    """Copy values onto obj; return the names that changed."""
    changed = []
    for name, value in changes.items():
        if getattr(obj, name) != value:
            setattr(obj, name, value)
            changed.append(name)
    return changed


@bp.route("/settings")
@staff_required
def settings():
    return render_template("placeholder.html", title="Settings", layout="layouts/app.html")


@bp.route("/settings/profile", methods=["GET", "POST"])
@staff_required
def profile():
    user = current_user._get_current_object()
    form = ProfileForm(obj=user if request.method == "GET" else None)
    if form.validate_on_submit():
        changes = {
            "first_name": form.first_name.data,
            "last_name": form.last_name.data or None,
            "phone": form.phone.data or None,
        }
        if _apply(user, changes):
            db.session.commit()
            flash("Saved your profile.", "success")
        else:
            flash("No changes to save.", "info")
        return redirect(url_for("admin.profile"))
    return render_template("admin/settings_profile.html", form=form)
