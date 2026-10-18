"""The supplier's catalog as the client sees it: search, categories and add to cart."""

from flask import render_template, request
from flask_login import current_user
from sqlalchemy import func, or_

from ..extensions import db
from ..models import Product
from ..permissions import client_required
from ..services import cart as cart_service
from . import bp
from .helpers import like_pattern, page_arg, paginate, search_arg

PER_PAGE = 24


def orderable():
    """Conditions for products the signed-in client can order."""
    return [Product.company_id == current_user.company_id, Product.is_active.is_(True)]


def category_names():
    """Categories that have at least one orderable product, A to Z."""
    names = db.session.scalars(
        db.select(Product.category).where(*orderable(), Product.category.is_not(None)).distinct()
    )
    return sorted({name for name in names if name and name.strip()}, key=str.lower)


@bp.route("/catalog")
@client_required
def catalog():
    q = search_arg("q")
    category = search_arg("category", max_length=80)

    select = db.select(Product).where(*orderable())
    if q:
        pattern = like_pattern(q)
        select = select.where(
            or_(
                Product.name.ilike(pattern, escape="\\"),
                Product.sku.ilike(pattern, escape="\\"),
                Product.description.ilike(pattern, escape="\\"),
            )
        )
    if category:
        select = select.where(Product.category == category)
    select = select.order_by(func.lower(Product.name), Product.id)
    page = paginate(select, page_arg(), PER_PAGE)

    # Tell an empty catalog apart from a search with no results
    catalog_is_empty = page.total == 0 and db.session.scalar(db.select(func.count(Product.id)).where(*orderable())) == 0
    cart = cart_service.get_cart()
    return render_template(
        "portal/catalog.html",
        page=page,
        q=q,
        category=category,
        categories=category_names(),
        catalog_is_empty=catalog_is_empty,
        in_cart=cart_service.quantities_by_product(cart),
        cart_items=sum(cart.values()),
        # Where "Add to cart" comes back to: this page, with its search, category and page number
        next_url=request.full_path.rstrip("?"),
    )
