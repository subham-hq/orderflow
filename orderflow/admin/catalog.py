"""Product catalog: add, edit, show/hide, and CSV import/export."""

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import RequestEntityTooLarge

from .. import money, timeutil
from ..extensions import db
from ..models import OrderItem, Product
from ..permissions import company_select, get_company_object_or_404, safe_next_url, staff_required
from ..services import catalog
from . import bp
from .forms import ProductForm, ProductImportForm, product_form_data

# Which CSV column each form field comes from, for import error messages
_CSV_COLUMN_FOR_FIELD = {
    "name": "name",
    "sku": "sku",
    "price": "price",
    "gst_rate": "gst_percent",
    "category": "category",
    "unit": "unit",
    "description": "description",
    "is_active": "active",
}
MAX_LISTED_IMPORT_ERRORS = 200


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


# CSV import ------------------------------------------------------------------


def _row_formdata(values, product, columns):
    """Form input for one CSV row. Columns left out of the file keep the
    product's current values (or the defaults for a new product)."""
    data = product_form_data(product) if product is not None else {"unit": catalog.DEFAULT_UNIT, "is_active": True}
    for column in ("sku", "name", "price", "category", "unit", "description"):
        if column in columns:
            data[column] = values.get(column, "")
    if "gst_percent" in columns:
        text = values.get("gst_percent", "").strip().rstrip("%").strip()
        try:
            data["gst_rate"] = money.rate_input_value(money.parse_gst_rate(text)) if text else ""
        except ValueError:
            data["gst_rate"] = text
    formdata = MultiDict({key: value for key, value in data.items() if key != "is_active"})
    if data.get("is_active"):
        formdata["is_active"] = "y"
    return formdata


def _plan_import(rows, columns):
    """Validate every row with the product form's rules.

    Returns (plan, errors): plan is a list of (product or None, data); errors
    a list of (row number, column, message). Nothing is written here.
    """
    company_id = _company_id()
    existing = {product.sku.upper(): product for product in db.session.scalars(company_select(Product))}
    categories = catalog.Categories.for_company(company_id)
    plan, errors, seen = [], [], {}

    for row in rows:
        if row.error:
            errors.append((row.number, "", row.error))
            continue
        try:
            sku = catalog.normalize_sku(row.values.get("sku"))
        except catalog.CatalogError:
            sku = None
        product = existing.get(sku) if sku else None

        formdata = _row_formdata(row.values, product, columns)
        active = None
        if "active" in columns:
            try:
                active = catalog.parse_active(row.values.get("active"))
            except catalog.CatalogError as error:
                errors.append((row.number, "active", str(error)))
                continue
        if active is not None:
            formdata.pop("is_active", None)
            if active:
                formdata["is_active"] = "y"

        form = ProductForm(formdata=formdata, meta={"csrf": False})
        form.gst_rate.choices = catalog.gst_choices(product.gst_rate_bp if product else None)
        if not form.validate():
            for name, messages in form.errors.items():
                errors.append((row.number, _CSV_COLUMN_FOR_FIELD.get(name, name), messages[0]))
            continue

        if sku in seen:
            errors.append((row.number, "sku", f"{sku} is also on row {seen[sku]}. Each SKU can appear only once."))
            continue
        seen[sku] = row.number
        data = form.product_data(categories)
        if data["category"]:
            categories.add(data["category"])
        plan.append((product, data))
    return plan, errors


def _apply_import(plan):
    created = updated = unchanged = 0
    for product, data in plan:
        if product is None:
            db.session.add(Product(company_id=_company_id(), **data))
            created += 1
        elif catalog.apply_product_data(product, data):
            updated += 1
        else:
            unchanged += 1
    db.session.commit()
    return created, updated, unchanged


def _plural(count, word):
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _import_summary(created, updated, unchanged):
    parts = []
    if created:
        parts.append(f"added {_plural(created, 'product')}")
    if updated:
        parts.append(f"updated {_plural(updated, 'product')}")
    if not parts:
        return "Nothing to change: every product in the file already matches your catalog."
    summary = "Import complete: " + " and ".join(parts) + "."
    if unchanged:
        summary += f" {_plural(unchanged, 'row')} already matched and {'was' if unchanged == 1 else 'were'} left as is."
    return summary


@bp.route("/products/import", methods=["GET", "POST"])
@staff_required
def products_import():
    form = ProductImportForm()
    errors, error_count = [], 0
    if form.validate_on_submit():
        try:
            columns, rows = catalog.read_products_csv(form.file.data.read())
        except catalog.CatalogError as error:
            form.file.errors.append(str(error))
        else:
            plan, errors = _plan_import(rows, columns)
            error_count = len(errors)
            if not errors:
                try:
                    counts = _apply_import(plan)
                except IntegrityError:
                    db.session.rollback()
                    form.file.errors.append(
                        "Someone changed the catalog while this file was importing. Nothing was imported; "
                        "upload the file again."
                    )
                else:
                    flash(_import_summary(*counts), "success")
                    return redirect(url_for("admin.products"))
            errors = errors[:MAX_LISTED_IMPORT_ERRORS]
    return render_template(
        "admin/products_import.html",
        form=form,
        errors=errors,
        error_count=error_count,
        columns=catalog.CSV_COLUMNS,
        required_columns=catalog.REQUIRED_CSV_COLUMNS,
        max_rows=catalog.MAX_IMPORT_ROWS,
        gst_rates=[label for _, label in catalog.gst_choices()],
    )


@bp.errorhandler(RequestEntityTooLarge)
def upload_too_large(error):
    """Uploads over MAX_CONTENT_LENGTH: explain on the import page instead of an error page."""
    if (
        request.endpoint == "admin.products_import"
        and current_user.is_authenticated
        and current_user.is_staff
        and current_user.is_verified
    ):
        flash("That file is larger than 1 MB. Split it into smaller files and import them one at a time.", "danger")
        return redirect(url_for("admin.products_import"), code=303)
    from ..errors import ERRORS

    title, message = ERRORS[413]
    return render_template("errors/error.html", code=413, title=title, message=message), 413
