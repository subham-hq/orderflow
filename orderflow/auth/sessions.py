"""How long a sign-in lasts.

Sessions are signed cookies (nothing is stored on the server), so a copy of
the cookie works wherever it is sent. To keep that window short:

- The session cookie lasts until the browser closes, and stops working after
  SESSION_IDLE_TIMEOUT (12 hours) without a request. A forgotten tab, or a
  cookie copied from a shared computer, expires on its own.
- "Keep me signed in" adds Flask-Login's remember-me cookie, which signs the
  person back in when the session ends, for REMEMBER_COOKIE_DURATION.
- Signing out deletes both cookies from this browser. It deliberately doesn't
  end sessions on other devices: that would also invalidate emailed links,
  which are tied to User.auth_version. Changing or resetting the password
  (or a suspension) bumps auth_version and ends every session everywhere.
"""

import time
from datetime import timedelta

from flask import current_app, g, request, session
from flask_login import logout_user

from . import bp

IDLE_TIMEOUT = timedelta(hours=12)

# When the session was last used, in seconds since the epoch. It's written
# back at most once a minute, so most responses don't need to re-send the cookie.
_LAST_SEEN = "_last_seen"
_WRITE_EVERY = 60

# What Flask-Login keeps in the session for a signed-in user
_SIGN_IN_KEYS = ("_user_id", "_fresh", "_id")


def now():
    return int(time.time())


def begin():
    """Note the start of a new signed-in session."""
    session[_LAST_SEEN] = now()


def end():
    """Sign out whoever is signed in here, and delete their remember-me cookie."""
    session.clear()
    # After clear(): logout_user() marks the remember-me cookie for deletion
    # in the session, and clearing afterwards would undo that.
    logout_user()


@bp.record_once
def _default_settings(state):
    state.app.config.setdefault("SESSION_IDLE_TIMEOUT", IDLE_TIMEOUT)


@bp.before_app_request
def end_idle_session():
    """Sign out a session that hasn't been used for SESSION_IDLE_TIMEOUT."""
    if request.endpoint == "static" or "_user_id" not in session:
        return
    last_seen = session.get(_LAST_SEEN)
    current = now()
    if isinstance(last_seen, int) and current - last_seen > current_app.config["SESSION_IDLE_TIMEOUT"].total_seconds():
        # Keep the cart and the form token; a remember-me cookie, if they
        # chose one, signs them straight back in.
        for key in (*_SIGN_IN_KEYS, _LAST_SEEN):
            session.pop(key, None)
        g.session_timed_out = True
        return
    if not isinstance(last_seen, int) or current - last_seen >= _WRITE_EVERY:
        session[_LAST_SEEN] = current
