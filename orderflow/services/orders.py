"""Pricing carts, placing orders and moving orders through their statuses."""

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..mail import external_url, send_email
from ..models import (
    APPROVED,
    CANCELLED,
    CLIENT_TRANSITIONS,
    FULFILLED,
    OWNER,
    PENDING,
    REJECTED,
    STAFF_TRANSITIONS,
    Company,
    Order,
    OrderEvent,
    OrderItem,
    Product,
    User,
)
from ..money import MAX_QUANTITY, gst_amount
from ..timeutil import utcnow
from .accounts import staff_emails

STATUS_LABELS = {
    PENDING: "Pending review",
    APPROVED: "Approved",
    REJECTED: "Rejected",
    FULFILLED: "Fulfilled",
    CANCELLED: "Cancelled",
}

# Wording for the timeline and emails
TRANSITION_VERBS = {
    APPROVED: "approved",
    REJECTED: "rejected",
    FULFILLED: "marked as fulfilled",
    CANCELLED: "cancelled",
}


# How many order numbers to try before giving up, when earlier ones are taken
MAX_NUMBER_ATTEMPTS = 100


class OrderError(ValueError):
    """A problem with an order the user can fix, with a safe message."""


@dataclass
class CartLine:
    product: Product
    quantity: int
    subtotal_paise: int
    tax_paise: int
    total_paise: int


@dataclass
class PricedCart:
    lines: list[CartLine]
    subtotal_paise: int
    tax_paise: int
    total_paise: int
    removed: list[str]  # names of items that are no longer available

    @property
    def item_count(self):
        return sum(line.quantity for line in self.lines)

    @property
    def is_empty(self):
        return not self.lines


def parse_quantity(value):
    """Parse a quantity from a form. Returns an int (0 means remove) or raises OrderError."""
    text = str(value if value is not None else "").strip()
    if text == "":
        return 0
    if not text.isascii() or not text.isdigit():
        raise OrderError("Quantities must be whole numbers.")
    quantity = int(text)
    if quantity > MAX_QUANTITY:
        raise OrderError(f"The most you can order of one item is {MAX_QUANTITY:,}.")
    return quantity


def price_cart(company_id, cart):
    """Price a cart ({product_id: quantity}) against the live catalog.

    Products that were removed or hidden since they were added are dropped
    and reported in .removed, so the client sees exactly what they'll pay.
    """
    product_ids = [int(pid) for pid in cart]
    products = {}
    if product_ids:
        rows = db.session.scalars(
            db.select(Product).where(Product.company_id == company_id, Product.id.in_(product_ids))
        )
        products = {product.id: product for product in rows}

    lines, removed = [], []
    for raw_id, quantity in cart.items():
        product = products.get(int(raw_id))
        if product is None or not product.is_active:
            if product is not None:
                removed.append(product.name)
            continue
        if quantity <= 0:
            continue
        subtotal = product.price_paise * quantity
        tax = gst_amount(subtotal, product.gst_rate_bp)
        lines.append(CartLine(product, quantity, subtotal, tax, subtotal + tax))

    lines.sort(key=lambda line: line.product.name.lower())
    subtotal = sum(line.subtotal_paise for line in lines)
    tax = sum(line.tax_paise for line in lines)
    return PricedCart(lines, subtotal, tax, subtotal + tax, removed)


def _next_order_number(company_id):
    """Reserve the next order number for a company (ORD-000001, ...).

    The counter is bumped with an UPDATE before it is read. The UPDATE takes
    the company row's lock on Postgres and the database's write lock on
    SQLite, held until the order is committed, so a second checkout waits
    and then reads the new value; two orders can never get the same number.
    (Reading first under SELECT ... FOR UPDATE didn't hold on SQLite, which
    ignores FOR UPDATE, and the session could return a stale Company.)
    Numbers that are already taken, say by an order added by hand, are skipped.
    """
    for _ in range(MAX_NUMBER_ATTEMPTS):
        db.session.execute(
            db.update(Company)
            .where(Company.id == company_id)
            .values(next_order_seq=db.func.coalesce(Company.next_order_seq, 1) + 1)
            .execution_options(synchronize_session=False)
        )
        # Columns, not the Company object, so the identity map can't hand back an old value
        seq, prefix = db.session.execute(
            db.select(Company.next_order_seq, Company.order_prefix).where(Company.id == company_id)
        ).one()
        number = f"{prefix or 'ORD'}-{seq - 1:06d}"
        taken = db.session.scalar(db.select(Order.id).where(Order.company_id == company_id, Order.number == number))
        if taken is None:
            return number
    db.session.rollback()
    raise OrderError("We couldn't give this order a number. Please try again in a moment.")


