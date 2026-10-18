"""Money and GST helpers.

All amounts are integers in paise. Conversion from user input uses Decimal
and rounds half up to the nearest paisa; floats are never used for money.
"""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Commas are only accepted where they group digits: the Indian way
# (1,23,45,678.50) or the international way (12,345,678.50). Anywhere else a
# comma is most likely a decimal comma or a typo ("12,50"), and dropping it
# would store a price 100 times too high.
GROUPED_NUMBER = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})(?:\.\d*)?", re.ASCII)

# Largest price accepted from a form: 1 crore rupees
MAX_PRICE_PAISE = 10_000_000_00
# Largest quantity on one order line
MAX_QUANTITY = 100_000
# GST slabs offered in the product form (percent)
GST_SLABS = ("0", "0.25", "3", "5", "12", "18", "28")


def _decimal(value):
    if isinstance(value, Decimal):
        amount = value
    else:
        text = str(value).strip() if value is not None else ""
        if not text:
            raise ValueError("Enter an amount.")
        if "," in text:
            if not GROUPED_NUMBER.fullmatch(text):
                raise ValueError("Use a dot for decimals and commas only between groups of digits, like 1,250.50.")
            text = text.replace(",", "")
        try:
            amount = Decimal(text)
        except InvalidOperation:
            raise ValueError("Enter a number, like 1250 or 1250.50.") from None
    if not amount.is_finite():
        raise ValueError("Enter a number, like 1250 or 1250.50.")
    if abs(amount) >= Decimal(10) ** 15:
        raise ValueError("That amount is too large.")
    return amount


def to_paise(value):
    """Convert a rupee amount ("1,250.50", "19.99", Decimal) to integer paise."""
    amount = _decimal(value)
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def parse_price(value):
    """Parse a product price from a form. Returns paise or raises ValueError."""
    paise = to_paise(value)
    if paise <= 0:
        raise ValueError("Price must be more than ₹0.")
    if paise > MAX_PRICE_PAISE:
        raise ValueError("Price must be ₹1,00,00,000 or less.")
    return paise


def parse_gst_rate(value):
    """Parse a GST rate in percent ("18", "0.25") to basis points."""
    text = "0" if value is None or str(value).strip() == "" else value
    rate = _decimal(text)
    if rate < 0 or rate > 100:
        raise ValueError("GST rate must be between 0% and 100%.")
    bp = (rate * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(bp)


def gst_amount(subtotal_paise, rate_bp):
    """GST on a line, rounded half up to the nearest paisa."""
    tax = Decimal(subtotal_paise) * Decimal(rate_bp) / Decimal(10000)
    return int(tax.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def rate_label(rate_bp):
    """1800 -> "18%", 25 -> "0.25%"."""
    percent = Decimal(rate_bp or 0) / Decimal(100)
    text = format(percent.normalize(), "f")
    return f"{text}%"


def rate_input_value(rate_bp):
    """1800 -> "18" for pre-filling a form."""
    return rate_label(rate_bp).rstrip("%")


def _group_indian(digits):
    """Group an integer string the Indian way: 12345678 -> 1,23,45,678."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def format_inr(paise, symbol=True, decimals=True):
    """Format paise as Indian rupees: 123456789 -> "₹12,34,567.89"."""
    if paise is None:
        paise = 0
    paise = int(paise)
    sign = "-" if paise < 0 else ""
    rupees, rem = divmod(abs(paise), 100)
    text = _group_indian(str(rupees))
    if decimals:
        text = f"{text}.{rem:02d}"
    return f"{sign}{'₹' if symbol else ''}{text}"


_COMPACT_UNITS = ((Decimal(10_000_000), "Cr"), (Decimal(100_000), "L"), (Decimal(1000), "K"))


def _compact_unit(rupees):
    """The (size, suffix) unit for an amount: 1 crore and up is Cr, and so on down to plain rupees."""
    return next(((size, suffix) for size, suffix in _COMPACT_UNITS if rupees >= size), (Decimal(1), ""))


def format_inr_compact(paise):
    """Short form for charts and stat tiles: ₹999, ₹4.2K, ₹3.1L, ₹1.3Cr, ₹2,56,000Cr."""
    rupees = abs(Decimal(int(paise or 0)) / 100)
    sign = "-" if paise and int(paise) < 0 else ""

    # Round in the unit the amount falls in, then choose the unit again, so
    # ₹99,950 rounds up to ₹1L rather than showing as ₹100K
    size, suffix = _compact_unit(rupees)
    step = Decimal("0.1") if suffix else Decimal(1)
    rupees = (rupees / size).quantize(step, rounding=ROUND_HALF_UP) * size
    size, suffix = _compact_unit(rupees)

    value = format((rupees / size).normalize(), "f")
    whole, _, fraction = value.partition(".")
    text = _group_indian(whole) + (f".{fraction}" if fraction else "")
    return f"{sign}₹{text}{suffix}"


def paise_input_value(paise):
    """Paise -> "1250.50" for pre-filling a price input."""
    if paise is None:
        return ""
    rupees, rem = divmod(int(paise), 100)
    return f"{rupees}.{rem:02d}"
