"""Product catalog (placeholder: replaced as the page is built)."""

from flask import render_template

from ..permissions import staff_required
from . import bp


def _placeholder(title):
    return render_template("placeholder.html", title=title, layout="layouts/app.html")


@bp.route("/products")
@staff_required
def products():
    return _placeholder("Catalog")
