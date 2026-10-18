"""Application configuration.

Settings come from environment variables so the same code runs locally,
in tests and on Render. See .env.example for the full list.
"""

import logging
import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

log = logging.getLogger("orderflow")

# The development config's fallback key is published right here, so anyone
# could sign session cookies and emailed links with it. check_settings()
# only allows it inside the flask command on a developer's computer.
DEV_SECRET_KEY = "dev-only-secret-key-do-not-use-in-production"


def _bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def running_flask_command():
    """True inside the `flask` command (flask run, flask db upgrade, ...), false under gunicorn."""
    # Flask's command line sets this before it loads the app
    return os.environ.get("FLASK_RUN_FROM_CLI") == "true"


def local_database_url():
    """The SQLite file used when DATABASE_URL isn't set."""
    return f"sqlite:///{BASE_DIR / 'instance' / 'orderflow.db'}"


def database_url():
    """Return the SQLAlchemy URL for DATABASE_URL, defaulting to local SQLite."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        (BASE_DIR / "instance").mkdir(exist_ok=True)
        return local_database_url()

    # Render (and Heroku-style hosts) hand out postgres:// URLs. SQLAlchemy
    # needs the dialect name and the psycopg 3 driver spelled out.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


class Config:
    APP_NAME = "OrderFlow"
    SECRET_KEY = os.environ.get("SECRET_KEY")

    # Public address of the site, used for links in emails
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000").rstrip("/")

    SQLALCHEMY_DATABASE_URI = database_url()
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Sessions: signed cookies, revocable through User.auth_version
    SESSION_COOKIE_NAME = "orderflow_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    PERMANENT_SESSION_LIFETIME = timedelta(days=14)
    REMEMBER_COOKIE_NAME = "orderflow_remember"
    REMEMBER_COOKIE_DURATION = timedelta(days=30)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)

    # CSRF tokens stay valid for the life of the session, so a form left
    # open for a while still submits.
    WTF_CSRF_TIME_LIMIT = None

    # Rate limiting. memory:// counts in each gunicorn worker separately and
    # starts again on every restart; a redis:// or rediss:// URL (such as
    # Render Key Value) shares the counts between workers and instances.
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True
    RATELIMIT_ENABLED = _bool("RATELIMIT_ENABLED", True)
    # If Redis can't be reached, count in memory until it's back rather than
    # failing every sign-in
    RATELIMIT_IN_MEMORY_FALLBACK_ENABLED = True

    # Email
    MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "console")  # console | resend | smtp | memory
    MAIL_FROM = os.environ.get("MAIL_FROM", "OrderFlow <no-reply@localhost>")
    MAIL_REPLY_TO = os.environ.get("MAIL_REPLY_TO")
    RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
    SMTP_HOST = os.environ.get("SMTP_HOST")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
    # STARTTLS after connecting (the usual, on port 587)...
    SMTP_USE_TLS = _bool("SMTP_USE_TLS", True)
    # ...or TLS from the start (implicit TLS). Unset means on for port 465 only.
    SMTP_USE_SSL = _bool("SMTP_USE_SSL", None)

    # Where landing-page enquiries are sent, and the contact shown in the legal pages
    CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL")
    # The business name that operates the site, used in the legal pages
    LEGAL_NAME = os.environ.get("LEGAL_NAME", "OrderFlow")

    # How long emailed links stay valid
    VERIFY_TOKEN_MAX_AGE = timedelta(days=3)
    RESET_TOKEN_MAX_AGE = timedelta(hours=1)
    INVITE_TOKEN_MAX_AGE = timedelta(days=7)

    DEFAULT_TIMEZONE = "Asia/Kolkata"
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024
    TRUST_PROXY = _bool("TRUST_PROXY", False)
    # How many proxies sit in front of the app (Render: 1)
    PROXY_HOPS = int(os.environ.get("PROXY_HOPS", "1"))
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")


class DevelopmentConfig(Config):
    APP_ENV = "development"
    DEBUG = True
    SECRET_KEY = Config.SECRET_KEY or DEV_SECRET_KEY


class TestingConfig(Config):
    APP_ENV = "testing"
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = os.environ.get("TEST_DATABASE_URL") or "sqlite://"
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    MAIL_BACKEND = "memory"
    MAIL_FROM = "OrderFlow <no-reply@orderflow.test>"
    APP_BASE_URL = "http://localhost"
    CONTACT_EMAIL = "sales@orderflow.test"


class ProductionConfig(Config):
    APP_ENV = "production"
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    TRUST_PROXY = True
    PREFERRED_URL_SCHEME = "https"
    MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "resend")


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name=None):
    name = (name or os.environ.get("APP_ENV") or "").strip().lower()
    if not name:
        # Falling back to development on a server would run it with DEBUG on,
        # cookies that aren't Secure and possibly the public key above.
        if not running_flask_command():
            raise RuntimeError(
                "APP_ENV is not set. Set APP_ENV=production on a server (render.yaml does this), "
                "or APP_ENV=development in .env on your computer."
            )
        name = "development"
    if name not in CONFIGS:
        raise RuntimeError(f"Unknown APP_ENV {name!r}; use one of {', '.join(CONFIGS)}")
    return CONFIGS[name]


def check_settings(app):
    """Refuse to start with settings that would expose or lose data, and warn about risky ones.

    Runs while the app is created, after any overrides passed to create_app.
    """
    config = app.config
    local = running_flask_command()
    if config.get("SECRET_KEY") == DEV_SECRET_KEY and not local:
        raise RuntimeError(
            "SECRET_KEY is not set. The development key is public, so it only works with the flask command "
            "(flask run) on your computer. Generate a key and add it to the environment."
        )
    if config.get("APP_ENV") == "development" and not local:
        log.warning(
            "APP_ENV is development outside the flask command: DEBUG is on and cookies aren't marked Secure. "
            "Use APP_ENV=production on a server."
        )
    workers = os.environ.get("WEB_CONCURRENCY", "").strip()
    in_memory = str(config.get("RATELIMIT_STORAGE_URI", "")).startswith("memory://")
    if not config.get("TESTING") and in_memory and workers.isdigit() and int(workers) > 1:
        log.warning(
            "Rate limits are counted separately in each of the %s gunicorn workers, so every limit is %s times "
            "looser. Run one worker, or set RATELIMIT_STORAGE_URI to a Redis URL to share the counts.",
            workers,
            workers,
        )
    if config.get("APP_ENV") == "production":
        uri = str(config.get("SQLALCHEMY_DATABASE_URI") or "")
        if uri == local_database_url():
            raise RuntimeError(
                "DATABASE_URL is not set. Production needs a Postgres database; without one the app would "
                "use a local SQLite file, and a redeploy or restart on Render deletes it with every order in it."
            )
        if uri.startswith("sqlite"):
            log.warning("The database is SQLite. On Render its file is deleted on every deploy; use Postgres.")
        if not config.get("CONTACT_EMAIL"):
            log.warning(
                "CONTACT_EMAIL is not set, so nobody is emailed about contact-form enquiries. They are saved "
                "and logged; list them with `flask leads`."
            )
