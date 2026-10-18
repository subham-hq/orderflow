import io

import pytest
from sqlalchemy.exc import IntegrityError

from orderflow.admin import catalog as catalog_views
from orderflow.extensions import db
from orderflow.models import Product
from orderflow.services import catalog
from tests import factories

HEADER = "sku,name,price,gst_percent,category,unit,description,active\n"


@pytest.fixture
def signed_in(login, owner):
    login(owner)
    return owner


def upload(client, content, filename="products.csv", follow=False):
    if isinstance(content, str):
        content = content.encode("utf-8")
    return client.post(
        "/app/products/import",
        data={"file": (io.BytesIO(content), filename)},
        content_type="multipart/form-data",
        follow_redirects=follow,
    )


def text(response):
    return response.get_data(as_text=True)


def all_products():
    return db.session.scalars(db.select(Product).order_by(Product.sku)).all()


def test_import_page_explains_the_format(client, signed_in):
    body = text(client.get("/app/products/import"))
    assert 'enctype="multipart/form-data"' in body
    assert "/app/products/import/template.csv" in body
    for column in catalog.CSV_COLUMNS:
        assert f">{column}</td>" in body
    assert "0%, 0.25%, 3%, 5%, 12%, 18%, 28%" in body
    assert 'accept=".csv,text/csv"' in body


def test_import_creates_and_updates_products(client, signed_in, company):
    existing = factories.make_product(company, sku="CTN-001", name="Old name", price_paise=100, category="Old")
    content = (
        HEADER
        + "ctn 001,Carton 5-ply,42.50,18,Packaging,box,Kraft,yes\n"
        + 'TAP-010,"BOPP tape, clear",68,18%,Packaging,roll,,\n'
        + 'rce-25k,Rice 25 kg,"1,450.00",5,Staples,bag,"Line one\nLine two",no\n'
    )
    response = upload(client, content)
    assert response.status_code == 302 and response.headers["Location"] == "/app/products"
    flash = text(client.get("/app/products"))
    assert "Import complete: added 2 products and updated 1 product." in flash

    products = {product.sku: product for product in all_products()}
    assert set(products) == {"CTN-001", "TAP-010", "RCE-25K"}
    carton = products["CTN-001"]
    assert carton.id == existing.id
    assert (carton.name, carton.price_paise, carton.gst_rate_bp, carton.category) == (
        "Carton 5-ply",
        4250,
        1800,
        "Packaging",
    )
    tape = products["TAP-010"]
    assert tape.name == "BOPP tape, clear" and tape.price_paise == 6800 and tape.is_active and tape.description is None
    rice = products["RCE-25K"]
    assert rice.price_paise == 145000 and rice.gst_rate_bp == 500 and not rice.is_active
    assert rice.description == "Line one\nLine two"


def test_any_invalid_row_means_nothing_is_imported(client, signed_in, company):
    factories.make_product(company, sku="KEEP-1", name="Keep me", price_paise=500)
    content = (
        HEADER
        + "KEEP-1,Changed,9,18,,,,\n"
        + "NEW-1,Fine,10,18,,,,\n"
        + "NEW-2,Bad price,-5,18,,,,\n"
        + "NEW-3,,abc,7,,,,maybe\n"
        + "NEW#4,Bad sku,10,18,,,,\n"
    )
    response = upload(client, content)
    body = text(response)
    assert response.status_code == 200
    assert "Nothing was imported" in body
    assert '<td class="num">4</td>' in body and "Price must be more than ₹0." in body
    assert "SKUs can only use letters A-Z" in body
    assert "Use yes or no." in body
    [product] = all_products()
    assert product.name == "Keep me" and product.price_paise == 500


