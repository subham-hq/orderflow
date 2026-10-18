"""Forms used in the client portal."""

from flask_wtf import FlaskForm
from wtforms import HiddenField, StringField
from wtforms.validators import ValidationError

from ..money import MAX_QUANTITY
from ..services.cart import parse_id
from ..services.orders import OrderError, parse_quantity


def strip(value):
    return value.strip() if isinstance(value, str) else value


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
