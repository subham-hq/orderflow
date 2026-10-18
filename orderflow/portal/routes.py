"""Client portal routes. (Placeholders: replaced as each page is built.)"""

from flask import render_template

from ..permissions import client_required
from . import bp


def _placeholder(title):
    return render_template("placeholder.html", title=title, layout="layouts/portal.html")


@bp.route("/")
@client_required
def home():
    return _placeholder("Home")


@bp.route("/catalog")
@client_required
def catalog():
    return _placeholder("Catalog")


@bp.route("/orders")
@client_required
def orders():
    return _placeholder("Orders")


@bp.route("/orders/<number>")
@client_required
def order_detail(number):
    return _placeholder(f"Order {number}")


@bp.route("/account")
@client_required
def account():
    return _placeholder("Account")
