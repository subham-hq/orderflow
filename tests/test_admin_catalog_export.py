import csv
import io

import pytest

from orderflow.services import catalog
from tests import factories


@pytest.fixture
def signed_in(login, owner):
    login(owner)
    return owner


def read_csv(response):
    raw = response.get_data()
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV files start with a UTF-8 BOM so Excel reads them correctly"
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))


@pytest.mark.parametrize("path", ["/app/products/export.csv", "/app/products/import/template.csv"])
def test_downloads_need_a_staff_sign_in(client, login, company, buyer, path):
    factories.make_product(company, name="Secret price list")
    response = client.get(path)
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    login(buyer)
    response = client.get(path)
    assert response.status_code == 302 and response.headers["Location"] == "/portal/"


def test_admins_can_export(client, login, company, admin):
    factories.make_product(company, sku="TP-1")
    login(admin)
    assert read_csv(client.get("/app/products/export.csv"))[1][0] == "TP-1"


def test_export_lists_every_product_in_the_import_layout(client, signed_in, company, other_company):
    factories.make_product(
        company,
        name="Tape",
        sku="TP-1",
        price_paise=1999,
        gst_rate_bp=25,
        category="Packaging",
        unit="roll",
        description="Clear\nstrong",
    )
    factories.make_product(company, name="Hidden film", sku="HF-1", price_paise=100000, is_active=False)
    factories.make_product(company, name="Rice ₹ अनाज", sku="RC-1", price_paise=29, gst_rate_bp=500)
    response = client.get("/app/products/export.csv")
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert response.headers["Content-Disposition"].startswith('attachment; filename="products-')
    assert response.headers["Cache-Control"] == "no-store"
    rows = read_csv(response)
    assert rows[0] == list(catalog.CSV_COLUMNS)
    assert rows[0] == ["sku", "name", "price", "gst_percent", "category", "unit", "description", "active"]
    assert rows[1] == ["HF-1", "Hidden film", "1000.00", "18", "", "unit", "", "no"]
    assert rows[2] == ["RC-1", "Rice ₹ अनाज", "0.29", "5", "", "unit", "", "yes"]
    assert rows[3] == ["TP-1", "Tape", "19.99", "0.25", "Packaging", "roll", "Clear\nstrong", "yes"]
    assert len(rows) == 4  # nothing from the other company


def test_export_filename_uses_the_company_date(client, signed_in, company, monkeypatch):
    from datetime import UTC, datetime

    from orderflow import timeutil

    # 20:00 UTC on 18 Oct is already 19 Oct in India
    monkeypatch.setattr(timeutil, "utcnow", lambda: datetime(2026, 10, 18, 20, 0, tzinfo=UTC))
    response = client.get("/app/products/export.csv")
    assert 'filename="products-2026-10-19.csv"' in response.headers["Content-Disposition"]


def test_export_neutralises_spreadsheet_formulas(client, signed_in, company):
    factories.make_product(company, name='=HYPERLINK("http://evil")', sku="A-1", description="@SUM(1)")
    factories.make_product(company, name="+cmd", sku="-B", category="\tTab", description="\rCR")
    rows = read_csv(client.get("/app/products/export.csv"))
    assert rows[1][1] == "'+cmd" and rows[1][0] == "'-B" and rows[1][4] == "'\tTab" and rows[1][6] == "'\rCR"
    assert rows[2][1] == '\'=HYPERLINK("http://evil")' and rows[2][6] == "'@SUM(1)"


def test_export_of_an_empty_catalog_is_just_the_header(client, signed_in):
    assert read_csv(client.get("/app/products/export.csv")) == [list(catalog.CSV_COLUMNS)]


def test_import_template_is_a_valid_example(client, signed_in):
    response = client.get("/app/products/import/template.csv")
    assert response.status_code == 200 and response.mimetype == "text/csv"
    assert 'filename="orderflow-products-template.csv"' in response.headers["Content-Disposition"]
    rows = read_csv(response)
    assert rows[0] == list(catalog.CSV_COLUMNS) and len(rows) == 3
    assert rows[1][0] == "CTN-001" and rows[1][2] == "42.50" and rows[1][3] == "18"
