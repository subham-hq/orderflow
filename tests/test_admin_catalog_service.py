import csv
import io

import pytest

from orderflow import money
from orderflow.services import catalog
from tests import factories


def test_normalize_sku_rules():
    assert catalog.normalize_sku("  ab  cd ef ") == "AB-CD-EF"
    assert catalog.normalize_sku("ctn\t001") == "CTN-001"
    assert catalog.normalize_sku("a/b.c_d-1") == "A/B.C_D-1"
    assert catalog.normalize_sku("x" * 64) == "X" * 64


@pytest.mark.parametrize("bad", ["", "   ", None, "AB#", "١٢٣", "ＣＴＮ１", "ÇTN", "x" * 65, "A,B"])
def test_normalize_sku_rejects_bad_codes(bad):
    with pytest.raises(catalog.CatalogError):
        catalog.normalize_sku(bad)


def test_gst_choices_are_the_standard_slabs_with_percent_labels():
    choices = catalog.gst_choices()
    assert [value for value, _ in choices] == list(money.GST_SLABS)
    assert ("18", "18%") in choices and ("0.25", "0.25%") in choices


def test_gst_choices_keep_a_custom_current_rate_in_order():
    values = [value for value, _ in catalog.gst_choices(150)]
    assert values == ["0", "0.25", "1.5", "3", "5", "12", "18", "28"]
    assert [value for value, _ in catalog.gst_choices(1800)] == list(money.GST_SLABS)


def test_parse_active_words():
    assert catalog.parse_active(" YES ") is True and catalog.parse_active("hidden") is False
    assert catalog.parse_active("") is None and catalog.parse_active(None) is None
    with pytest.raises(catalog.CatalogError):
        catalog.parse_active("maybe")


def test_category_options_are_per_company_and_sorted(app, company, other_company):
    factories.make_product(company, category="safety")
    factories.make_product(company, category="Packaging")
    factories.make_product(company, category="Packaging")
    factories.make_product(company, category=None)
    factories.make_product(company, category="")
    factories.make_product(other_company["company"], category="Rival only")
    assert catalog.category_options(company.id) == ["Packaging", "safety"]


def test_categories_reuse_spelling_but_keep_exact_matches():
    categories = catalog.Categories(["Packaging"])
    assert categories.canonical("packaging") == "Packaging"
    assert categories.canonical("PACKAGING") == "Packaging"
    assert categories.canonical("Packaging") == "Packaging"
    assert categories.canonical("New one") == "New one"
    assert categories.canonical("") is None and categories.canonical(None) is None
    categories.add("New one")
    assert categories.canonical("new ONE") == "New one"


def test_categories_keep_a_legacy_spelling_that_exists_exactly(app, company):
    # Old data can hold two spellings; saving a product unchanged keeps its own
    factories.make_product(company, category="packaging")
    factories.make_product(company, category="Packaging")
    categories = catalog.Categories.for_company(company.id)
    assert categories.canonical("Packaging") == "Packaging"
    assert categories.canonical("packaging") == "packaging"
    assert categories.canonical("PACKAGING") == "Packaging"


def test_find_product_by_sku_ignores_case_and_company(app, company, other_company):
    product = factories.make_product(company, sku="old-sku")
    assert catalog.find_product_by_sku(company.id, "OLD-SKU").id == product.id
    assert catalog.find_product_by_sku(company.id, "RIVAL-1") is None
    assert catalog.find_product_by_sku(other_company["company"].id, "rival-1").id == other_company["product"].id


def test_apply_product_data_reports_changes(app, company):
    product = factories.make_product(company, name="Tape", price_paise=100)
    assert catalog.apply_product_data(product, {"name": "Tape", "price_paise": 100}) is False
    assert catalog.apply_product_data(product, {"name": "Tape", "price_paise": 101}) is True
    assert product.price_paise == 101


@pytest.mark.parametrize("value", ["=1+1", "+91", "-5", "@SUM(A1)", "\tx", "\rx"])
def test_csv_safe_neutralises_formulas(value):
    assert catalog.csv_safe(value) == "'" + value


@pytest.mark.parametrize(("value", "expected"), [("Tape", "Tape"), (None, ""), (12, "12"), ("a=b", "a=b")])
def test_csv_safe_leaves_other_values(value, expected):
    assert catalog.csv_safe(value) == expected


def test_products_csv_has_bom_header_and_crlf(app, company):
    product = factories.make_product(company, sku="A-1", name="Tape", price_paise=1999, gst_rate_bp=25)
    text = catalog.products_csv([product])
    assert text.startswith("\ufeff")
    assert text.endswith("\r\n")
    rows = list(csv.reader(io.StringIO(text[1:], newline="")))
    assert rows == [list(catalog.CSV_COLUMNS), ["A-1", "Tape", "19.99", "0.25", "", "unit", "", "yes"]]


def test_template_reads_back_cleanly():
    names, rows = catalog.read_products_csv(catalog.template_csv().encode("utf-8"))
    assert names == list(catalog.CSV_COLUMNS)
    assert [row.values["sku"] for row in rows] == ["CTN-001", "RCE-25K"]
    assert rows[0].number == 2 and rows[0].error is None


def test_read_products_csv_normalises_headers_and_skips_blank_lines():
    names, rows = catalog.read_products_csv(b"SKU , Name,Price,GST  percent\n\n A-1 ,One,10,18\n , , , \n")
    assert names == ["sku", "name", "price", "gst_percent"]
    assert len(rows) == 1 and rows[0].number == 3
    assert rows[0].values == {"sku": "A-1", "name": "One", "price": "10", "gst_percent": "18"}


def test_read_products_csv_undoes_formula_escaping():
    _, rows = catalog.read_products_csv(b"sku,name,price,gst_percent\n'-B,'=SUM(1),10,18\n")
    assert rows[0].values["sku"] == "-B" and rows[0].values["name"] == "=SUM(1)"


def test_read_products_csv_flags_rows_with_extra_values():
    _, rows = catalog.read_products_csv(b"sku,name,price,gst_percent\nA-1,One,10,18,extra\nA-2,Two,10,18,\n")
    assert "more values than there are columns" in rows[0].error
    assert rows[1].error is None


def test_short_rows_fill_missing_cells_with_blanks():
    _, rows = catalog.read_products_csv(b"sku,name,price,gst_percent,category\nA-1,One\n")
    assert rows[0].values == {"sku": "A-1", "name": "One", "price": "", "gst_percent": "", "category": ""}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "That file is empty."),
        (None, "That file is empty."),
        (b"\xef\xbb\xbf\r\n  ", "That file is empty."),
        (b"sku,name,price,gst_percent\n", "header but no products"),
        (b"\xff\xfes\x00k\x00u\x00", "isn't saved as UTF-8"),
        (b"sku,name,price,gst_percent\nA\x00,B,1,2\n", "doesn't look like a CSV file"),
    ],
)
def test_read_products_csv_file_problems(data, message):
    with pytest.raises(catalog.CatalogError, match=message):
        catalog.read_products_csv(data)


def test_read_products_csv_row_limit(monkeypatch):
    monkeypatch.setattr(catalog, "MAX_IMPORT_ROWS", 1)
    with pytest.raises(catalog.CatalogError, match="Import up to 1 at a time"):
        catalog.read_products_csv(b"sku,name,price,gst_percent\nA-1,One,1,0\nA-2,Two,1,0\n")
