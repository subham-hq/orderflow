"""Forms and list helpers for the workspace pages (catalog, clients, team,
bulletins and settings).

Text is trimmed by filters, lengths match the database columns, and every
error message says what to do next. Values that need parsing (prices, GST
rates, SKUs) are parsed once during validation and left on the field as
`.parsed` / normalised `.data` for the view to save.
"""

import re
from datetime import timedelta

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import BooleanField, EmailField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional, StopValidation, ValidationError

from .. import money, timeutil
from ..extensions import db
from ..services import catalog

PER_PAGE = 25
SEARCH_MAX_LENGTH = 100
BULLETIN_MAX_YEARS_AHEAD = 5

_CURRENCY_PREFIX = re.compile(r"^(?:₹|rs\.?|inr)\s*", re.IGNORECASE)
_PHONE_CHARS = re.compile(r"[0-9+()\-. ]+")


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


def lower(value):
    """Emails: trimmed and lower-cased, like services.accounts.normalize_email."""
    return value.strip().lower() if isinstance(value, str) else value


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


class Phone:
    """A phone number in any common format, with 6 to 15 digits."""

    def __call__(self, form, field):
        value = field.data or ""
        digits = sum(char.isdigit() for char in value)
        if value and (not _PHONE_CHARS.fullmatch(value) or not 6 <= digits <= 15):
            raise ValidationError("Enter a phone number using digits, spaces and + - ( ), like +91 98450 12345.")


def _email_validators(required=True, what="an email address"):
    validators = [DataRequired(f"Enter {what}.")] if required else [Optional()]
    return validators + [
        Length(max=254, message="Keep the email address to 254 characters or fewer."),
        Email("Enter a valid email address, like name@business.in."),
    ]


def _first_name(what="their"):
    return StringField(
        "First name", filters=[squash], validators=[DataRequired(f"Enter {what} first name."), _length(80, "the name")]
    )


def _last_name():
    return StringField("Last name", filters=[squash], validators=[Optional(), _length(80, "the name")])


def _phone():
    return StringField("Phone", filters=[squash], validators=[Optional(), _length(32, "the phone number"), Phone()])


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


# Clients ---------------------------------------------------------------------


class ClientForm(FlaskForm):
    """Invite a client, or edit one (the view removes email once they've joined)."""

    first_name = _first_name()
    last_name = _last_name()
    buyer_company = StringField(
        "Business name",
        filters=[squash],
        validators=[DataRequired("Enter the name of the business they buy for."), _length(160, "the business name")],
    )
    email = EmailField("Email", filters=[lower], validators=_email_validators(what="their email address"))
    phone = _phone()


# Team ------------------------------------------------------------------------


class TeamInviteForm(FlaskForm):
    first_name = _first_name()
    last_name = _last_name()
    email = EmailField("Work email", filters=[lower], validators=_email_validators(what="their work email address"))


# Bulletins -------------------------------------------------------------------


class BulletinForm(FlaskForm):
    title = StringField(
        "Title",
        filters=[squash],
        validators=[DataRequired("Give the bulletin a title."), _length(160, "the title")],
    )
    body = TextAreaField(
        "Message",
        filters=[strip_text],
        validators=[DataRequired("Write the message your clients will see."), _length(4000, "the message")],
    )
    publish = BooleanField("Publish it so clients can see it", default=True)
    expires_at = StringField("Take it down at (optional)", filters=[strip])

    def __init__(self, *args, timezone=timeutil.DEFAULT_TZ, current_expiry=None, **kwargs):
        super().__init__(*args, **kwargs)
        # The company time zone the expiry is typed in, and the saved expiry
        # when editing (the input only shows whole minutes)
        self.timezone = timezone
        self.current_expiry = timeutil.as_utc(current_expiry)

    def validate_expires_at(self, field):
        """Parse the expiry in the company's time zone; result (UTC) in field.parsed."""
        field.parsed = None
        if not field.data:
            return
        try:
            expires = timeutil.parse_local_datetime(field.data, self.timezone)
        except (ValueError, OverflowError):
            raise StopValidation("Enter a date and time, like 31 Oct 2026, 6:00 PM.") from None
        if self.current_expiry and expires == self.current_expiry.replace(second=0, microsecond=0):
            expires = self.current_expiry  # unchanged: keep the exact saved time
        now = timeutil.utcnow()
        if expires > now + timedelta(days=365 * BULLETIN_MAX_YEARS_AHEAD):
            raise StopValidation(
                f"Choose a time within the next {BULLETIN_MAX_YEARS_AHEAD} years, or leave it blank to keep it up."
            )
        if self.publish.data and expires <= now:
            raise StopValidation(
                "That time has already passed. Choose a time in the future, or leave it blank to keep "
                "the bulletin up until you take it down."
            )
        field.parsed = expires


# List helpers ----------------------------------------------------------------


def search_text(value):
    """A search box value, trimmed and capped."""
    return " ".join(str(value or "").split())[:SEARCH_MAX_LENGTH]


def like_pattern(text):
    """A LIKE pattern matching text anywhere, with % and _ taken literally."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def page_number(value):
    try:
        number = int(str(value or "1").strip())
    except ValueError:
        return 1
    return max(1, min(number, 100_000))


def paginate(stmt, page, per_page=PER_PAGE):
    """Paginate a select; a page past the end shows the last page."""
    result = db.paginate(stmt, page=page, per_page=per_page, error_out=False)
    if not result.items and page > 1 and result.pages:
        result = db.paginate(stmt, page=result.pages, per_page=per_page, error_out=False)
    return result