def place_order(client, cart, notes=None, submission_key=None):
    """Create a pending order from a cart for a client.

    submission_key comes from the checkout form; submitting the same form
    twice returns the order created the first time.
    """
    if submission_key:
        existing = db.session.scalar(
            db.select(Order).where(Order.client_id == client.id, Order.submission_key == submission_key)
        )
        if existing:
            return existing, False

    priced = price_cart(client.company_id, cart)
    if priced.is_empty:
        raise OrderError("Your cart is empty. Add at least one product to place an order.")

    notes = (notes or "").strip() or None
    if notes and len(notes) > 2000:
        raise OrderError("Order notes can be at most 2,000 characters.")

    order = Order(
        company_id=client.company_id,
        client_id=client.id,
        number=_next_order_number(client.company_id),
        status=PENDING,
        notes=notes,
        subtotal_paise=priced.subtotal_paise,
        tax_paise=priced.tax_paise,
        total_paise=priced.total_paise,
        submission_key=submission_key,
        placed_at=utcnow(),
    )
    for line in priced.lines:
        product = line.product
        order.items.append(
            OrderItem(
                product_id=product.id,
                sku=product.sku,
                name=product.name,
                category=product.category,
                unit=product.unit,
                unit_price_paise=product.price_paise,
                gst_rate_bp=product.gst_rate_bp,
                quantity=line.quantity,
                line_subtotal_paise=line.subtotal_paise,
                line_tax_paise=line.tax_paise,
                line_total_paise=line.total_paise,
            )
        )
    order.events.append(OrderEvent(actor_id=client.id, from_status=None, to_status=PENDING))
    db.session.add(order)
    try:
        db.session.commit()
    except IntegrityError:
        # Same submission_key raced in from a double click: return the winner
        db.session.rollback()
        if submission_key:
            existing = db.session.scalar(
                db.select(Order).where(Order.client_id == client.id, Order.submission_key == submission_key)
            )
            if existing:
                return existing, False
        raise

    _notify_new_order(order)
    return order, True


def allowed_transitions(order, user):
    """Statuses this user may move the order to right now."""
    if user.company_id != order.company_id:
        return ()
    if user.is_staff:
        return STAFF_TRANSITIONS.get(order.status, ())
    if user.is_client and order.client_id == user.id:
        return CLIENT_TRANSITIONS.get(order.status, ())
    return ()


def transition_order(order, user, to_status, message=None):
    """Move an order to a new status, recording who did it and when."""
    if to_status not in allowed_transitions(order, user):
        current = STATUS_LABELS.get(order.status, order.status)
        raise OrderError(f"This order is {current.lower()}, so it can't be changed that way.")

    message = (message or "").strip() or None
    if to_status == REJECTED and not message:
        raise OrderError("Add a short reason so the client knows why the order was rejected.")
    if message and len(message) > 1000:
        raise OrderError("Keep the note under 1,000 characters.")

    now = utcnow()
    previous = order.status
    # Change the status only if it is still the one checked above, so two
    # people acting on the same order at once (approve and reject, or a client
    # cancelling as staff approve) can't both win. The views lock the row on
    # Postgres, but SQLite ignores FOR UPDATE and a caller may hold an old copy.
    changed = db.session.execute(
        db.update(Order)
        .where(Order.id == order.id, Order.status == previous)
        .values(status=to_status)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        db.session.rollback()
        raise OrderError("Someone else changed this order a moment ago. Check its status and try again.")
    order.status = to_status
    if to_status in (APPROVED, REJECTED):
        order.decided_at = now
        order.decided_by_id = user.id
        if to_status == REJECTED:
            order.rejection_reason = message
    elif to_status == FULFILLED:
        order.fulfilled_at = now
        order.fulfilled_by_id = user.id
    elif to_status == CANCELLED:
        order.cancelled_at = now

    order.events.append(
        OrderEvent(actor_id=user.id, from_status=previous, to_status=to_status, message=message, created_at=now)
    )
    db.session.commit()
    _notify_status_change(order, user, to_status, message)
    return order


def supplier_reply_to(company):
    """Where a client's reply to an order email goes: the supplier's support email, else its owner.

    Order emails are sent from the platform's address (MAIL_FROM), so without
    this a client's "Why was this rejected?" would reach the platform, not
    the supplier.
    """
    if company.support_email:
        return company.support_email
    return db.session.scalar(
        db.select(User.email)
        .where(User.company_id == company.id, User.role == OWNER, User.active.is_(True))
        .order_by(User.id)
        .limit(1)
    )


def _notify_new_order(order):
    client = order.client
    send_email(
        client.email,
        f"We received your order {order.number}",
        "order_placed_client",
        reply_to=supplier_reply_to(order.company),
        order=order,
        link=external_url("portal.order_detail", number=order.number),
    )
    recipients = staff_emails(order.company)
    if recipients:
        send_email(
            recipients,
            f"New order {order.number} from {client.buyer_company or client.full_name}",
            "order_placed_staff",
            reply_to=client.email,  # staff can answer the client straight from the email
            order=order,
            link=external_url("admin.order_detail", number=order.number),
        )


def _notify_status_change(order, actor, to_status, message):
    if actor.is_staff:
        send_email(
            order.client.email,
            f"Order {order.number} {TRANSITION_VERBS[to_status]}",
            "order_status_client",
            reply_to=supplier_reply_to(order.company),
            order=order,
            to_status=to_status,
            status_label=STATUS_LABELS[to_status],
            message=message,
            link=external_url("portal.order_detail", number=order.number),
        )
    else:
        recipients = staff_emails(order.company)
        if recipients:
            send_email(
                recipients,
                f"Order {order.number} was cancelled by the client",
                "order_cancelled_staff",
                reply_to=order.client.email,
                order=order,
                link=external_url("admin.order_detail", number=order.number),
            )
