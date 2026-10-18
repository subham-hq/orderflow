"""Time helpers.

Timestamps are stored in UTC and shown in the company's time zone
(Asia/Kolkata unless the company changes it). SQLite hands datetimes back
without tzinfo, so always pass stored values through as_utc() before
converting or comparing them.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "Asia/Kolkata"

# A short list for the settings form; any IANA name works in the database
COMMON_TIMEZONES = (
    "Asia/Kolkata",
    "Asia/Dubai",
    "Asia/Singapore",
    "Asia/Kathmandu",
    "Asia/Dhaka",
    "Asia/Colombo",
    "Europe/London",
    "Europe/Berlin",
    "America/New_York",
    "America/Los_Angeles",
    "Australia/Sydney",
    "UTC",
)


def utcnow():
    return datetime.now(UTC)


def as_utc(value):
    """Return value as an aware UTC datetime (None stays None)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def get_zone(name):
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


def to_local(value, tz_name):
    """Convert a stored UTC datetime to the given time zone."""
    if value is None:
        return None
    return as_utc(value).astimezone(get_zone(tz_name))


def parse_local_datetime(value, tz_name):
    """Parse an <input type="datetime-local"> value ("2026-10-18T14:30")
    entered in tz_name and return it in UTC. Returns None for empty input
    and raises ValueError for anything that isn't a valid date and time."""
    value = (value or "").strip()
    if not value:
        return None
    naive = datetime.fromisoformat(value)
    if naive.tzinfo is not None:
        return naive.astimezone(UTC)
    return naive.replace(tzinfo=get_zone(tz_name)).astimezone(UTC)


def local_input_value(value, tz_name):
    """Format a stored UTC datetime for a datetime-local input."""
    local = to_local(value, tz_name)
    return local.strftime("%Y-%m-%dT%H:%M") if local else ""


def format_datetime(value, tz_name, fmt="%d %b %Y, %I:%M %p"):
    local = to_local(value, tz_name)
    if local is None:
        return ""
    return local.strftime(fmt).replace(" 0", " ").lstrip("0")


def format_date(value, tz_name, fmt="%d %b %Y"):
    local = to_local(value, tz_name)
    if local is None:
        return ""
    return local.strftime(fmt).lstrip("0")


def time_ago(value, now=None):
    """Short relative time, e.g. "just now", "5 min ago", "3 days ago"."""
    if value is None:
        return ""
    now = as_utc(now) if now else utcnow()
    seconds = int((now - as_utc(value)).total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 45:
        return "just now"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min ago"
    hours = round(minutes / 60)
    if hours < 24:
        return f"{hours} hr ago"
    days = round(hours / 24)
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    months = round(days / 30)
    if months < 12:
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = round(days / 365)
    return f"{years} year{'s' if years != 1 else ''} ago"
