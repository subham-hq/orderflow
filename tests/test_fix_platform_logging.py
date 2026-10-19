"""What the logs show: no emailed tokens, one entry per error."""

import io
import logging
import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from gunicorn.config import Config as GunicornConfig
from gunicorn.glogging import Logger as GunicornLogger
from werkzeug.test import EnvironBuilder

from orderflow.security import LOG_PATH_KEY, log_safe_path

START_SH = (Path(__file__).resolve().parent.parent / "bin" / "start.sh").read_text()


@pytest.mark.parametrize(
    ("path", "logged"),
    [
        ("/reset-password/IjEyMyI.Zx9Q.abc-_", "/reset-password/[token]"),
        ("/invite/eyJ1aWQiOjF9.ZxZx.sig", "/invite/[token]"),
        ("/verify/eyJ1aWQiOjF9.ZxZx.sig", "/verify/[token]"),
        ("/Invite/eyJ1aWQiOjF9.ZxZx.sig", "/Invite/[token]"),
        ("/verify/abc/extra", "/verify/[token]/extra"),
        ("/verify/resend", "/verify/resend"),
        ("/verify", "/verify"),
        ("/app/orders/ORD-000012", "/app/orders/ORD-000012"),
        ("/bad\nline", "/bad\\x0aline"),
    ],
)
def test_log_safe_path(path, logged):
    assert log_safe_path(path) == logged


def gunicorn_access_line(environ, status="200 OK"):
    """Format one access-log line exactly as gunicorn does with bin/start.sh's settings."""
    log_format = re.search(r"--access-logformat '([^']+)'", START_SH).group(1)
    config = GunicornConfig()
    config.set("accesslog", "-")
    config.set("access_log_format", log_format)
    logger = GunicornLogger(config)
    stream = io.StringIO()
    logger.access_log.handlers = [logging.StreamHandler(stream)]
    response = SimpleNamespace(status=status, headers=[], sent=512)
    logger.access(response, SimpleNamespace(headers=[]), environ, timedelta(milliseconds=12))
    return stream.getvalue().strip()


def serve(app, url, **kwargs):
    """Send one request through the app's WSGI callable, as gunicorn does, keeping its environ."""
    path, _, query = url.partition("?")
    environ = EnvironBuilder(path=path, query_string=query, **kwargs).get_environ()
    statuses = []
    body = app.wsgi_app(environ, lambda status, headers, exc_info=None: statuses.append(status))
    b"".join(body)
    if hasattr(body, "close"):
        body.close()
    return environ, statuses[0]


def test_the_access_log_never_shows_tokens_query_strings_or_referers(app):
    environ, status = serve(
        app,
        "/reset-password/IjEyMyI.Zx9Q.secret-token?next=/app/clients%3Fq%3DMehta",
        headers={"Referer": "https://orderflow.example/invite/another-secret", "User-Agent": "Mail scanner"},
    )
    assert environ[LOG_PATH_KEY] == "/reset-password/[token]"

    line = gunicorn_access_line(environ, status=status)
    assert '"GET /reset-password/[token]" 400 512 12ms "Mail scanner"' in line
    assert "secret" not in line and "Mehta" not in line and "next=" not in line


def test_the_access_log_shows_ordinary_paths(app):
    environ, status = serve(app, "/privacy?utm_source=newsletter")
    line = gunicorn_access_line(environ, status=status)
    assert '"GET /privacy" 200' in line and "utm_source" not in line

    environ, status = serve(app, "/caf%C3%A9")
    assert '"GET /café" 404' in gunicorn_access_line(environ, status=status)


def test_start_script_uses_the_safe_access_log_format():
    assert "--access-logfile -" in START_SH
    log_format = re.search(r"--access-logformat '([^']+)'", START_SH).group(1)
    assert "%({orderflow.log_path}e)s" in log_format
    for unsafe in ("%(r)s", "%(U)s", "%(q)s", "%(f)s"):  # request line, raw path, query, Referer
        assert unsafe not in log_format
