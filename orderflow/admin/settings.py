"""Company settings (owner only; admins can look), each person's own profile,
and the link that confirms a new sign-in email (for staff and clients)."""

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import OWNER, User
from ..permissions import company_select, home_url_for, staff_required
from ..services import clients as accounts
from ..services.accounts import email_taken
from ..tokens import TokenError
from . import bp
from .forms import ChangeEmailForm, CompanySettingsForm, ProfileForm, timezone_choices
from .throttle import per_person

EMAIL_TAKEN = "Someone with this email already has an OrderFlow account."

COMPANY_FIELDS = ("name", "legal_name", "gstin", "address", "phone", "support_email", "timezone", "order_prefix")
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
        changes = {name: form[name].data for name in COMPANY_FIELDS}
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


def _render_profile(form=None, email_form=None):
    """The profile page; only the form that was submitted reads the request."""
    user = current_user._get_current_object()
    return render_template(
        "admin/settings_profile.html",
        form=form or ProfileForm(formdata=None, obj=user),
        email_form=email_form or ChangeEmailForm(formdata=None),
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
    return _render_profile(form=form)


@bp.route("/settings/email", methods=["POST"])
@staff_required
@per_person("5 per minute;20 per hour")
def change_email():
    """Send a link to a new address; the sign-in email changes once it's opened."""
    user = current_user._get_current_object()
    form = ChangeEmailForm()
    if form.validate_on_submit():
        new_email = form.new_email.data
        if not user.check_password(form.current_password.data):
            form.current_password.errors.append("That isn't your password. Check it and try again.")
        elif new_email == user.email:
            form.new_email.errors.append("That's already the email you sign in with.")
        elif email_taken(new_email):
            form.new_email.errors.append(EMAIL_TAKEN)
        elif accounts.has_link_or_markup(user.first_name):
            form.form_errors.append(
                "Your first name includes a web address or the symbols < and >. Change it in your profile first."
            )
        elif problem := accounts.invitation_blocked(user.company):
            form.form_errors.append(problem)
        elif accounts.send_email_change_link(user, new_email, requested_by=user):
            accounts.count_invitation(user.company_id)
            flash(
                f"Check {new_email} for a link to confirm it. Your sign-in email changes once you open the link; "
                f"until then, keep signing in with {user.email}.",
                "success",
            )
            return redirect(url_for("admin.profile"))
        else:
            form.form_errors.append(
                f"We couldn't send the email to {new_email} just now. Please try again in a few minutes."
            )
    return _render_profile(email_form=form)


@bp.route("/email/confirm/<token>")
def confirm_email_change(token):
    """Switch the sign-in email to the address the link was sent to.

    The person must be signed in to the account the link is for, so a link
    sent to an address someone else controls can't move the account there.
    """
    try:
        user, new_email = accounts.load_email_change_token(token)
    except TokenError as error:
        return _link_invalid(f"{error} {_ask_again()}", 400)

    if not current_user.is_authenticated:
        flash("Sign in with your current email and password to confirm your new email address.", "info")
        return redirect(url_for("auth.login", next=request.path))
    if current_user.id != user.id:
        return _link_invalid(
            "This link is for a different account. Sign out, then open the link again and sign in "
            "with the account it was sent for.",
            403,
        )
    try:
        old_email = accounts.change_email(user, new_email)
    except accounts.ClientError as error:
        return _link_invalid(str(error), 409)
    accounts.send_email_changed_notice(user, old_email)
    flash(f"Your sign-in email is now {user.email}. Use it the next time you sign in.", "success")
    return redirect(home_url_for(user))


def _ask_again():
    if current_user.is_authenticated and current_user.is_client:
        return f"Ask {current_user.company.name} to send a new one."
    if current_user.is_authenticated:
        return "You can ask for a new one from your profile."
    return "Ask for a new one."


def _link_invalid(message, status):
    if current_user.is_authenticated:
        action_url, action_label = home_url_for(current_user), "Go to your account"
    else:
        action_url, action_label = url_for("auth.login"), "Go to sign in"
    return render_template(
        "auth/link_invalid.html", message=message, action_url=action_url, action_label=action_label
    ), status
