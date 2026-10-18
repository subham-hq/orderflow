"""Company settings (owner only; admins can look) and each person's own profile."""

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import OWNER, User
from ..permissions import company_select, staff_required
from . import bp
from .forms import CompanySettingsForm, ProfileForm, timezone_choices

# Fields that may be left blank (stored as NULL)
_OPTIONAL_COMPANY_FIELDS = ("legal_name", "gstin", "address", "phone", "support_email")


def _next_order_number(company):
    return f"{company.order_prefix or 'ORD'}-{company.next_order_seq or 1:06d}"


def _apply(obj, changes):
    """Copy values onto obj; return the names that changed."""
    changed = []
    for name, value in changes.items():
        if getattr(obj, name) != value:
            setattr(obj, name, value)
            changed.append(name)
    return changed


@bp.route("/settings", methods=["GET", "POST"])
@staff_required
def settings():
    company = current_user.company
    can_edit = current_user.is_owner
    if request.method == "POST" and not can_edit:
        flash("Only the account owner can change company settings.", "warning")
        return redirect(url_for("admin.settings"))

    form = CompanySettingsForm(obj=company if request.method == "GET" else None)
    form.timezone.choices = timezone_choices(company.timezone)
    if can_edit and form.validate_on_submit():
        changes = {name: field.data for name, field in form._fields.items() if name != "csrf_token"}
        for name in _OPTIONAL_COMPANY_FIELDS:
            changes[name] = changes[name] or None
        changed = _apply(company, changes)
        if not changed:
            flash("No changes to save.", "info")
            return redirect(url_for("admin.settings"))
        db.session.commit()
        message = "Saved your company settings."
        if "order_prefix" in changed:
            message += (
                f" New orders will be numbered like {_next_order_number(company)}; "
                "orders you already have keep their numbers."
            )
        if "timezone" in changed:
            message += f" Dates and times are now shown in {company.timezone} time."
        flash(message, "success")
        return redirect(url_for("admin.settings"))

    return render_template(
        "admin/settings.html",
        form=form,
        company=company,
        can_edit=can_edit,
        next_order_number=_next_order_number(company),
        owners=db.session.scalars(company_select(User).where(User.role == OWNER).order_by(User.id)).all(),
    )


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
