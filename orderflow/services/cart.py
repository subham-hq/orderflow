"""The client's cart, kept in the signed session cookie.

session["cart"] maps product ids (as strings) to whole quantities, for
example {"12": 5, "40": 1}. The portal layout sums the values for the cart
badge, so the format stays this simple.

The cart never stores prices. Every page that shows or orders the cart
prices it again from the live catalog (services.orders.price_cart), and only
active products of the client's own company may be in it.
"""

import hashlib
import json
from dataclasses import dataclass, field

from flask import session

from ..extensions import db
from ..models import Product
from ..money import MAX_QUANTITY

CART_KEY = "cart"

# Distinct products one cart can hold. Keeps the session cookie well under
# the 4 KB browsers allow, so a big cart can never sign anyone out.
MAX_CART_LINES = 100

# Largest id a Postgres integer column can hold
MAX_ID = 2_147_483_647


class CartError(ValueError):
    """A problem with the cart the client can fix, with a safe message."""


@dataclass
class SyncResult:
    """What sync_cart() took out of the cart."""

    removed_names: list[str] = field(default_factory=list)  # products that were hidden
    unknown_count: int = 0  # products that no longer exist at all

    @property
    def changed(self):
        return bool(self.removed_names or self.unknown_count)


def parse_id(value):
    """Parse a product id from a form or URL. Returns an int, or None if it isn't a valid id."""
    text = str(value if value is not None else "").strip()
    if not text or len(text) > 10 or not text.isascii() or not text.isdigit():
        return None
    number = int(text)
    if not 1 <= number <= MAX_ID:
        return None
    return number


def sanitize(raw):
    """Return a clean copy of a stored cart, dropping anything malformed."""
    if not isinstance(raw, dict):
        return {}
    cart = {}
    for key, quantity in raw.items():
        product_id = parse_id(key)
        if product_id is None or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            continue
        cart[str(product_id)] = min(quantity, MAX_QUANTITY)
        if len(cart) >= MAX_CART_LINES:
            break
    return cart


def get_cart():
    """The current session's cart as {"<product_id>": quantity}."""
    return sanitize(session.get(CART_KEY))


def save_cart(cart):
    cart = sanitize(cart)
    if cart:
        session[CART_KEY] = cart
    else:
        session.pop(CART_KEY, None)


def clear_cart():
    session.pop(CART_KEY, None)


def quantities_by_product(cart):
    """{product_id (int): quantity} for templates."""
    return {int(key): quantity for key, quantity in cart.items()}


def add_quantity(cart, product_id, quantity):
    """Add quantity of a product to a cart dict, capped at MAX_QUANTITY.

    Returns (new_quantity, capped). Raises CartError when the cart already
    holds MAX_CART_LINES different products.
    """
    key = str(product_id)
    if key not in cart and len(cart) >= MAX_CART_LINES:
        raise CartError(
            f"Your cart can hold up to {MAX_CART_LINES} different products. "
            "Place this order first, then start another one."
        )
    total = cart.get(key, 0) + quantity
    cart[key] = min(total, MAX_QUANTITY)
    return cart[key], total > MAX_QUANTITY


def active_products(company_id, product_ids):
    """Active products of the company among product_ids, as {id: Product}."""
    ids = [int(product_id) for product_id in product_ids]
    if not ids:
        return {}
    rows = db.session.scalars(
        db.select(Product).where(
            Product.company_id == company_id,
            Product.id.in_(ids),
            Product.is_active.is_(True),
        )
    )
    return {product.id: product for product in rows}


def sync_cart(company_id):
    """Drop products that are no longer orderable from the session cart.

    Returns a SyncResult describing what was taken out, so the page can tell
    the client.
    """
    raw = session.get(CART_KEY)
    if raw is None:
        return SyncResult()
    cart = sanitize(raw)
    result = SyncResult()
    if cart:
        rows = db.session.execute(
            db.select(Product.id, Product.name, Product.is_active).where(
                Product.company_id == company_id,
                Product.id.in_([int(key) for key in cart]),
            )
        ).all()
        found = {row.id: row for row in rows}
        for key in list(cart):
            row = found.get(int(key))
            if row is None:
                result.unknown_count += 1
                del cart[key]
            elif not row.is_active:
                result.removed_names.append(row.name)
                del cart[key]
    if cart != raw:
        save_cart(cart)
    return result


def cart_version(priced):
    """A fingerprint of a priced cart: changes whenever a line, quantity or price does.

    The cart page puts it in the checkout form, so an order is only placed
    for exactly what the client saw.
    """
    lines = sorted(
        (
            line.product.id,
            line.quantity,
            line.product.price_paise,
            line.product.gst_rate_bp,
            line.product.sku,
            line.product.name,
            line.product.unit,
        )
        for line in priced.lines
    )
    return hashlib.sha256(json.dumps(lines).encode()).hexdigest()[:32]


def join_names(names, limit=5):
    """["A", "B", "C"] -> "A, B and C" (long lists end with "and 3 more")."""
    names = list(names)
    if len(names) > limit:
        shown, rest = names[:limit], len(names) - limit
        return f"{', '.join(shown)} and {rest} more"
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def sync_message(result):
    """A plain sentence telling the client what sync_cart() removed, or None."""
    parts = []
    if result.removed_names:
        names = join_names(result.removed_names)
        verb = "is" if len(result.removed_names) == 1 else "are"
        pronoun = "it" if len(result.removed_names) == 1 else "them"
        parts.append(f"{names} {verb} no longer available, so we took {pronoun} out of your cart.")
    if result.unknown_count:
        if result.unknown_count == 1:
            parts.append("An item in your cart is no longer in the catalog, so we took it out.")
        else:
            parts.append(
                f"{result.unknown_count} items in your cart are no longer in the catalog, so we took them out."
            )
    return " ".join(parts) or None
