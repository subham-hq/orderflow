"""The cart: add products from the catalog, change quantities, remove lines."""

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..money import MAX_QUANTITY
from ..permissions import client_required, safe_next_url
from ..services import cart as cart_service
from ..services.cart import CartError, parse_id
from ..services.orders import OrderError, price_cart
from . import bp
from .forms import AddToCartForm, read_quantity


def priced_session_cart():
    """Price the session cart, taking out anything that can't be ordered and saying so."""
    cart = cart_service.get_cart()
    priced = price_cart(current_user.company_id, cart)
    kept = {str(line.product.id) for line in priced.lines}
    if kept != set(cart):
        result = cart_service.SyncResult(
            removed_names=list(priced.removed),
            unknown_count=len(set(cart) - kept) - len(priced.removed),
        )
        cart_service.save_cart({key: quantity for key, quantity in cart.items() if key in kept})
        flash(cart_service.sync_message(result), "warning")
    return priced


def render_cart(values=None, errors=None):
    priced = priced_session_cart()
    return render_template(
        "portal/cart.html",
        priced=priced,
        values=values or {},
        errors=errors or {},
    )


def read_cart_form(cart, formdata):
    """Apply the quantity fields and remove button of the cart form to a copy of the cart.

    Only lines already in the cart can be changed here; fields for anything
    else (a stale tab, a tampered form) are ignored. Returns
    (new_cart, values, errors): values are what the client typed, by product
    id, so the form can be shown again exactly as they left it.
    """
    new_cart, values, errors = dict(cart), {}, {}
    for key in formdata:
        if not key.startswith("qty-"):
            continue
        product_id = parse_id(key[4:])
        if product_id is None or str(product_id) not in cart:
            continue
        raw = formdata.get(key, "")
        values[product_id] = raw
        try:
            quantity = read_quantity(raw)
        except OrderError as error:
            errors[product_id] = str(error)
            continue
        if quantity == 0:
            new_cart.pop(str(product_id), None)
        else:
            new_cart[str(product_id)] = quantity

    remove_id = parse_id(formdata.get("remove"))
    if remove_id is not None:
        new_cart.pop(str(remove_id), None)
        errors.pop(remove_id, None)  # a line being removed doesn't need a valid quantity
    return new_cart, values, errors


def describe_changes(old_cart, new_cart):
    """Flash what an update changed, or that nothing did."""
    removed_ids = [int(key) for key in old_cart if key not in new_cart]
    changed = [key for key in new_cart if new_cart[key] != old_cart.get(key)]
    if not old_cart:
        flash("Your cart is empty, so there was nothing to update.", "info")
    elif not removed_ids and not changed:
        flash("Your cart is already up to date.", "info")
    if removed_ids:
        products = cart_service.active_products(current_user.company_id, removed_ids)
        names = cart_service.join_names(sorted(product.name for product in products.values()))
        flash(f"Removed {names or 'the item'} from your cart.", "success")
    if changed:
        flash("Quantities updated. The totals below use today's prices.", "success")


@bp.route("/cart")
@client_required
def cart():
    return render_cart()


@bp.route("/cart", methods=["POST"])
@client_required
def cart_update():
    cart = cart_service.get_cart()
    new_cart, values, errors = read_cart_form(cart, request.form)
    if errors:
        return render_cart(values=values, errors=errors)
    describe_changes(cart, new_cart)
    cart_service.save_cart(new_cart)
    return redirect(url_for("portal.cart"))


@bp.route("/cart/add", methods=["POST"])
@client_required
def cart_add():
    form = AddToCartForm()
    form.validate_on_submit()
    back = safe_next_url(url_for("portal.catalog"))
    if not back.startswith(url_for("portal.home")):
        back = url_for("portal.catalog")  # only ever return to a portal page

    product = None
    if not form.product_id.errors:
        product_id = form.product_id_value
        product = cart_service.active_products(current_user.company_id, [product_id]).get(product_id)
    if product is None:
        flash("That product isn't available any more, so nothing was added to your cart.", "warning")
        return redirect(back)
    if form.quantity.errors:
        flash(f"{form.quantity.errors[0]} Nothing was added for {product.name}.", "danger")
        return redirect(back)

    quantity = form.quantity_value
    cart = cart_service.get_cart()
    try:
        new_quantity, capped = cart_service.add_quantity(cart, product.id, quantity)
    except CartError as error:
        flash(str(error), "danger")
        return redirect(back)
    cart_service.save_cart(cart)

    if capped:
        flash(
            f"You can order up to {MAX_QUANTITY:,} of {product.name}, so your cart now has {new_quantity:,}.",
            "warning",
        )
    else:
        flash(f"Added {quantity:,} × {product.name} to your cart.", "success")
    if "#" not in back:
        back = f"{back}#product-{product.id}"
    return redirect(back)
