import os
import sqlite3
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from cs50 import SQL
from flask import flash, redirect, session
from functools import wraps

# Resolve paths relative to this file so the app works from any working directory
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "orderflow.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")


def init_db():
    """
    Create the SQLite database from schema.sql if it does not exist yet.
    """

    if os.path.exists(DB_PATH):
        return

    connection = sqlite3.connect(DB_PATH)
    try:
        with open(SCHEMA_PATH) as schema:
            connection.executescript(schema.read())
    except Exception:
        # Don't leave a half-built database behind
        connection.close()
        os.remove(DB_PATH)
        raise
    connection.close()


init_db()

# Single shared database handle, used by these decorators and by app.py
db = SQL(f"sqlite:///{DB_PATH}")

def client_required(f):
    """
    Decorate routes to allow access only to users with role 'client'.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):

        # Ensure user is logged in
        if session.get("user_id") is None:
            return redirect("/login")

        # Get user role
        rows = db.execute(
            "SELECT role FROM users WHERE id = ?",
            session["user_id"]
        )

        # Block access if not client
        if len(rows) != 1 or rows[0]["role"] != "client":
            flash("Access denied. Clients only.", "danger")
            return redirect("/")

        return f(*args, **kwargs)

    return decorated_function

def admin_required(f):
    """
    Decorate routes to allow access only to users with role 'admin'.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):

        # Ensure user is logged in
        if session.get("user_id") is None:
            return redirect("/login")

        # Fetch user role from database
        rows = db.execute(
            "SELECT role FROM users WHERE id = ?",
            session["user_id"]
        )

        # If not admin → deny access
        if len(rows) != 1 or rows[0]["role"] != "admin":
            flash("Access denied. Administrator privileges required.", "danger")
            return redirect("/")

        return f(*args, **kwargs)

    return decorated_function

def inr(value):
    """Format value as INR."""
    return f"₹{value:,.2f}"

def to_paise(value):
    """
    Convert a rupee amount entered in a form (e.g. "19.99") to integer paise.

    Uses Decimal and rounds to the nearest paisa. float(value) * 100 can land
    just below the real amount (19.99 * 100 = 1998.9999...), and int() would
    then cut it down by one paisa.

    Raises ValueError if the value is not a finite number.
    """

    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValueError(f"Invalid amount: {value!r}")

    if not amount.is_finite():
        raise ValueError(f"Invalid amount: {value!r}")

    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
