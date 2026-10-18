"""Client accounts: list, invite, edit, suspend or reactivate, resend invites, delete."""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import CLIENT, User
from ..permissions import company_select, get_company_object_or_404, safe_next_url, staff_required
from ..services import clients as client_accounts
from ..services.accounts import AccountError, email_taken, find_user_by_email, invite_user, send_invite_email
from . import bp
from .forms import ClientForm, like_pattern, page_number, paginate, search_text

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
    q = search_text(request.args.get("q"))
    status = request.args.get("status", "")
    if status not in client_accounts.ACCOUNT_STATUSES:
        status = ""

    filtered = company_select(User).where(User.role == CLIENT)
    if q:
        pattern = like_pattern(q)
        full_name = User.first_name + " " + func.coalesce(User.last_name, "")
        filtered = filtered.where(
            or_(
                full_name.ilike(pattern, escape="\\"),
                User.email.ilike(pattern, escape="\\"),
                User.buyer_company.ilike(pattern, escape="\\"),
            )
        )
    counts = client_accounts.status_counts(filtered)
    filtered = client_accounts.where_status(filtered, status)
    ordered = filtered.order_by(
        func.lower(func.coalesce(User.buyer_company, User.first_name)), func.lower(User.first_name), User.id
    )
    page = paginate(ordered, page_number(request.args.get("page")))

    total_clients = db.session.scalar(
        db.select(func.count(User.id)).where(User.company_id == current_user.company_id, User.role == CLIENT)
    )
    return render_template(
        "admin/clients.html",
        page=page,
        q=q,
        status=status,
        counts=counts,
        order_counts=client_accounts.order_counts(current_user.company_id, [user.id for user in page.items]),
        total_clients=total_clients,
        filtering=bool(q or status),
    )


# Invite ----------------------------------------------------------------------


@bp.route("/clients/invite", methods=["GET", "POST"])
@staff_required
def client_invite():
    form = ClientForm()
    if form.validate_on_submit():
        try:
            user = invite_user(
                current_user.company,
                current_user._get_current_object(),
                form.email.data,
                CLIENT,
                form.first_name.data,
                last_name=form.last_name.data,
                buyer_company=form.buyer_company.data,
                phone=form.phone.data,
            )
        except AccountError as error:
            form.email.errors.append(_email_problem(form.email.data, str(error)))
        except IntegrityError:
            # The same email was registered a moment ago in another request
            db.session.rollback()
            form.email.errors.append(EMAIL_TAKEN)
        else:
            flash(
                f"Invitation sent to {user.email}. {user.first_name} will show as Invited until they "
                "accept it and set a password.",
                "success",
            )
            return redirect(url_for("admin.client_detail", user_id=user.id))
    return render_template("admin/client_form.html", form=form, client=None)


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
    category, message = client_accounts.resend_invitation(user)
    flash(message, category)
    return redirect(_back_to(user))


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
