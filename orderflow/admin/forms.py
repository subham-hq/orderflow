"""Forms and list helpers for the workspace pages (catalog, clients, team,
bulletins and settings).

Text is trimmed by filters, lengths match the database columns, and every
error message says what to do next. Values that need parsing (prices, GST
rates, SKUs) are parsed once during validation and left on the field as
`.parsed` / normalised `.data` for the view to save.
"""

import re

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import BooleanField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, StopValidation

from .. import money
from ..services import catalog

_CURRENCY_PREFIX = re.compile(r"^(?:₹|rs\.?|inr)\s*", re.IGNORECASE)


# Filters -------------------------------------------------------------------


def strip(value):
    return value.strip() if isinstance(value, str) else value


def squash(value):
    """Trim and collapse runs of whitespace (names, titles, categories)."""
    return " ".join(value.split()) if isinstance(value, str) else value


def strip_text(value):
    """Trim a multi-line value and use plain newlines."""
    if not isinstance(value, str):
        return value
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def price_text(value):
    """Allow a leading ₹, Rs or INR in price inputs."""
    if not isinstance(value, str):
        return value
    return _CURRENCY_PREFIX.sub("", value.strip()).strip()


# Validators ----------------------------------------------------------------


def _length(max_length, what="this"):
    return Length(max=max_length, message=f"Keep {what} to {max_length:,} characters or fewer.")


class Sku:
    """Normalise a SKU in place (see services.catalog.normalize_sku)."""

    def __call__(self, form, field):
        try:
            field.data = catalog.normalize_sku(field.data)
        except catalog.CatalogError as error:
            raise StopValidation(str(error)) from None


class Price:
    """Parse rupees into paise with orderflow.money; result in field.parsed."""

    def __call__(self, form, field):
        try:
            field.parsed = money.parse_price(field.data)
        except ValueError as error:
            raise StopValidation(str(error)) from None


class GstRate:
    """Accept only the rates offered in the select; result (basis points) in field.parsed."""

    def __call__(self, form, field):
        allowed = [value for value, _ in field.choices]
        if field.data not in allowed:
            labels = [f"{value}%" for value in allowed]
            listed = ", ".join(labels[:-1]) + f" or {labels[-1]}" if len(labels) > 1 else labels[0]
            raise StopValidation(f"Choose one of these GST rates: {listed}.")
        field.parsed = money.parse_gst_rate(field.data)


# Catalog -------------------------------------------------------------------


class ProductForm(FlaskForm):
    name = StringField(
        "Product name",
        filters=[squash],
        validators=[DataRequired("Enter the product name."), _length(160, "the name")],
    )
    sku = StringField(
        "SKU",
        filters=[strip],
        validators=[DataRequired("Enter a SKU (the code you use for this product)."), Sku()],
    )
    category = StringField("Category", filters=[squash], validators=[Optional(), _length(80, "the category")])
    unit = StringField("Unit", filters=[squash], validators=[Optional(), _length(20, "the unit")])
    price = StringField(
        "Price per unit (₹, before GST)",
        filters=[price_text],
        validators=[DataRequired("Enter a price, like 1250 or 1250.50."), Price()],
    )
    gst_rate = SelectField(
        "GST rate",
        choices=catalog.gst_choices(),
        validate_choice=False,
        validators=[DataRequired("Choose a GST rate (use 0% for exempt items)."), GstRate()],
    )
    description = TextAreaField(
        "Description", filters=[strip_text], validators=[Optional(), _length(2000, "the description")]
    )
    is_active = BooleanField("Show this product in the client catalog", default=True)

    def product_data(self, categories):
        """Validated values, ready for services.catalog.apply_product_data().

        categories is a services.catalog.Categories, so a new spelling of an
        existing category reuses the one already in the catalog.
        """
        return {
            "name": self.name.data,
            "sku": self.sku.data,
            "category": categories.canonical(self.category.data),
            "unit": self.unit.data or catalog.DEFAULT_UNIT,
            "price_paise": self.price.parsed,
            "gst_rate_bp": self.gst_rate.parsed,
            "description": self.description.data or None,
            "is_active": bool(self.is_active.data),
        }


def product_form_data(product):
    """Pre-fill values for editing a product, exact to the paisa."""
    return {
        "name": product.name,
        "sku": product.sku,
        "category": product.category or "",
        "unit": product.unit or catalog.DEFAULT_UNIT,
        "price": money.paise_input_value(product.price_paise),
        "gst_rate": money.rate_input_value(product.gst_rate_bp),
        "description": product.description or "",
        "is_active": bool(product.is_active),
    }


class ProductImportForm(FlaskForm):
    file = FileField(
        "CSV file",
        render_kw={"accept": ".csv,text/csv"},
        validators=[
            FileRequired("Choose a CSV file to upload."),
            FileAllowed(["csv"], "Upload a file that ends in .csv. In Excel, use File > Save As > CSV UTF-8."),
        ],
    )
