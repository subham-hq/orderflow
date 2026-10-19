"""Catalog rules shared by the product pages and CSV import/export.

SKUs are normalised the same way everywhere (form, import, lookups), so
"ctn 001", " CTN-001 " and "Ctn-001" are one product code. Prices and GST
rates go through orderflow.money, never floats.
"""

import csv
import io
import re
from dataclasses import dataclass, field

from sqlalchemy import func

from .. import money
from ..extensions import db
from ..models import Product

SKU_MAX_LENGTH = 64
_SKU_ALLOWED = re.compile(r"[A-Z0-9_./-]+")

# Suggestions for the unit field; any short text is accepted
COMMON_UNITS = (
    "unit",
    "piece",
    "box",
    "pack",
    "kg",
    "g",
    "litre",
    "ml",
    "metre",
    "roll",
    "bag",
    "carton",
    "dozen",
    "set",
)
DEFAULT_UNIT = "unit"

# CSV layout used by the template, the export and the import
CSV_COLUMNS = ("sku", "name", "price", "gst_percent", "category", "unit", "description", "active")
REQUIRED_CSV_COLUMNS = ("sku", "name", "price", "gst_percent")
MAX_IMPORT_ROWS = 5000
# The longest value a cell may hold. Descriptions allow 2,000 characters; a
# much longer value is nearly always a missing closing quote swallowing rows.
MAX_CSV_CELL_LENGTH = 5000

# Cells starting with these are treated as formulas by spreadsheet apps
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

_TRUE_WORDS = {"yes", "y", "true", "1", "active", "visible", "shown"}
_FALSE_WORDS = {"no", "n", "false", "0", "hidden", "inactive"}


class CatalogError(ValueError):
    """A problem with catalog input, with a message safe to show the user."""


def normalize_sku(value):
    """Return the canonical form of a SKU or raise CatalogError.

    Trims, upper-cases and turns runs of whitespace into a single "-".
    Only A-Z, 0-9 and - _ . / are allowed, up to 64 characters.
    """
    text = "-".join(str(value or "").split()).upper()
    if not text:
        raise CatalogError("Enter a SKU (the code you use for this product).")
    if not _SKU_ALLOWED.fullmatch(text):
        raise CatalogError("SKUs can only use letters A-Z, numbers 0-9 and the symbols - _ . /")
    if len(text) > SKU_MAX_LENGTH:
        raise CatalogError(f"Keep the SKU to {SKU_MAX_LENGTH} characters or fewer.")
    return text


def gst_choices(current_bp=None):
    """(value, label) pairs for the GST select. A product's current rate is
    kept as an option even when it isn't one of the standard slabs."""
    values = list(money.GST_SLABS)
    if current_bp is not None:
        current = money.rate_input_value(current_bp)
        if current not in values:
            values.append(current)
    values.sort(key=lambda value: money.parse_gst_rate(value))
    return [(value, f"{value}%") for value in values]


def category_options(company_id):
    """The company's existing categories, sorted for the category suggestions."""
    rows = db.session.scalars(
        db.select(Product.category)
        .where(Product.company_id == company_id, Product.category.is_not(None), Product.category != "")
        .distinct()
    )
    return sorted(set(rows), key=lambda name: (name.lower(), name))


class Categories:
    """A company's category names, used to reuse their spelling.

    "packaging" typed for a new product becomes the existing "Packaging".
    A name that already exists exactly as typed is always kept, so saving a
    product never quietly moves it into a similarly spelled category.
    """

    def __init__(self, names=()):
        self.exact = set()
        self.by_lower = {}
        for name in names:
            self.add(name)

    @classmethod
    def for_company(cls, company_id):
        return cls(category_options(company_id))

    def add(self, name):
        self.exact.add(name)
        self.by_lower.setdefault(name.lower(), name)

    def canonical(self, value):
        if not value:
            return None
        if value in self.exact:
            return value
        return self.by_lower.get(value.lower(), value)


def find_product_by_sku(company_id, sku):
    """The company's product with this SKU (compared case-insensitively), or None."""
    return db.session.scalars(
        db.select(Product)
        .where(Product.company_id == company_id, func.upper(Product.sku) == sku.upper())
        .order_by(Product.id)
        .limit(1)
    ).first()


def apply_product_data(product, data):
    """Copy validated values onto a product. Returns True if anything changed."""
    changed = False
    for name, value in data.items():
        if getattr(product, name) != value:
            setattr(product, name, value)
            changed = True
    return changed


def parse_active(value):
    """Read the CSV "active" column: True, False, or None when blank."""
    text = (value or "").strip().lower()
    if not text:
        return None
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    raise CatalogError("Use yes or no.")


# CSV ---------------------------------------------------------------------


def csv_safe(value):
    """Neutralise cells a spreadsheet would run as a formula (CSV injection)."""
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _unescape_cell(text):
    """Undo csv_safe() so an exported file imports back unchanged."""
    if len(text) >= 2 and text[0] == "'" and text[1] in "".join(_FORMULA_PREFIXES):
        return text[1:]
    return text


def _csv_text(rows):
    buffer = io.StringIO()
    buffer.write("\ufeff")  # BOM, so Excel opens the file as UTF-8 (₹, Hindi names)
    writer = csv.writer(buffer, lineterminator="\r\n")
    for row in rows:
        writer.writerow([csv_safe(cell) for cell in row])
    return buffer.getvalue()


def product_csv_row(product):
    return (
        product.sku,
        product.name,
        money.paise_input_value(product.price_paise),
        money.rate_input_value(product.gst_rate_bp),
        product.category or "",
        product.unit or DEFAULT_UNIT,
        product.description or "",
        "yes" if product.is_active else "no",
    )


