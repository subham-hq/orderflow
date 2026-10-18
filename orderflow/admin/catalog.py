"""Product catalog: add, edit, show/hide, and CSV export."""

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from .. import timeutil
from ..extensions import db
from ..models import OrderItem, Product
from ..permissions import company_select, get_company_object_or_404, safe_next_url, staff_required
from ..services import catalog
from . import bp
from .forms import ProductForm, product_form_data


def _company_id():
    return current_user.company_id


# List ------------------------------------------------------------------------


@bp.route("/products")
@staff_required
def products():
    return render_template("placeholder.html", title="Catalog", layout="layouts/app.html")


# Add and edit ----------------------------------------------------------------


def _render_product_form(form, product=None):
    order_lines = 0
    if product is not None:
        order_lines = db.session.scalar(db.select(func.count(OrderItem.id)).where(OrderItem.product_id == product.id))
    return render_template(
        "admin/product_form.html",
        form=form,
        product=product,
        order_lines=order_lines,
        categories=catalog.category_options(_company_id()),
        units=catalog.COMMON_UNITS,
    )


def _sku_taken(form, product):
    existing = catalog.find_product_by_sku(_company_id(), form.sku.data)
    if existing is not None and (product is None or existing.id != product.id):
        form.sku.errors.append(
            f"{existing.sku} is already the SKU for “{existing.name}”. Use a different SKU, or edit that product."
        )
        return True
    return False


def _sku_race_error(form):
    db.session.rollback()
    form.sku.errors.append(f"{form.sku.data} was just used for another product. Use a different SKU.")


@bp.route("/products/new", methods=["GET", "POST"])
@staff_required
def product_new():
    form = ProductForm(data={"gst_rate": "18", "unit": catalog.DEFAULT_UNIT} if request.method == "GET" else None)
    if form.validate_on_submit() and not _sku_taken(form, None):
        data = form.product_data(catalog.Categories.for_company(_company_id()))
        product = Product(company_id=_company_id(), **data)
        db.session.add(product)
        try:
            db.session.commit()
        except IntegrityError:
            _sku_race_error(form)
        else:
            visibility = "Clients can order it now." if product.is_active else "It's hidden from clients for now."
            flash(f"Added “{product.name}” to your catalog. {visibility}", "success")
            if request.form.get("next_action") == "another":
                return redirect(url_for("admin.product_new"))
            return redirect(url_for("admin.products"))
    return _render_product_form(form)


@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@staff_required
def product_edit(product_id):
    product = get_company_object_or_404(Product, product_id)
    form = ProductForm(data=product_form_data(product) if request.method == "GET" else None)
    # Keep the product's current GST rate selectable even if it isn't a standard slab
    form.gst_rate.choices = catalog.gst_choices(product.gst_rate_bp)

    if form.validate_on_submit() and not _sku_taken(form, product):
        data = form.product_data(catalog.Categories.for_company(_company_id()))
        if not catalog.apply_product_data(product, data):
            flash(f"No changes to save for “{product.name}”.", "info")
            return redirect(url_for("admin.products"))
        try:
            db.session.commit()
        except IntegrityError:
            _sku_race_error(form)
            product = get_company_object_or_404(Product, product_id)
        else:
            flash(f"Saved “{product.name}”. New orders use these details; past orders keep theirs.", "success")
            return redirect(url_for("admin.products"))
    return _render_product_form(form, product)


@bp.route("/products/<int:product_id>/status", methods=["POST"])
@staff_required
def product_status(product_id):
    product = get_company_object_or_404(Product, product_id)
    wanted = request.form.get("active")
    if wanted not in ("0", "1"):
        abort(400)
    active = wanted == "1"
    back = safe_next_url(url_for("admin.products"))

    if product.is_active == active:
        state = "visible to clients" if active else "hidden from clients"
        flash(f"“{product.name}” is already {state}.", "info")
        return redirect(back)

    product.is_active = active
    db.session.commit()
    if active:
        flash(f"“{product.name}” is back in the client catalog.", "success")
    else:
        flash(f"“{product.name}” is hidden. Clients can't order it, and past orders are unchanged.", "success")
    return redirect(back)


# CSV export ------------------------------------------------------------------


def _csv_response(text, filename):
    return Response(
        text,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@bp.route("/products/export.csv")
@staff_required
def products_export():
    rows = db.session.scalars(company_select(Product).order_by(func.lower(Product.name), Product.id))
    today = timeutil.to_local(timeutil.utcnow(), current_user.company.timezone).strftime("%Y-%m-%d")
    return _csv_response(catalog.products_csv(rows), f"products-{today}.csv")


@bp.route("/products/import/template.csv")
@staff_required
def products_import_template():
    return _csv_response(catalog.template_csv(), "orderflow-products-template.csv")
