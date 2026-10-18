"""Forms used in the client portal."""

import re

from flask_wtf import FlaskForm
from wtforms import HiddenField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from ..money import MAX_QUANTITY
from ..services.cart import parse_id
from ..services.orders import OrderError, parse_quantity

NOTES_MAX_LENGTH = 2000
# secrets.token_urlsafe(16) makes 22 characters; the column holds 64
SUBMISSION_KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,64}")
# A phone number: an optional +, then ASCII digits with spaces, brackets, dots or dashes between them
PHONE_PATTERN = re.compile(r"\+?[0-9(][0-9 ().-]*[0-9]")
PHONE_DIGITS = (7, 15)  # 15 is the longest number the international format allows


def strip(value):
    return value.strip() if isinstance(value, str) else value


def normalize_newlines(value):
    """Browsers send line breaks in a textarea as \\r\\n; keep them as \\n."""
    return value.replace("\r\n", "\n").replace("\r", "\n") if isinstance(value, str) else value


def read_quantity(value):
    """parse_quantity() that also refuses absurdly long input with a clear message.

    (int() refuses strings of more than 4,300 digits with a plain ValueError,
    which would otherwise surface as a server error.)
    """
    text = str(value if value is not None else "").strip()
    if len(text) > 12:
        if text.isascii() and text.isdigit():
            raise OrderError(f"The most you can order of one item is {MAX_QUANTITY:,}.")
        raise OrderError("Quantities must be whole numbers.")
    return parse_quantity(text)


class AddToCartForm(FlaskForm):
    """The add-to-cart form on each catalog product (rendered by hand, one per card)."""

    product_id = StringField(filters=[strip])
    quantity = StringField(filters=[strip])
    next = HiddenField()

    def validate_product_id(self, field):
        self.product_id_value = parse_id(field.data)
        if self.product_id_value is None:
            raise ValidationError("That product isn't available any more.")

    def validate_quantity(self, field):
        try:
            self.quantity_value = read_quantity(field.data)
        except OrderError as error:
            raise ValidationError(str(error)) from None
        if self.quantity_value == 0:
            raise ValidationError("Enter a quantity of 1 or more.")


class CheckoutForm(FlaskForm):
    """Order notes, plus the hidden fields that make placing an order safe to submit twice."""

    notes = TextAreaField(
        "Notes for the supplier",
        filters=[normalize_newlines, strip],
        validators=[
            Optional(),
            Length(max=NOTES_MAX_LENGTH, message=f"Keep notes to {NOTES_MAX_LENGTH:,} characters or fewer."),
        ],
    )
    # A one-time key made when the cart page is shown: submitting the same page twice returns the same order
    submission_key = HiddenField()
    # A fingerprint of the cart as shown, so the order is placed only for exactly what the client saw
    cart_version = HiddenField()

    def validate_submission_key(self, field):
        if not field.data or not SUBMISSION_KEY_PATTERN.fullmatch(field.data):
            raise ValidationError("This page has expired. Check your cart and place the order again.")


class AccountForm(FlaskForm):
    """The client's own contact details. Their email and business name are managed by the supplier."""

    first_name = StringField(
        "First name",
        filters=[strip],
        validators=[
            DataRequired(message="Enter your first name."),
            Length(max=80, message="Keep your first name to 80 characters or fewer."),
        ],
    )
    last_name = StringField(
        "Last name",
        filters=[strip],
        validators=[Optional(), Length(max=80, message="Keep your last name to 80 characters or fewer.")],
    )
    phone = StringField(
        "Phone",
        filters=[strip],
        validators=[Optional(), Length(max=32, message="Keep the phone number to 32 characters or fewer.")],
    )

    def validate_phone(self, field):
        digits = sum(character in "0123456789" for character in field.data)
        low, high = PHONE_DIGITS
        if not PHONE_PATTERN.fullmatch(field.data) or not low <= digits <= high:
            raise ValidationError(f"Enter a phone number with {low} to {high} digits, like +91 98765 43210.")
