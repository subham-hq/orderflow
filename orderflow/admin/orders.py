"""Order management (placeholder: replaced as the page is built)."""

from flask import render_template

from ..permissions import staff_required
from . import bp


def _placeholder(title):
    return render_template("placeholder.html", title=title, layout="layouts/app.html")


@bp.route("/orders")
@staff_required
def orders():
    return _placeholder("Orders")


@bp.route("/orders/<number>")
@staff_required
def order_detail(number):
    return _placeholder(f"Order {number}")
