from flask import flash, g, redirect, render_template, request, session, url_for
from flask_limiter.util import get_remote_address
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db, limiter
from ..mail import external_url, send_email
from ..permissions import home_url_for, login_and_verified_required, safe_next_url
from ..services.accounts import (
    AccountError,
    accept_invite,
    create_company_with_owner,
    find_user_by_email,
    mark_verified,
    normalize_email,
    send_verification_email,
)
from ..timeutil import utcnow
from ..tokens import ACCEPT_INVITE, RESET_PASSWORD, VERIFY_EMAIL, TokenError, load_token, make_token
from . import bp
from .forms import (
    PASSWORD_HINT,
    ChangePasswordForm,
    ForgotPasswordForm,
    LoginForm,
    SetPasswordForm,
    SignupForm,
)

# Checked against when an email isn't registered, so a failed sign-in takes
# about as long whether or not the account exists.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def _email_key():
    return normalize_email(request.form.get("email")) or "anonymous"


def _sign_in_key():
    # One count per account *and* client address, so wrong guesses sent from
    # somewhere else can never lock the real person out of their account.
    return f"{_email_key()}|{get_remote_address()}"


def _sign_in_failed(response):
    return g.get("sign_in_failed", False)


def _start_session(user, remember=False, keep_cart=False):
    """Sign the user in with a fresh session.

    keep_cart carries a client's cart over when the same person signs in again
    (after changing their password); a sign-in never inherits someone else's.
    """
    cart = session.get("cart") if keep_cart else None
    session.clear()
    if cart:
        session["cart"] = cart
    login_user(user, remember=remember)
    user.last_login_at = utcnow()
    db.session.commit()