def test_each_problem_names_its_row_and_column(client, signed_in):
    content = HEADER + "A-1,,10,18,,,,\nA-2,Ok,10,7,,,,\n"
    body = text(upload(client, content))
    assert "Enter the product name." in body and "Choose one of these GST rates" in body
    assert body.index('<td class="num">2</td>') < body.index('<td class="mono">name</td>')
    assert '<td class="mono">gst_percent</td>' in body
    assert "Fix the 2 problems below" in body


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("A-1,Item,inf,18,,,,", "Enter a number, like 1250 or 1250.50."),
        ("A-1,Item,nan,18,,,,", "Enter a number, like 1250 or 1250.50."),
        ("A-1,Item,1e400,18,,,,", "That amount is too large."),
        ("A-1,Item,0,18,,,,", "Price must be more than ₹0."),
        ("A-1,Item,,18,,,,", "Enter a price"),
        ("A-1,Item,10,,,,,", "Choose a GST rate"),
        ("A-1,Item,10,inf,,,,", "Choose one of these GST rates"),
        ("A-1,Item,10,-18,,,,", "Choose one of these GST rates"),
        (",Item,10,18,,,,", "Enter a SKU"),
        ("A-1," + "n" * 161 + ",10,18,,,,", "Keep the name to 160 characters or fewer."),
        ("A-1,Item,10,18," + "c" * 81 + ",,,", "Keep the category to 80 characters or fewer."),
        ("A-1,Item,10,18,," + "u" * 21 + ",,", "Keep the unit to 20 characters or fewer."),
    ],
)
def test_rows_use_the_same_rules_as_the_form(client, signed_in, row, message):
    response = upload(client, HEADER + row + "\n")
    assert response.status_code == 200
    assert message in text(response)
    assert all_products() == []


def test_exact_prices_from_csv(client, signed_in):
    upload(client, HEADER + "A-1,One,19.99,18,,,,\nA-2,Two,0.29,0.25,,,,\nA-3,Three,₹75,0,,,,\n")
    prices = {product.sku: (product.price_paise, product.gst_rate_bp) for product in all_products()}
    assert prices == {"A-1": (1999, 1800), "A-2": (29, 25), "A-3": (7500, 0)}


def test_duplicate_skus_in_one_file_are_rejected(client, signed_in):
    body = text(upload(client, HEADER + "A-1,One,10,18,,,,\na 1,Two,10,18,,,,\n"))
    assert "A-1 is also on row 2. Each SKU can appear only once." in body
    assert all_products() == []


def test_rows_with_extra_values_are_rejected(client, signed_in):
    body = text(upload(client, HEADER + "A-1,Tape, clear,10,18,Pack,box,desc,yes\n"))
    assert "more values than there are columns" in body and "Whole row" in body
    assert all_products() == []


def test_blank_lines_and_missing_optional_columns(client, signed_in, company):
    product = factories.make_product(
        company, sku="A-1", name="Old", category="Keep me", unit="kg", description="Keep", is_active=False
    )
    response = upload(client, "SKU, Name ,Price,GST Percent\n\n a-1 ,New name,10,5\n,,,\nB-2,Brand new,20,12\n")
    assert response.status_code == 302
    product = db.session.get(Product, product.id)
    assert (product.name, product.category, product.unit, product.description) == ("New name", "Keep me", "kg", "Keep")
    assert product.is_active is False and product.gst_rate_bp == 500
    new = catalog.find_product_by_sku(company.id, "B-2")
    assert new.unit == "unit" and new.category is None and new.is_active


def test_blank_active_keeps_status_and_words_change_it(client, signed_in, company):
    hidden = factories.make_product(company, sku="H-1", name="Hidden", is_active=False)
    shown = factories.make_product(company, sku="S-1", name="Shown")
    upload(client, HEADER + "H-1,Hidden,10,18,,,,\nS-1,Shown,10,18,,,,No\n")
    assert db.session.get(Product, hidden.id).is_active is False
    assert db.session.get(Product, shown.id).is_active is False
    upload(client, HEADER + "H-1,Hidden,10,18,,,,TRUE\n")
    assert db.session.get(Product, hidden.id).is_active is True


