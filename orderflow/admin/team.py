"""Team members (placeholder: replaced as the page is built)."""

from flask import render_template

from ..permissions import owner_required
from . import bp


def _placeholder(title):
    return render_template("placeholder.html", title=title, layout="layouts/app.html")


@bp.route("/team")
@owner_required
def team():
    return _placeholder("Team")
