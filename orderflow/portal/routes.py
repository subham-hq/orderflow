"""Client portal routes. (Placeholders: replaced as each page is built.)"""

from flask import render_template

from ..permissions import client_required
from . import bp


def _placeholder(title):
    return render_template("placeholder.html", title=title, layout="layouts/portal.html")


@bp.route("/account")
@client_required
def account():
    return _placeholder("Account")
