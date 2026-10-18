"""The client portal: browse the catalog, place and track orders."""

from flask import Blueprint

bp = Blueprint("portal", __name__)

from . import routes  # noqa: E402,F401
