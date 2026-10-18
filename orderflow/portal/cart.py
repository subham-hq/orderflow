"""The cart: add products from the catalog, change quantities, remove lines, place the order."""

import secrets

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import Order
from ..money import MAX_QUANTITY
from ..permissions import client_required, safe_next_url
from ..services import cart as cart_service
from ..services.cart import CartError, cart_version, parse_id
from ..services.orders import OrderError, place_order, price_cart
from . import bp
from .forms import AddToCartForm, CheckoutForm, read_quantity

BLANK_QUANTITY_MESSAGE = "Enter a quantity, or 0 to remove this line."


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


def render_cart(form=None, values=None, errors=None):
    """Show the cart page. Passing the submitted form keeps the client's notes on screen."""
    priced = priced_session_cart()
    form = form or CheckoutForm()
    # A fresh one-time key and fingerprint every time the page is shown
    form.submission_key.data = secrets.token_urlsafe(16)
    form.cart_version.data = cart_version(priced)
    return render_template(
        "portal/cart.html",
        priced=priced,
        form=form,
        values=values or {},
        errors=errors or {},
    )


def read_cart_form(cart, formdata):
    """Apply the quantity fields and remove button of the cart form to a copy of the cart.

    Only lines already in the cart can be changed here; fields for anything
    else (a stale tab, a tampered form) are ignored. A line is removed only by
    a quantity of 0 or its remove button; a blank or unreadable quantity is an
    error, so a typo never empties a line. Returns
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
        if not raw.strip():
            # Browsers send a number box they can't read ("5-", "1e", a letter) as
            # an empty value, so a blank is a typo to fix, never a request to remove
            errors[product_id] = BLANK_QUANTITY_MESSAGE
            continue
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
    form = CheckoutForm()
    cart = cart_service.get_cart()
    new_cart, values, errors = read_cart_form(cart, request.form)
    if errors:
        return render_cart(form=form, values=values, errors=errors)
    describe_changes(cart, new_cart)
    cart_service.save_cart(new_cart)
    if form.notes.data:
        # Show the page straight away rather than redirecting, so notes typed before updating aren't lost
        return render_cart(form=form)
    return redirect(url_for("portal.cart"))


def order_placed(order):
    cart_service.clear_cart()
    flash(
        f"Order {order.number} is placed. {order.company.name} will review it, "
        "and we'll email you when its status changes.",
        "success",
    )
    return redirect(url_for("portal.order_detail", number=order.number))


def cart_matches_order(cart, order):
    """True if the cart holds exactly the products and quantities on the order."""
    ordered = {str(item.product_id): item.quantity for item in order.items if item.product_id is not None}
    return bool(cart) and cart == ordered


def already_placed(order):
    """The checkout form was submitted again after the order was made.

    A double click sends the same cart twice, so the second response clears
    the cart too (otherwise its cookie would bring the ordered items back).
    Coming back to an old cart page later must not empty a cart the client
    has filled again since, so in that case the cart is left alone.
    """
    if cart_matches_order(cart_service.get_cart(), order):
        return order_placed(order)
    flash(f"You already placed this order as {order.number}, so we didn't place it again.", "info")
    return redirect(url_for("portal.order_detail", number=order.number))


@bp.route("/checkout", methods=["POST"])
@client_required
def checkout():
    form = CheckoutForm()
    form_ok = form.validate_on_submit()
    if form_ok:
        # Submitted twice (a double click, or back and submit again): show the order made the first time
        existing = db.session.scalar(
            db.select(Order).where(
                Order.company_id == current_user.company_id,
                Order.client_id == current_user.id,
                Order.submission_key == form.submission_key.data,
            )
        )
        if existing is not None:
            return already_placed(existing)

    cart = cart_service.get_cart()
    new_cart, values, errors = read_cart_form(cart, request.form)
    if not form_ok or errors:
        return render_cart(form=form, values=values, errors=errors)

    quantities_changed = new_cart != cart
    if quantities_changed:
        cart_service.save_cart(new_cart)
    priced = priced_session_cart()
    if priced.is_empty:
        flash("Your cart is empty. Add at least one product to place an order.", "warning")
        return redirect(url_for("portal.cart"))
    if quantities_changed:
        flash("We've updated your quantities. Check the new total, then place your order.", "info")
        return render_cart(form=form)
    if form.cart_version.data != cart_version(priced):
        flash(
            "Your cart changed since this page was opened. Check the items and total, then place your order.",
            "warning",
        )
        return render_cart(form=form)

    try:
        order, created = place_order(
            current_user._get_current_object(),
            cart_service.get_cart(),
            notes=form.notes.data,
            submission_key=form.submission_key.data,
        )
    except OrderError as error:
        flash(str(error), "danger")
        return render_cart(form=form)
    if not created:
        # Another request with the same key won the race to create it
        return already_placed(order)
    return order_placed(order)


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