@bp.route("/signup", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(home_url_for(current_user))

    form = SignupForm()
    if form.validate_on_submit():
        try:
            owner = create_company_with_owner(
                form.company_name.data,
                form.first_name.data,
                form.last_name.data,
                form.email.data,
                form.password.data,
            )
        except AccountError as error:
            form.email.errors.append(str(error))
        else:
            _start_session(owner)
            send_verification_email(owner)
            return redirect(url_for("auth.verify_pending"))
    return render_template("auth/signup.html", form=form, password_hint=PASSWORD_HINT)


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
# Only wrong passwords count, so signing in normally never uses this up
@limiter.limit("10 per 15 minutes", methods=["POST"], key_func=_sign_in_key, deduct_when=_sign_in_failed)
def login():
    if current_user.is_authenticated:
        return redirect(home_url_for(current_user))

    form = LoginForm()
    if form.validate_on_submit():
        user = find_user_by_email(form.email.data)
        if user is None or not user.password_hash:
            check_password_hash(_DUMMY_HASH, form.password.data)
            password_ok = False
        else:
            password_ok = user.check_password(form.password.data)

        if not password_ok:
            g.sign_in_failed = True
            form.password.errors.append("That email and password don't match. Check them and try again.")
        elif not user.active:
            form.email.errors.append("This account has been suspended. Contact your supplier or account owner.")
        else:
            next_url = safe_next_url(None)
            _start_session(user, remember=form.remember.data)
            if not user.is_verified:
                return redirect(url_for("auth.verify_pending"))
            return redirect(next_url or home_url_for(user))
    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    session.clear()
    flash("You've been signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/verify")
@login_required
def verify_pending():
    if current_user.is_verified:
        return redirect(home_url_for(current_user))
    return render_template("auth/verify_pending.html")


@bp.route("/verify/resend", methods=["POST"])
@login_required
@limiter.limit("3 per hour", methods=["POST"], key_func=lambda: f"verify:{current_user.get_id()}")
def resend_verification():
    if current_user.is_verified:
        return redirect(home_url_for(current_user))
    if send_verification_email(current_user):
        flash(f"We sent a new confirmation link to {current_user.email}.", "success")
    else:
        flash("We couldn't send the email just now. Please try again in a few minutes.", "danger")
    return redirect(url_for("auth.verify_pending"))


@bp.route("/verify/<token>")
def verify_email(token):
    try:
        user = load_token(VERIFY_EMAIL, token)
    except TokenError as error:
        return render_template(
            "auth/link_invalid.html",
            message=str(error),
            action_url=url_for("auth.verify_pending") if current_user.is_authenticated else url_for("auth.login"),
            action_label="Send a new link" if current_user.is_authenticated else "Sign in",
        ), 400

    if not user.is_verified:
        mark_verified(user)
        db.session.commit()
    flash("Your email is confirmed. Welcome to OrderFlow!", "success")
    if current_user.is_authenticated and current_user.id == user.id:
        return redirect(home_url_for(user))
    return redirect(url_for("auth.login"))


@bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def forgot_password():
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        user = find_user_by_email(form.email.data)
        if user is not None and user.active and user.password_hash:
            send_email(
                user.email,
                "Reset your OrderFlow password",
                "reset_password",
                user=user,
                link=external_url("auth.reset_password", token=make_token(RESET_PASSWORD, user)),
            )
        # Same answer either way, so this form can't be used to find accounts
        return render_template("auth/forgot_password_sent.html", email=normalize_email(form.email.data))
    return render_template("auth/forgot_password.html", form=form)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def reset_password(token):
    try:
        user = load_token(RESET_PASSWORD, token)
    except TokenError as error:
        return render_template(
            "auth/link_invalid.html",
            message=str(error),
            action_url=url_for("auth.forgot_password"),
            action_label="Request a new link",
        ), 400

    form = SetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.end_all_sessions()
        mark_verified(user)  # they proved they can read this inbox
        db.session.commit()
        if not user.active:
            flash("Your password was changed, but this account is suspended.", "warning")
            return redirect(url_for("auth.login"))
        _start_session(user)
        flash("Your password has been changed and you're signed in.", "success")
        return redirect(home_url_for(user))
    return render_template("auth/reset_password.html", form=form, password_hint=PASSWORD_HINT)


@bp.route("/invite/<token>", methods=["GET", "POST"], endpoint="accept_invite")
@limiter.limit("10 per hour", methods=["POST"])
def accept_invite_page(token):
    try:
        user = load_token(ACCEPT_INVITE, token)
    except TokenError as error:
        return render_template(
            "auth/link_invalid.html",
            message=f"{error} Ask whoever invited you to send a new invitation.",
            action_url=url_for("auth.login"),
            action_label="Go to sign in",
        ), 400

    if user.has_accepted_invite:
        flash("You've already set up this account. Sign in to continue.", "info")
        return redirect(url_for("auth.login"))
    if not user.active:
        flash("This invitation has been withdrawn.", "warning")
        return redirect(url_for("auth.login"))

    form = SetPasswordForm()
    if form.validate_on_submit():
        try:
            accept_invite(user, form.password.data)
        except AccountError as error:
            form.password.errors.append(str(error))
        else:
            _start_session(user)
            flash(f"Welcome to {user.company.name}!", "success")
            return redirect(home_url_for(user))
    return render_template("auth/accept_invite.html", form=form, invitee=user, password_hint=PASSWORD_HINT)


@bp.route("/account/password", methods=["GET", "POST"])
@login_and_verified_required
@limiter.limit("10 per hour", methods=["POST"])
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append("That isn't your current password.")
        elif form.password.data == form.current_password.data:
            form.password.errors.append("Choose a password you haven't used here before.")
        else:
            user = current_user._get_current_object()
            user.set_password(form.password.data)
            user.end_all_sessions()  # sign out every other device
            db.session.commit()
            _start_session(user, keep_cart=True)
            flash("Password updated. You've been signed out on your other devices.", "success")
            return redirect(home_url_for(user))
    return render_template("auth/change_password.html", form=form, password_hint=PASSWORD_HINT)
