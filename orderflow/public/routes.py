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


class ContactForm(FlaskForm):
    name = StringField("Your name", validators=[DataRequired(), Length(max=120)])
    email = EmailField("Work email", validators=[DataRequired(), Email(), Length(max=254)])
    company = StringField("Company", validators=[Optional(), Length(max=160)])
    message = TextAreaField("What do you sell, and to whom?", validators=[Optional(), Length(max=2000)])


@bp.route("/")
def index():
    return render_template("public/index.html", contact_form=ContactForm())


@bp.route("/contact", methods=["POST"])
@limiter.limit("5 per hour", methods=["POST"])
def contact():
    form = ContactForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            flash(errors[0], "danger")
            break
        return redirect(url_for("public.index", _anchor="contact"))

    lead = Lead(
        name=clean(form.name.data),
        email=normalize_email(form.email.data),
        company=clean(form.company.data),
        message=clean(form.message.data),
    )
    db.session.add(lead)
    db.session.commit()

    if current_app.config.get("CONTACT_EMAIL"):
        send_email(
            current_app.config["CONTACT_EMAIL"],
            f"New enquiry from {lead.name}",
            "lead",
            lead=lead,
            reply_to=lead.email,
        )
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