def products_csv(products):
    """The catalog as CSV text (with a UTF-8 BOM), in the import layout."""
    return _csv_text([CSV_COLUMNS, *(product_csv_row(product) for product in products)])


def template_csv():
    """A small example file people can fill in and import."""
    return _csv_text(
        [
            CSV_COLUMNS,
            (
                "CTN-001",
                "Corrugated carton, 5-ply, 18 x 12 x 12 in",
                "42.50",
                "18",
                "Packaging",
                "box",
                "Brown kraft, 5-ply",
                "yes",
            ),
            ("RCE-25K", "Sona Masoori rice, 25 kg", "1450", "5", "Staples", "bag", "", "yes"),
        ]
    )


@dataclass
class CsvRow:
    number: int  # the row number a spreadsheet shows (the header is row 1)
    values: dict = field(default_factory=dict)
    error: str | None = None


def _header_name(cell):
    return "_".join(cell.strip().lower().split())


def read_products_csv(data):
    """Decode and split an uploaded CSV into rows keyed by column name.

    Raises CatalogError for problems with the file as a whole. Problems with
    a single row are left on CsvRow.error so they can be listed together.
    """
    try:
        text = (data or b"").decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CatalogError(
            "We couldn't read this file because it isn't saved as UTF-8. In Excel, choose "
            "File > Save As > CSV UTF-8 (Comma delimited). In Google Sheets, use File > Download > "
            "Comma-separated values. Then upload it again."
        ) from None
    if not text.strip():
        raise CatalogError("That file is empty. Add a header row and one product per row.")
    if "\x00" in text:
        raise CatalogError(
            "This doesn't look like a CSV file (it may be an Excel workbook renamed to .csv). "
            "Save it as CSV UTF-8 and upload it again."
        )

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = [_header_name(cell) for cell in next(reader, [])]
        _check_cell_lengths(header, 1)
        used, unnamed = _check_header(header)
        names = [name for _, name in used]

        # Rows are read one at a time, and only the first MAX_IMPORT_ROWS are
        # kept: past that the rest are just counted for the message, so a
        # file of millions of tiny rows can't fill the server's memory.
        rows, product_count = [], 0
        for index, cells in enumerate(reader, start=2):
            if not any(cell.strip() for cell in cells):
                continue  # blank lines are skipped
            product_count += 1
            if product_count > MAX_IMPORT_ROWS:
                continue
            _check_cell_lengths(cells, index)
            for position in unnamed:
                if position < len(cells) and cells[position].strip():
                    raise CatalogError(
                        f"Column {position + 1} has values but no header. "
                        f"Give it one of these names: {', '.join(CSV_COLUMNS)}."
                    )
            rows.append(_csv_row(index, cells, header, used))
    except csv.Error as error:
        if "field larger than field limit" in str(error):
            raise _long_value_error("one value in this file") from None
        raise CatalogError(
            f"We couldn't read this CSV file ({error}). Check that it opens in a spreadsheet app."
        ) from None

    if product_count > MAX_IMPORT_ROWS:
        raise CatalogError(
            f"This file has {product_count:,} products. Import up to {MAX_IMPORT_ROWS:,} at a time by "
            "splitting it into smaller files."
        )
    if not rows:
        raise CatalogError("This file has a header but no products. Add one product per row below the header.")
    return names, rows


def _long_value_error(where):
    return CatalogError(
        f"We couldn't read this CSV file: {where} is longer than {MAX_CSV_CELL_LENGTH:,} characters. "
        "Descriptions can be up to 2,000 characters. If a value starts with a double quote, check that it "
        "ends with one too, then upload the file again."
    )


def _check_cell_lengths(cells, number):
    """Stop at the first value far longer than any column allows (usually an unclosed quote)."""
    if any(len(cell) > MAX_CSV_CELL_LENGTH for cell in cells):
        raise _long_value_error(f"a value on row {number:,}")


def _shown_name(name, limit=40):
    """A column name for a message, cut short if someone pasted a paragraph into the header."""
    return name if len(name) <= limit else name[: limit - 1] + "…"


def _check_header(header):
    """Check the header row's names. Returns (used, unnamed): the (position,
    name) of each named column and the positions of columns with no name."""
    used = [(position, name) for position, name in enumerate(header) if name]
    unnamed = [position for position, name in enumerate(header) if not name]
    names = [name for _, name in used]
    for name in names:
        if names.count(name) > 1:
            raise CatalogError(f"The column “{_shown_name(name)}” appears more than once. Keep one and try again.")
    unknown = [name for name in names if name not in CSV_COLUMNS]
    if unknown:
        raise CatalogError(
            f"We don't recognise the column “{_shown_name(unknown[0])}”. "
            f"Use these column names: {', '.join(CSV_COLUMNS)}."
        )
    missing = [name for name in REQUIRED_CSV_COLUMNS if name not in names]
    if missing:
        raise CatalogError(
            f"This file is missing the {'column' if len(missing) == 1 else 'columns'} {', '.join(missing)}. "
            f"The first row must be a header like: {','.join(CSV_COLUMNS)}"
        )
    return used, unnamed


def _csv_row(number, cells, header, used):
    row = CsvRow(number=number)
    extra = cells[len(header) :]
    if any(cell.strip() for cell in extra):
        row.error = (
            "This row has more values than there are columns. If a value contains a comma, "
            "put the whole value in double quotes."
        )
    for position, name in used:
        cell = cells[position] if position < len(cells) else ""
        row.values[name] = _unescape_cell(cell.strip())
    return row
