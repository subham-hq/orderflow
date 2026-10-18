from flask import current_app, flash, jsonify, redirect, render_template, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from sqlalchemy import text
from wtforms import EmailField, StringField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional

from ..extensions import db, limiter
from ..mail import send_email
from ..models import Lead
from ..permissions import home_url_for
from ..services.accounts import clean, normalize_email
from . import bp


def one_line(value):
    """Collapse runs of whitespace, line breaks included, so a name can't start a new line in a log or subject."""
    return " ".join(value.split()) if isinstance(value, str) else value


class ContactForm(FlaskForm):
    name = StringField(
        "Your name",
        filters=[one_line],
        validators=[
            DataRequired("Enter your name."),
            Length(max=120, message="Keep your name to 120 characters or fewer."),
        ],
    )
    email = EmailField(
        "Work email",
        validators=[
            DataRequired("Enter your work email."),
            Length(max=254, message="Keep the email address to 254 characters or fewer."),
            Email("Enter a valid email address, like name@business.in."),
        ],
    )
    company = StringField(
        "Company",
        filters=[one_line],
        validators=[Optional(), Length(max=160, message="Keep the company name to 160 characters or fewer.")],
    )
    message = TextAreaField(
        "What do you sell, and to whom?",
        validators=[Optional(), Length(max=2000, message="Keep your message to 2,000 characters or fewer.")],
    )


@bp.route("/")
def index():
    return render_template("public/index.html", contact_form=ContactForm())


@bp.route("/contact", methods=["POST"])
@limiter.limit("5 per hour", methods=["POST"])
def contact():
    form = ContactForm()
    if not form.validate_on_submit():
        # Show the page again with the errors next to each field and the
        # visitor's text kept, rather than redirecting and losing it.
        return render_template("public/index.html", contact_form=form), 400

    lead = Lead(
        name=clean(form.name.data),
        email=normalize_email(form.email.data),
        company=clean(form.company.data),
        message=clean(form.message.data),
    )
    db.session.add(lead)
    db.session.commit()

    # Log every enquiry, so one is never only in a table nobody looks at
    log = current_app.logger
    log.info("Contact enquiry %s from %s <%s> (%s)", lead.id, lead.name, lead.email, lead.company or "no company")
    inbox = current_app.config.get("CONTACT_EMAIL")
    if not inbox:
        log.warning("Contact enquiry %s wasn't emailed: CONTACT_EMAIL is not set. See it with `flask leads`.", lead.id)
    elif not send_email(inbox, f"New enquiry from {lead.name}", "lead", lead=lead, reply_to=lead.email):
        log.warning("Contact enquiry %s was saved but couldn't be emailed. See it with `flask leads`.", lead.id)
    flash("Thanks! We'll reply to you by email within one working day.", "success")
    return redirect(url_for("public.index", _anchor="contact"))


@bp.route("/start")
def start():
    """Send signed-in users to their workspace and everyone else to sign up."""
    if current_user.is_authenticated:
        return redirect(home_url_for(current_user))
    return redirect(url_for("auth.signup"))


@bp.route("/privacy")
def privacy():
    return render_template("public/privacy.html")


@bp.route("/terms")
def terms():
    return render_template("public/terms.html")


@bp.route("/healthz")
@limiter.exempt
def healthz():
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        current_app.logger.exception("Health check failed")
        return jsonify(status="error"), 503
    return jsonify(status="ok")


@bp.route("/robots.txt")
def robots():
    body = "User-agent: *\nDisallow: /app/\nDisallow: /portal/\n"
    return current_app.response_class(body, mimetype="text/plain")
