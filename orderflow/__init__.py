"""OrderFlow: B2B ordering for suppliers and their business clients."""

import logging

from flask import Flask
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import get_config
from .extensions import csrf, db, limiter, login_manager, migrate

__version__ = "2.0.0"


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    # SQLite ignores foreign keys unless asked; Postgres always enforces them.
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_app(config_name=None, **overrides):
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))
    app.config.update(overrides)

    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY is not set. Generate one and add it to the environment.")

    if app.config["TRUST_PROXY"]:
        # Render terminates TLS at its proxy. Trust only the forwarded values
        # its proxy adds (the right-most ones), which clients can't forge.
        hops = app.config["PROXY_HOPS"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)

    logging.basicConfig(level=app.config["LOG_LEVEL"], format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _warn_about_production_settings(app)

    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True, compare_type=True)
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)

    from . import models  # noqa: F401  (register models with SQLAlchemy)

    _register_user_loader()
    _register_template_helpers(app)
    _register_blueprints(app)

    from .errors import init_errors
    from .security import init_security

    init_security(app)
    init_errors(app)

    from .cli import init_cli

    init_cli(app)
    return app


def _warn_about_production_settings(app):
    """Log clear warnings for settings that would break a live deployment."""
    if not app.config.get("SESSION_COOKIE_SECURE"):
        return  # not production
    log = logging.getLogger("orderflow")
    base_url = app.config["APP_BASE_URL"]
    if "localhost" in base_url or not base_url.startswith("https://"):
        log.warning("APP_BASE_URL is %r; set it to your https:// address so email links work.", base_url)
    if app.config["MAIL_BACKEND"] == "resend" and not app.config.get("RESEND_API_KEY"):
        log.warning("MAIL_BACKEND is resend but RESEND_API_KEY is not set; emails will not be sent.")
    if app.config["MAIL_BACKEND"] == "console":
        log.warning("MAIL_BACKEND is console; emails are only written to the log.")
    if "localhost" in app.config["MAIL_FROM"]:
        log.warning("MAIL_FROM is %r; set it to an address on your verified sending domain.", app.config["MAIL_FROM"])


def _register_user_loader():
    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        # Session ids look like "<id>:<auth_version>". Bumping auth_version
        # (password change, reset, suspension) signs the user out everywhere.
        try:
            uid, version = (int(part) for part in str(user_id).split(":", 1))
        except ValueError:
            return None
        user = db.session.get(User, uid)
        if user is None or user.auth_version != version or not user.active:
            return None
        return user


def _register_blueprints(app):
    from .admin import bp as admin_bp
    from .auth import bp as auth_bp
    from .portal import bp as portal_bp
    from .public import bp as public_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(portal_bp, url_prefix="/portal")
    app.register_blueprint(admin_bp, url_prefix="/app")


def _register_template_helpers(app):
    from flask import g, has_request_context
    from flask_login import current_user

    from . import money, timeutil
    from .services.orders import STATUS_LABELS

    def _tz():
        # Emails rendered outside a request can set g.timezone; pages use the
        # signed-in user's company time zone.
        if getattr(g, "timezone", None):
            return g.timezone
        if has_request_context() and current_user.is_authenticated:
            return current_user.company.timezone
        return timeutil.DEFAULT_TZ

    app.jinja_env.filters.update(
        inr=money.format_inr,
        inr_compact=money.format_inr_compact,
        gst=money.rate_label,
        datetime=lambda value, fmt="%d %b %Y, %I:%M %p": timeutil.format_datetime(value, _tz(), fmt),
        date=lambda value, fmt="%d %b %Y": timeutil.format_date(value, _tz(), fmt),
        timeago=timeutil.time_ago,
    )
    app.jinja_env.globals.update(
        APP_NAME=app.config["APP_NAME"],
        APP_VERSION=__version__,
        STATUS_LABELS=STATUS_LABELS,
    )

    @app.context_processor
    def inject_now():
        local_now = timeutil.to_local(timeutil.utcnow(), _tz())
        return {"current_year": local_now.year}

    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True
