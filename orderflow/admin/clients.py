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
from .throttle import per_person

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


def _refused(form, problem):
    """Show a problem above the form; True if there was one."""
    if problem:
        form.form_errors.append(problem)
    return bool(problem)


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
@per_person("10 per minute")
def client_invite():
    form = ClientForm()
    if form.validate_on_submit() and not _refused(
        form, client_accounts.invitation_blocked(current_user.company, current_user)
    ):
        try:
            user, sent = invite_user(
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
            if sent:
                client_accounts.count_invitation(current_user.company_id)
                flash(
                    f"Invitation sent to {user.email}. {user.first_name} will show as Invited until they "
                    "accept it and set a password.",
                    "success",
                )
            else:
                flash(
                    f"We added {user.full_name}, but couldn't send the invitation email to {user.email} just now. "
                    "Use Resend invitation on this page to try again.",
                    "warning",
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
@per_person("20 per minute")
def client_edit(user_id):
    user = _get_client_or_404(user_id)
    form = ClientForm(obj=user if request.method == "GET" else None)

    if form.validate_on_submit():
        changes = {
            "first_name": form.first_name.data,
            "last_name": form.last_name.data or None,
            "buyer_company": form.buyer_company.data,
            "phone": form.phone.data or None,
        }
        new_email = form.email.data
        if new_email == user.email:
            return _save_client(user, form, changes, new_email)
        if email_taken(new_email):
            form.email.errors.append(_email_problem(new_email, EMAIL_TAKEN))
        elif user.has_accepted_invite:
            # They sign in with it, so the change waits until they confirm it
            if not user.active:
                form.email.errors.append(
                    f"{client_accounts.display_name(user)} is suspended, so they can't confirm a new email "
                    "address. Reactivate them first."
                )
            elif not _refused(form, client_accounts.invitation_blocked(user.company)):
                return _request_email_change(user, changes, new_email)
        elif not user.active or not _refused(form, client_accounts.invitation_blocked(user.company, user.invited_by)):
            # A fresh invitation goes to the new address (unless they're suspended)
            return _save_client(user, form, changes, new_email)
    return render_template("admin/client_form.html", form=form, client=user)


def _request_email_change(user, changes, new_email):
    """Save the other changes, and email the new address a link the client must open to switch to it."""
    changed = False
    for name, value in changes.items():
        if getattr(user, name) != value:
            setattr(user, name, value)
            changed = True
    if changed:
        db.session.commit()
    saved = f"Saved changes to {client_accounts.display_name(user)}. " if changed else ""
    if client_accounts.send_email_change_link(user, new_email, requested_by=current_user):
        client_accounts.count_invitation(user.company_id)
        flash(
            f"{saved}We sent a link to {new_email} to confirm it. {user.first_name}'s sign-in email changes once "
            f"they open the link and sign in; until then, they keep using {user.email}.",
            "success",
        )
    else:
        flash(
            f"{saved}We couldn't email the confirmation link to {new_email} just now. "
            "Please try again in a few minutes.",
            "warning",
        )
    return redirect(url_for("admin.client_detail", user_id=user.id))


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
        return render_template("admin/client_form.html", form=form, client=user)

    message = f"Saved changes to {client_accounts.display_name(user)}."
    if email_changed:
        if not user.active:
            message += (
                f" Their email is now {user.email}. They're suspended, so we haven't sent an invitation; "
                "reactivate them first."
            )
        elif send_invite_email(user):
            db.session.commit()
            client_accounts.count_invitation(user.company_id)
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
@per_person("10 per minute")
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
