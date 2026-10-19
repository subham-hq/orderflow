"""Security headers and request hygiene."""

import contextlib
import re

from flask import abort, request
from flask_login import current_user

from .config import check_settings

# ASCII control characters (NUL, tab, newline, ...). None belong in a URL path.
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

# Links in emails carry a secret token as their last path segment. Anyone
# who can read the logs could use one before the person it was sent to.
TOKEN_PATH = re.compile(r"^/(verify|reset-password|invite)/(?!resend$)[^/]+", re.IGNORECASE)

# WSGI environ key holding the path that gunicorn's access log prints
# (bin/start.sh logs %({orderflow.log_path}e)s instead of the request line)
LOG_PATH_KEY = "orderflow.log_path"

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "object-src 'none'",
    ]
)


def log_safe_path(path):
    """The path as logs should show it: emailed tokens hidden, control characters escaped."""
    path = TOKEN_PATH.sub(lambda match: f"/{match.group(1)}/[token]", path)
    return CONTROL_CHARACTERS.sub(lambda match: f"\\x{ord(match.group()):02x}", path)


class AccessLogPath:
    """WSGI middleware that stores a log-safe copy of the path for the access log.

    The query string is left out too, since search terms can hold client
    names. The access log reads the copy from the environ after the response.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        with contextlib.suppress(UnicodeEncodeError):
            # WSGI servers pass the path as latin-1; the URL itself is UTF-8
            path = path.encode("latin-1").decode("utf-8", "replace")
        environ[LOG_PATH_KEY] = log_safe_path(path)
        return self.wsgi_app(environ, start_response)


def init_security(app):
    # Fail at start-up rather than run with a public key or a throwaway database
    check_settings(app)
    app.wsgi_app = AccessLogPath(app.wsgi_app)

    @app.before_request
    def reject_control_characters():
        # NUL characters can't be stored in Postgres text columns (psycopg
        # refuses them), so a NUL anywhere a view might query with turns into
        # a 500. Paths never legitimately hold any control character; query
        # and form values may hold newlines and tabs, but never NUL.
        if CONTROL_CHARACTERS.search(request.path):
            abort(400)
        for source in (request.args, request.form):
            for key, values in source.lists():
                if "\x00" in key or any("\x00" in value for value in values):
                    abort(400)

    @app.after_request
    def set_security_headers(response):
        headers = response.headers
        headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.is_secure:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

        # Pages for signed-in users must not be cached by browsers or proxies.
        # Static files keep their normal caching.
        if request.endpoint != "static" and current_user.is_authenticated:
            headers["Cache-Control"] = "no-store"
        return response
