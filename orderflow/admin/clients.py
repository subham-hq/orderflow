"""Client accounts (placeholder: replaced as the page is built)."""

from flask import render_template

from ..permissions import staff_required
from . import bp


def _placeholder(title):
    return render_template("placeholder.html", title=title, layout="layouts/app.html")


@bp.route("/clients")
@staff_required
def clients():
    return _placeholder("Clients")
