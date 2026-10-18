"""Client account pages: details, edit, suspend or reactivate, resend invites, delete."""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import CLIENT, User
from ..permissions import get_company_object_or_404, safe_next_url, staff_required
from ..services import clients as client_accounts
from ..services.accounts import email_taken, find_user_by_email, send_invite_email
from . import bp
from .forms import ClientForm

EMAIL_TAKEN = "Someone with this email already has an OrderFlow account."


def _get_client_or_404(user_id):
    """A client of the current company. Staff accounts are managed on the Team page."""
    user = get_company_object_or_404(User, user_id)
    if user.role != CLIENT:
        abort(404)
    return user


def _explicit_active():
    wanted = request.form.get("active")
    if wanted not in ("0", "1"):
        abort(400)
    return wanted == "1"


def _email_problem(email, default):
    """Explain a taken email; point to the client when it's already one of ours."""
    existing = find_user_by_email(email)
    if existing is not None and existing.company_id == current_user.company_id:
        if existing.role == CLIENT:
            return (
                f"{existing.email} is already one of your clients ({client_accounts.display_name(existing)}). "
                "Open their account to resend the invitation or reactivate them."
            )
        return f"{existing.email} belongs to someone on your team."
    return default


def _back_to(user):
    return safe_next_url(url_for("admin.client_detail", user_id=user.id))


# List ------------------------------------------------------------------------


@bp.route("/clients")
@staff_required
def clients():
    return render_template("placeholder.html", title="Clients", layout="layouts/app.html")


# Detail and edit -------------------------------------------------------------


@bp.route("/clients/<int:user_id>")
@staff_required
def client_detail(user_id):
    user = _get_client_or_404(user_id)
    return render_template(
        "admin/client_detail.html",
        client=user,
        status=client_accounts.account_status(user),
        orders=client_accounts.recent_orders(user),
        order_count=client_accounts.order_count(user),
        ordered_total=client_accounts.ordered_total_paise(user),
    )


@bp.route("/clients/<int:user_id>/edit", methods=["GET", "POST"])
@staff_required
def client_edit(user_id):
    user = _get_client_or_404(user_id)
    email_editable = not user.has_accepted_invite
    form = ClientForm(obj=user if request.method == "GET" else None)
    if not email_editable:
        del form.email  # they sign in with it; only they can change it

    if form.validate_on_submit():
        changes = {
            "first_name": form.first_name.data,
            "last_name": form.last_name.data or None,
            "buyer_company": form.buyer_company.data,
            "phone": form.phone.data or None,
        }
        new_email = form.email.data if email_editable else user.email
        if new_email != user.email and email_taken(new_email):
            form.email.errors.append(_email_problem(new_email, EMAIL_TAKEN))
        else:
            return _save_client(user, form, changes, new_email)
    return render_template("admin/client_form.html", form=form, client=user, email_editable=email_editable)


def _save_client(user, form, changes, new_email):
    changed = False
    for name, value in changes.items():
        if getattr(user, name) != value:
            setattr(user, name, value)
            changed = True
    email_changed = new_email != user.email
    if not changed and not email_changed:
        flash(f"No changes to save for {client_accounts.display_name(user)}.", "info")
        return redirect(url_for("admin.client_detail", user_id=user.id))

    user.email = new_email
    try:
        db.session.commit()
    except IntegrityError:
        # Emails are unique, so this is the new address being taken a moment ago
        db.session.rollback()
        if not email_changed:
            raise
        form.email.errors.append(EMAIL_TAKEN)
        user = _get_client_or_404(user.id)
        return render_template("admin/client_form.html", form=form, client=user, email_editable=True)

    message = f"Saved changes to {client_accounts.display_name(user)}."
    if email_changed:
        if not user.active:
            message += (
                f" Their email is now {user.email}. They're suspended, so we haven't sent an invitation; "
                "reactivate them first."
            )
        elif send_invite_email(user):
            db.session.commit()
            message += f" We sent a new invitation to {user.email}; the link in the old one no longer works."
        else:
            db.session.rollback()
            flash(
                f"Saved changes, but we couldn't email the new invitation to {user.email} just now. "
                "Use Resend invitation to try again.",
                "warning",
            )
            return redirect(url_for("admin.client_detail", user_id=user.id))
    flash(message, "success")
    return redirect(url_for("admin.client_detail", user_id=user.id))


# Status, invitations and removal --------------------------------------------


@bp.route("/clients/<int:user_id>/status", methods=["POST"])
@staff_required
def client_status(user_id):
    user = _get_client_or_404(user_id)
    active = _explicit_active()
    name = client_accounts.display_name(user)

    if not client_accounts.set_active(user, active):
        flash(f"{name} is already {'active' if active else 'suspended'}.", "info")
        return redirect(_back_to(user))

    db.session.commit()
    if not active:
        flash(
            f"Suspended {name}. They've been signed out and can't sign in or place orders until you reactivate them.",
            "success",
        )
    elif user.has_accepted_invite:
        flash(f"Reactivated {name}. They can sign in and place orders again.", "success")
    else:
        flash(f"Reactivated {name}. Resend their invitation so they can set a password.", "success")
    return redirect(_back_to(user))


@bp.route("/clients/<int:user_id>/resend-invite", methods=["POST"])
@staff_required
def client_resend_invite(user_id):
    user = _get_client_or_404(user_id)
    back = _back_to(user)
    if user.has_accepted_invite:
        flash(f"{user.full_name} has already accepted their invitation and can sign in.", "info")
    elif not user.active:
        flash(f"{user.full_name} is suspended. Reactivate them before sending a new invitation.", "warning")
    elif client_accounts.invite_recently_sent(user):
        flash(
            f"We sent an invitation to {user.email} less than a minute ago. Give it a few minutes to arrive, "
            "and ask them to check their spam folder.",
            "info",
        )
    elif send_invite_email(user):
        db.session.commit()
        flash(f"Sent a new invitation to {user.email}.", "success")
    else:
        db.session.rollback()
        flash(f"We couldn't send the invitation to {user.email} just now. Please try again in a few minutes.", "danger")
    return redirect(back)


@bp.route("/clients/<int:user_id>/delete", methods=["POST"])
@staff_required
def client_delete(user_id):
    user = _get_client_or_404(user_id)
    name, email = client_accounts.display_name(user), user.email
    try:
        client_accounts.delete_client(user)
    except client_accounts.ClientError as error:
        flash(str(error), "warning")
        return redirect(url_for("admin.client_detail", user_id=user_id))
    flash(f"Deleted the account for {name} ({email}).", "success")
    return redirect(url_for("admin.clients"))