def test_category_spelling_is_reused(client, signed_in, company):
    factories.make_product(company, sku="X-1", category="Packaging")
    upload(client, HEADER + "A-1,One,10,18,packaging,,,\nA-2,Two,10,18,NEW cat,,,\nA-3,Three,10,18,new CAT,,,\n")
    categories = {product.sku: product.category for product in all_products()}
    assert categories["A-1"] == "Packaging"
    assert categories["A-2"] == categories["A-3"] == "NEW cat"


def test_export_then_import_changes_nothing(client, signed_in, company):
    factories.make_product(company, sku="-ODD", name="=SUM(1)", description="@note\nline 2", gst_rate_bp=150)
    factories.make_product(company, sku="TP-1", name="Tape", price_paise=1999, category="Packaging", is_active=False)
    exported = client.get("/app/products/export.csv").get_data()
    before = [(p.sku, p.name, p.description, p.price_paise, p.gst_rate_bp, p.is_active) for p in all_products()]
    response = upload(client, exported, follow=True)
    assert "Nothing to change: every product in the file already matches your catalog." in text(response)
    after = [(p.sku, p.name, p.description, p.price_paise, p.gst_rate_bp, p.is_active) for p in all_products()]
    assert before == after


def test_unchanged_rows_are_reported(client, signed_in, company):
    factories.make_product(company, sku="A-1", name="Same", price_paise=1000, gst_rate_bp=1800)
    response = upload(client, HEADER + "A-1,Same,10,18,,unit,,yes\nB-1,New,5,5,,,,\n", follow=True)
    assert "Import complete: added 1 product. 1 row already matched and was left as is." in text(response)


def test_non_standard_gst_only_allowed_for_products_that_already_have_it(client, signed_in, company):
    factories.make_product(company, sku="GC-1", name="Gold coin", gst_rate_bp=150)
    assert upload(client, HEADER + "GC-1,Gold coin,100,1.5,,,,\n").status_code == 302
    body = text(upload(client, HEADER + "NEW-1,Silver,100,1.5,,,,\n"))
    assert "Choose one of these GST rates" in body


def test_other_companies_are_untouched(client, signed_in, company, other_company):
    rival = other_company["product"]
    upload(client, HEADER + "rival-1,Mine now,10,18,,,,\n")
    rival = db.session.get(Product, rival.id)
    assert rival.name != "Mine now" and rival.company_id == other_company["company"].id
    mine = catalog.find_product_by_sku(company.id, "RIVAL-1")
    assert mine.name == "Mine now"


def test_bom_is_accepted(client, signed_in):
    assert upload(client, "\ufeff" + HEADER + "A-1,Café ₹ item,10,18,,,,\n").status_code == 302
    assert all_products()[0].name == "Café ₹ item"


def test_crlf_line_endings(client, signed_in):
    assert upload(client, HEADER.replace("\n", "\r\n") + "A-1,One,10,18,,,,\r\n").status_code == 302


def test_non_utf8_files_are_rejected(client, signed_in):
    body = text(upload(client, (HEADER + "A-1,Café,10,18,,,,\n").encode("latin-1")))
    assert "isn&#39;t saved as UTF-8" in body
    assert all_products() == []


def test_binary_files_are_rejected(client, signed_in):
    body = text(upload(client, b"sku,name\x00,price\n"))
    assert "doesn&#39;t look like a CSV file" in body


@pytest.mark.parametrize("filename", ["products.xlsx", "products.csv.exe", "products", "products.txt"])
def test_only_csv_files_are_accepted(client, signed_in, filename):
    body = text(upload(client, HEADER + "A-1,One,10,18,,,,\n", filename=filename))
    assert "Upload a file that ends in .csv" in body
    assert all_products() == []


def test_uppercase_extension_is_fine(client, signed_in):
    assert upload(client, HEADER + "A-1,One,10,18,,,,\n", filename="PRODUCTS.CSV").status_code == 302


