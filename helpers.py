import requests

from cs50 import SQL
from flask import flash, redirect, render_template, session
from functools import wraps

db = SQL("sqlite:///orderflow.db")

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

db = SQL("sqlite:///orderflow.db")

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
