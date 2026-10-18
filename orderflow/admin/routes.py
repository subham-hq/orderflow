"""Supplier workspace routes. (Placeholders: replaced as each page is built.)"""

from flask import render_template

from ..permissions import owner_required, staff_required
from . import bp


def _placeholder(title):
    return render_template("placeholder.html", title=title, layout="layouts/app.html")


@bp.route("/")
@staff_required
def dashboard():
    return _placeholder("Dashboard")


@bp.route("/orders")
@staff_required
def orders():
    return _placeholder("Orders")


@bp.route("/orders/<number>")
@staff_required
def order_detail(number):
    return _placeholder(f"Order {number}")


@bp.route("/products")
@staff_required
def products():
    return _placeholder("Catalog")


@bp.route("/clients")
@staff_required
def clients():
    return _placeholder("Clients")


@bp.route("/team")
@owner_required
def team():
    return _placeholder("Team")


@bp.route("/bulletins")
@staff_required
def bulletins():
    return _placeholder("Bulletins")


@bp.route("/settings")
@staff_required
def settings():
    return _placeholder("Settings")
