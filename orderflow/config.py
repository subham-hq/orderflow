"""Application configuration.

Settings come from environment variables so the same code runs locally,
in tests and on Render. See .env.example for the full list.
"""

import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def database_url():
    """Return the SQLAlchemy URL for DATABASE_URL, defaulting to local SQLite."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        instance_dir = BASE_DIR / "instance"
        instance_dir.mkdir(exist_ok=True)
        return f"sqlite:///{instance_dir / 'orderflow.db'}"

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

    # Rate limiting (memory:// is per process; set a redis:// URL to share limits)
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True
    RATELIMIT_ENABLED = _bool("RATELIMIT_ENABLED", True)

    # Email
    MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "console")  # console | resend | smtp | memory
    MAIL_FROM = os.environ.get("MAIL_FROM", "OrderFlow <no-reply@localhost>")
    MAIL_REPLY_TO = os.environ.get("MAIL_REPLY_TO")
    RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
    SMTP_HOST = os.environ.get("SMTP_HOST")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
    SMTP_USE_TLS = _bool("SMTP_USE_TLS", True)

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
    DEBUG = True
    SECRET_KEY = Config.SECRET_KEY or "dev-only-secret-key-do-not-use-in-production"


class TestingConfig(Config):
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
    name = (name or os.environ.get("APP_ENV") or "development").lower()
    if name not in CONFIGS:
        raise RuntimeError(f"Unknown APP_ENV {name!r}; use one of {', '.join(CONFIGS)}")
    return CONFIGS[name]
