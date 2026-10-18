"""The supplier's workspace: orders, catalog, clients, team and settings."""

from flask import Blueprint

bp = Blueprint("admin", __name__)

from . import bulletins, catalog, clients, dashboard, orders, settings, team  # noqa: E402,F401