def test_a_file_is_required(client, signed_in):
    response = client.post("/app/products/import", data={}, content_type="multipart/form-data")
    assert "Choose a CSV file to upload." in text(response)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "That file is empty."),
        ("   \n\n", "That file is empty."),
        (HEADER, "This file has a header but no products."),
        (HEADER + ",,,,,,,\n", "This file has a header but no products."),
        ("sku,name,price\nA-1,One,10\n", "missing the column gst_percent"),
        ("sku,name\nA-1,One\n", "missing the columns price, gst_percent"),
        ("sku,name,price,gst_percent,categry\nA-1,One,10,18,X\n", "We don&#39;t recognise the column “categry”"),
        ("sku,name,price,gst_percent,SKU\nA-1,One,10,18,A\n", "The column “sku” appears more than once"),
        ("sku,name,price,gst_percent,\nA-1,One,10,18,oops\n", "Column 5 has values but no header"),
        ('sku,name,price,gst_percent\n"A-1,One,10,18\n', "We couldn&#39;t read this CSV file"),
        ('sku,name,price,gst_percent\n"A-1"x,One,10,18\n', "We couldn&#39;t read this CSV file"),
    ],
)
def test_file_level_problems(client, signed_in, content, message):
    response = upload(client, content)
    assert response.status_code == 200
    assert message in text(response)
    assert all_products() == []


def test_empty_trailing_header_columns_are_ignored(client, signed_in):
    assert upload(client, "sku,name,price,gst_percent,,\nA-1,One,10,18,,\n").status_code == 302


def test_unreadable_csv(client, signed_in):
    huge_cell = "x" * 200_000
    body = text(upload(client, HEADER + f'A-1,"{huge_cell}",10,18,,,,\n'))
    assert "We couldn&#39;t read this CSV file" in body


def test_too_many_rows(client, signed_in, monkeypatch):
    monkeypatch.setattr(catalog, "MAX_IMPORT_ROWS", 2)
    body = text(upload(client, HEADER + "A-1,One,10,18,,,,\nA-2,Two,10,18,,,,\nA-3,Three,10,18,,,,\n"))
    assert "This file has 3 products. Import up to 2 at a time" in body


def test_long_error_lists_are_capped(client, signed_in, monkeypatch):
    monkeypatch.setattr(catalog_views, "MAX_LISTED_IMPORT_ERRORS", 2)
    body = text(upload(client, HEADER + "".join(f"A-{n},,10,18,,,,\n" for n in range(5))))
    assert "Fix the 5 problems below" in body and "Showing the first 2 of 5 problems." in body


def test_files_over_1_mb_are_explained_on_the_import_page(client, signed_in):
    big = HEADER + "A-1,One,10,18,,,," + " " * (1024 * 1024) + "\n"
    response = upload(client, big)
    assert response.status_code == 303 and response.headers["Location"] == "/app/products/import"
    assert "larger than 1 MB" in text(client.get("/app/products/import"))
    assert all_products() == []


def test_oversized_posts_elsewhere_get_the_normal_error_page(client, signed_in):
    response = client.post("/app/products/new", data={"name": "x" * (1024 * 1024 + 10)})
    assert response.status_code == 413
    assert "That upload is too large" in text(response)


def test_oversized_upload_while_signed_out_is_not_processed(client, company):
    response = upload(client, "x" * (1024 * 1024 + 10))
    assert response.status_code == 413


def test_a_conflicting_change_during_import_rolls_back(client, signed_in, monkeypatch):
    def conflict(plan):
        raise IntegrityError("INSERT", {}, Exception("duplicate"))

    monkeypatch.setattr(catalog_views, "_apply_import", conflict)
    body = text(upload(client, HEADER + "A-1,One,10,18,,,,\n"))
    assert "Nothing was imported; upload the file again." in body
    assert all_products() == []


def test_clients_cannot_import(client, login, buyer):
    login(buyer)
    response = upload(client, HEADER + "A-1,One,10,18,,,,\n")
    assert response.headers["Location"] == "/portal/"
    assert all_products() == []
