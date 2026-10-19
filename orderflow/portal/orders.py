"""The client's orders: the list, one order, cancelling, reordering and the proforma invoice."""

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import APPROVED, CANCELLED, FULFILLED, ORDER_STATUSES, PENDING, REJECTED, Order
from ..money import MAX_QUANTITY
from ..permissions import client_required
from ..services import cart as cart_service
from ..services.orders import OrderError, allowed_transitions, transition_order
from . import bp
from .helpers import get_client_order_or_404, like_pattern, page_arg, paginate, search_arg

PER_PAGE = 20

# Orders still in progress: the home page's "Open orders" tile counts these and links to their tab
OPEN_STATUSES = (PENDING, APPROVED)

# Status tabs on the orders list: key, label, statuses shown
TABS = (
    ("all", "All", ORDER_STATUSES),
    ("open", "Open", OPEN_STATUSES),
    ("pending", "Pending", (PENDING,)),
    ("approved", "Approved", (APPROVED,)),
    ("fulfilled", "Fulfilled", (FULFILLED,)),
    ("closed", "Closed", (REJECTED, CANCELLED)),
)
TAB_STATUSES = {key: statuses for key, _label, statuses in TABS}


def order_search_arg():
    """The order number searched for, without a leading # people often type."""
    return search_arg("q", max_length=32).lstrip("#").strip()


@bp.route("/orders")
@client_required
def orders():
    q = order_search_arg()
    tab = request.args.get("status", "all")
    if tab not in TAB_STATUSES:
        tab = "all"

    matching = [Order.company_id == current_user.company_id, Order.client_id == current_user.id]
    if q:
        matching.append(Order.number.ilike(like_pattern(q), escape="\\"))

    # Counts per tab follow the search, so each number matches what the tab shows
    by_status = dict(
        db.session.execute(db.select(Order.status, func.count(Order.id)).where(*matching).group_by(Order.status)).all()
    )
    counts = {key: sum(by_status.get(status, 0) for status in statuses) for key, statuses in TAB_STATUSES.items()}

    select = (
        db.select(Order)
        .where(*matching, Order.status.in_(TAB_STATUSES[tab]))
        .options(selectinload(Order.items))
        .order_by(Order.placed_at.desc(), Order.id.desc())
    )
    page = paginate(select, page_arg(), PER_PAGE)
    return render_template("portal/orders.html", page=page, tabs=TABS, tab=tab, counts=counts, q=q)


@bp.route("/orders/<number>")
@client_required
def order_detail(number):
    order = get_client_order_or_404(number)
    can_cancel = CANCELLED in allowed_transitions(order, current_user._get_current_object())
    return render_template("portal/order_detail.html", order=order, can_cancel=can_cancel)


@bp.route("/orders/<number>/cancel", methods=["POST"])
@client_required
def order_cancel(number):
    order = get_client_order_or_404(number, for_update=True)
    user = current_user._get_current_object()
    supplier = order.company.name
    if order.status == CANCELLED:
        flash(f"Order {order.number} is already cancelled.", "info")
    elif CANCELLED not in allowed_transitions(order, user):
        flash(
            f"Order {order.number} can't be cancelled because it's already {order.status}. "
            f"Contact {supplier} if something needs to change.",
            "warning",
        )
    else:
        try:
            transition_order(order, user, CANCELLED)
        except OrderError as error:
            flash(str(error), "warning")
        else:
            flash(f"Order {order.number} is cancelled. We've let {supplier} know.", "success")
    return redirect(url_for("portal.order_detail", number=order.number))


def reorder_into_cart(order, cart):
    """Put the order's still-available products into cart (a dict, changed in place).

    Each product ends up with at least the quantity ordered last time: a
    smaller quantity already in the cart is raised, a larger one is kept.
    That makes reordering safe to repeat. Returns lists of product names:
    (changed, unchanged, unavailable, no_room).
    """
    wanted, names = {}, {}
    unavailable = []
    for item in order.items:
        if item.product_id is None:
            unavailable.append(item.name)
            continue
        wanted[item.product_id] = min(wanted.get(item.product_id, 0) + item.quantity, MAX_QUANTITY)
        names.setdefault(item.product_id, item.name)

    available = cart_service.active_products(current_user.company_id, list(wanted))
    changed, unchanged, no_room = [], [], []
    for product_id, quantity in wanted.items():
        product = available.get(product_id)
        if product is None:
            unavailable.append(names[product_id])
            continue
        key = str(product_id)
        if key not in cart and len(cart) >= cart_service.MAX_CART_LINES:
            no_room.append(product.name)
        elif cart.get(key, 0) >= quantity:
            unchanged.append(product.name)
        else:
            cart[key] = quantity
            changed.append(product.name)
    return changed, unchanged, unavailable, no_room


def unavailable_message(names):
    joined = cart_service.join_names(names)
    if len(names) == 1:
        return f"{joined} is no longer available, so it wasn't added."
    return f"{joined} are no longer available, so they weren't added."


@bp.route("/orders/<number>/reorder", methods=["POST"])
@client_required
def order_reorder(number):
    order = get_client_order_or_404(number)
    cart = cart_service.get_cart()
    changed, unchanged, unavailable, no_room = reorder_into_cart(order, cart)
    cart_service.save_cart(cart)

    if changed:
        count = len(changed)
        flash(
            f"Added {count} {'item' if count == 1 else 'items'} from {order.number} to your cart at the quantities "
            "you ordered. Prices are today's, so check the total before you place the order.",
            "success",
        )
    elif unchanged:
        flash(f"Everything from {order.number} is already in your cart.", "info")
    elif not no_room:
        flash(f"None of the items on {order.number} are available any more, so nothing was added.", "warning")
    if unavailable and (changed or unchanged or no_room):
        flash(unavailable_message(unavailable), "warning")
    if no_room:
        flash(
            f"Your cart can hold up to {cart_service.MAX_CART_LINES} different products, so "
            f"{cart_service.join_names(no_room)} couldn't be added. Place an order first, then reorder again.",
            "warning",
        )

    if changed or unchanged:
        return redirect(url_for("portal.cart"))
    return redirect(url_for("portal.order_detail", number=order.number))


@bp.route("/orders/<number>/invoice")
@client_required
def invoice(number):
    order = get_client_order_or_404(number)
    return render_template(
        "shared/invoice.html", order=order, back_url=url_for("portal.order_detail", number=order.number)
    )
