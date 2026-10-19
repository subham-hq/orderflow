"""Friendly error pages."""

import math
import time

from flask import render_template, request
from flask_wtf.csrf import CSRFError

from .security import log_safe_path

ERRORS = {
    400: ("Something was wrong with that request", "Please go back and try again."),
    403: ("You don't have access to this page", "If you think you should, ask your account owner."),
    404: ("We couldn't find that page", "The link may be out of date, or the page may have moved."),
    405: ("That action isn't allowed here", "Please go back and try again."),
    413: ("That upload is too large", "Please try again with a smaller file."),
    429: ("Too many attempts", "Please wait a while and try again."),
    500: ("Something went wrong on our side", "We've logged the problem. Please try again in a moment."),
}


def _render(code, title=None, message=None):
    default_title, default_message = ERRORS.get(code, ERRORS[500])
    return (
        render_template(
            "errors/error.html",
            code=code,
            title=title or default_title,
            message=message or default_message,
        ),
        code,
    )


def wait_message(seconds):
    """How long to wait before trying again, in words: limits last from a minute to an hour."""
    if seconds is None:
        return ERRORS[429][1]
    minutes = max(1, math.ceil(seconds / 60))
    if minutes == 1:
        return "Please wait a minute and try again."
    if minutes < 60:
        return f"Please wait {minutes} minutes and try again."
    hours = math.ceil(minutes / 60)
    return "Please wait an hour and try again." if hours == 1 else f"Please wait {hours} hours and try again."


def _seconds_until_reset():
    """Seconds until the limit this request hit resets, as in the Retry-After header."""
    from .extensions import limiter

    try:
        limit = limiter.current_limit
        return None if limit is None else max(0, int(limit.reset_at - time.time()))
    except Exception:  # the limiter's storage may be unreachable; the page must still render
        return None


def init_errors(app):
    @app.errorhandler(CSRFError)
    def csrf_error(error):
        return _render(
            400,
            "This form has expired",
            "Refresh the page and submit the form again.",
        )

    for code in (400, 403, 404, 405, 413):
        app.register_error_handler(code, lambda error, code=code: _render(code))

    @app.errorhandler(429)
    def too_many_requests(error):
        # Limits run from one minute (sign-in per address) to one hour (sign-up,
        # password reset), so say how long this one lasts.
        return _render(429, message=wait_message(_seconds_until_reset()))

    def log_exception(exc_info):
        # Flask calls this once for every unhandled exception, just before the
        # 500 handler below. Replacing it (rather than logging again in the
        # handler) keeps one traceback per error, and the path never shows a
        # token from an emailed link.
        app.logger.error("Unhandled error on %s %s", request.method, log_safe_path(request.path), exc_info=exc_info)

    app.log_exception = log_exception

    @app.errorhandler(500)
    def server_error(error):
        from .extensions import db

        db.session.rollback()
        return _render(500)
