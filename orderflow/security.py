"""Security headers and request hygiene."""

import re

from flask import abort, request
from flask_login import current_user

# ASCII control characters (NUL, tab, newline, ...). None belong in a URL path.
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

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


def init_security(app):
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
