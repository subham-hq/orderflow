"""The supplier's team: the owner and the admins who help run the workspace.

Only the owner manages the team. The owner can never be suspended or
removed, nobody can suspend themselves, and an admin who has joined can
only be suspended (so their name stays on the orders they handled).
"""

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import ADMIN, OWNER, STAFF_ROLES, User
from ..permissions import company_select, get_company_object_or_404, owner_required
from ..services import clients as accounts
from ..services.accounts import AccountError, find_user_by_email, invite_user
from . import bp
from .forms import TeamInviteForm

EMAIL_TAKEN = "Someone with this email already has an OrderFlow account."


def _team_members():
    owner_first = case((User.role == OWNER, 0), else_=1)
    return db.session.scalars(
        company_select(User)
        .where(User.role.in_(STAFF_ROLES))
        .order_by(owner_first, func.lower(User.first_name), func.lower(func.coalesce(User.last_name, "")), User.id)
    ).all()


def _render_team(form):
    return render_template("admin/team.html", members=_team_members(), form=form)


def _get_member_or_404(user_id):
    """The owner or an admin of this company. Clients are managed on the Clients page."""
    user = get_company_object_or_404(User, user_id)
    if user.role not in STAFF_ROLES:
        abort(404)
    return user


def _protected(user, action, done):
    """Why this person can't be suspended or removed, or None."""
    if user.id == current_user.id:
        return f"You can't {action} your own account."
    if user.role == OWNER:
        return f"{user.full_name} is an owner of this account, so they can't be {done}."
    return None


@bp.route("/team")
@owner_required
def team():
    return _render_team(TeamInviteForm(formdata=None))


@bp.route("/team/invite", methods=["POST"])
@owner_required
def team_invite():
    form = TeamInviteForm()
    if form.validate_on_submit():
        try:
            user = invite_user(
                current_user.company,
                current_user._get_current_object(),
                form.email.data,
                ADMIN,
                form.first_name.data,
                last_name=form.last_name.data,
            )
        except AccountError as error:
            form.email.errors.append(_email_problem(form.email.data, str(error)))
        except IntegrityError:
            db.session.rollback()
            form.email.errors.append(EMAIL_TAKEN)
        else:
            flash(
                f"Invitation sent to {user.email}. Once {user.first_name} sets a password, they can manage orders, "
                "the catalog, clients and bulletins. Only you can manage the team and company settings.",
                "success",
            )
            return redirect(url_for("admin.team"))
    return _render_team(form)


def _email_problem(email, default):
    existing = find_user_by_email(email)
    if existing is not None and existing.company_id == current_user.company_id:
        if existing.is_client:
            return f"{existing.email} belongs to one of your clients. Use a different email for their team account."
        return f"{existing.email} is already on your team."
    return default


@bp.route("/team/<int:user_id>/resend-invite", methods=["POST"])
@owner_required
def team_resend_invite(user_id):
    user = _get_member_or_404(user_id)
    category, message = accounts.resend_invitation(user)
    flash(message, category)
    return redirect(url_for("admin.team"))


@bp.route("/team/<int:user_id>/status", methods=["POST"])
@owner_required
def team_status(user_id):
    user = _get_member_or_404(user_id)
    wanted = request.form.get("active")
    if wanted not in ("0", "1"):
        abort(400)
    active = wanted == "1"

    problem = _protected(user, "suspend", "suspended")
    if problem:
        flash(problem, "warning")
    elif not accounts.set_active(user, active):
        flash(f"{user.full_name} is already {'active' if active else 'suspended'}.", "info")
    else:
        db.session.commit()
        if not active:
            flash(
                f"Suspended {user.full_name}. They've been signed out and can't sign in until you reactivate them.",
                "success",
            )
        elif user.has_accepted_invite:
            flash(f"Reactivated {user.full_name}. They can sign in again.", "success")
        else:
            flash(f"Reactivated {user.full_name}. Resend their invitation so they can set a password.", "success")
    return redirect(url_for("admin.team"))


@bp.route("/team/<int:user_id>/delete", methods=["POST"])
@owner_required
def team_remove(user_id):
    user = _get_member_or_404(user_id)
    problem = _protected(user, "remove", "removed")
    if problem:
        flash(problem, "warning")
        return redirect(url_for("admin.team"))
    if user.has_accepted_invite:
        flash(
            f"{user.full_name} has already joined, so their account can't be removed. Suspend it instead: "
            "they won't be able to sign in, and their name stays on the orders they handled.",
            "warning",
        )
        return redirect(url_for("admin.team"))

    email = user.email
    db.session.delete(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(f"We couldn't remove {email} just now. Suspend the account instead.", "danger")
        return redirect(url_for("admin.team"))
    flash(f"Withdrew the invitation for {email}. The link we sent no longer works.", "success")
    return redirect(url_for("admin.team"))
