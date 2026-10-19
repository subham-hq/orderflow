import pytest
from werkzeug.datastructures import MultiDict

from orderflow.extensions import db
from orderflow.models import OrderItem, Product
from orderflow.services import catalog
from orderflow.services.orders import place_order
from tests import factories

NEW = {
    "name": "Corrugated carton, 5-ply",
    "sku": "ctn-001",
    "category": "Packaging",
    "unit": "box",
    "price": "42.50",
    "gst_rate": "18",
    "description": "Brown kraft",
    "is_active": "y",
}


@pytest.fixture
def signed_in(login, owner):
    login(owner)
    return owner


def products():
    return db.session.scalars(db.select(Product).order_by(Product.id)).all()


def text(response):
    return response.get_data(as_text=True)


# Access ----------------------------------------------------------------------

ENDPOINTS = [
    ("get", "/app/products/new"),
    ("post", "/app/products/new"),
    ("get", "/app/products/{id}/edit"),
    ("post", "/app/products/{id}/edit"),
    ("post", "/app/products/{id}/status"),
]


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_anonymous_users_are_sent_to_sign_in(client, company, method, path):
    product = factories.make_product(company, name="Untouched")
    response = getattr(client, method)(path.format(id=product.id), data={**NEW, "active": "0"})
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")
    product = db.session.get(Product, product.id)
    assert product.is_active and product.name == "Untouched" and len(products()) == 1


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_clients_are_sent_back_to_their_portal(client, login, company, buyer, method, path):
    product = factories.make_product(company, name="Untouched")
    login(buyer)
    response = getattr(client, method)(path.format(id=product.id), data={**NEW, "active": "0"})
    assert response.status_code == 302
    assert response.headers["Location"] == "/portal/"
    product = db.session.get(Product, product.id)
    assert product.is_active and product.name == "Untouched" and len(products()) == 1


def test_admins_can_add_and_edit_products(client, login, company, admin):
    login(admin)
    assert client.get("/app/products/new").status_code == 200
    assert client.post("/app/products/new", data=NEW).status_code == 302
    product = products()[0]
    assert product.sku == "CTN-001" and product.company_id == company.id
    response = client.post(f"/app/products/{product.id}/edit", data={**NEW, "name": "Renamed"})
    assert response.status_code == 302
    assert db.session.get(Product, product.id).name == "Renamed"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/app/products/{id}/edit"),
        ("post", "/app/products/{id}/edit"),
        ("post", "/app/products/{id}/status"),
    ],
)
def test_products_of_other_companies_are_not_found(client, signed_in, other_company, method, path):
    rival = other_company["product"]
    response = getattr(client, method)(path.format(id=rival.id), data={**NEW, "active": "0"})
    assert response.status_code == 404
    rival = db.session.get(Product, rival.id)
    assert rival.is_active and rival.sku == "RIVAL-1"


def test_unknown_product_ids_are_not_found(client, signed_in):
    assert client.get("/app/products/999999/edit").status_code == 404
    assert client.get("/app/products/abc/edit").status_code == 404
    assert client.post("/app/products/999999/status", data={"active": "0"}).status_code == 404


# New product -------------------------------------------------------------------


def test_add_product_saves_exact_values(client, signed_in, company):
    response = client.post("/app/products/new", data={**NEW, "sku": "  ctn   001 ", "price": "₹1,250.50"})
    assert response.status_code == 302 and response.headers["Location"] == "/app/products"
    [product] = products()
    assert product.company_id == company.id
    assert product.sku == "CTN-001"
    assert product.price_paise == 125050
    assert product.gst_rate_bp == 1800
    assert product.unit == "box" and product.category == "Packaging" and product.is_active
    assert product.description == "Brown kraft"
    page = text(client.get("/app/products"))
    assert "Added “Corrugated carton, 5-ply” to your catalog. Clients can order it now." in page


def test_new_form_defaults(client, signed_in, company):
    factories.make_product(company, category="Fasteners")
    body = text(client.get("/app/products/new"))
    assert '<option selected value="18">18%</option>' in body
    assert '<option value="0.25">0.25%</option>' in body
    assert 'value="unit"' in body and '<option value="Fasteners">' in body
    assert 'list="category-options"' in body and 'list="unit-options"' in body
    for unit in catalog.COMMON_UNITS:
        assert f'<option value="{unit}">' in body
    assert 'id="is_active" name="is_active" type="checkbox" value="y"' in body and "checked" in body
    assert "None" not in body


def test_new_form_only_suggests_this_companys_categories(client, signed_in, other_company):
    factories.make_product(other_company["company"], category="Rival secret category")
    assert "Rival secret category" not in text(client.get("/app/products/new"))


def test_unit_defaults_and_category_is_optional(client, signed_in):
    client.post("/app/products/new", data={**NEW, "unit": "  ", "category": "", "description": "  "})
    [product] = products()
    assert product.unit == "unit" and product.category is None and product.description is None


def test_text_is_trimmed_and_spaces_collapsed(client, signed_in):
    client.post(
        "/app/products/new", data={**NEW, "name": "  Carton   box ", "category": " Pack   aging ", "unit": " kg "}
    )
    [product] = products()
    assert (product.name, product.category, product.unit) == ("Carton box", "Pack aging", "kg")


def test_description_keeps_line_breaks(client, signed_in):
    client.post("/app/products/new", data={**NEW, "description": "  Line one\r\nLine two  "})
    assert products()[0].description == "Line one\nLine two"


def test_unchecked_active_box_adds_a_hidden_product(client, signed_in):
    data = {key: value for key, value in NEW.items() if key != "is_active"}
    client.post("/app/products/new", data=data)
    assert products()[0].is_active is False
    assert "hidden from clients for now" in text(client.get("/app/products"))


def test_add_and_start_another(client, signed_in):
    response = client.post("/app/products/new", data={**NEW, "next_action": "another"})
    assert response.headers["Location"] == "/app/products/new"


def test_category_reuses_existing_spelling(client, signed_in, company):
    factories.make_product(company, category="Packaging Material")
    client.post("/app/products/new", data={**NEW, "category": "  packaging   material "})
    assert products()[-1].category == "Packaging Material"


@pytest.mark.parametrize(
    ("price", "paise"),
    [
        ("19.99", 1999),
        ("0.29", 29),
        ("1,250.50", 125050),
        ("Rs. 75", 7500),
        ("INR 10", 1000),
        ("₹ 99", 9900),
        ("0.005", 1),
        ("10000000", 10_000_000_00),
    ],
)
def test_prices_are_parsed_exactly(client, signed_in, price, paise):
    client.post("/app/products/new", data={**NEW, "price": price})
    assert products()[0].price_paise == paise


@pytest.mark.parametrize(
    ("price", "message"),
    [
        ("0", "Price must be more than ₹0."),
        ("0.00", "Price must be more than ₹0."),
        ("0.004", "Price must be more than ₹0."),
        ("-5", "Price must be more than ₹0."),
        ("inf", "Enter a number, like 1250 or 1250.50."),
        ("nan", "Enter a number, like 1250 or 1250.50."),
        ("-inf", "Enter a number, like 1250 or 1250.50."),
        ("Infinity", "Enter a number, like 1250 or 1250.50."),
        ("1e400", "That amount is too large."),
        ("9" * 400, "That amount is too large."),
        ("abc", "Enter a number, like 1250 or 1250.50."),
        ("12.5.0", "Enter a number, like 1250 or 1250.50."),
        ("10000000.01", "Price must be ₹1,00,00,000 or less."),
        ("", "Enter a price, like 1250 or 1250.50."),
        ("   ", "Enter a price, like 1250 or 1250.50."),
    ],
)
def test_invalid_prices_are_rejected_with_a_clear_message(client, signed_in, price, message):
    response = client.post("/app/products/new", data={**NEW, "price": price})
    assert response.status_code == 200
    body = text(response)
    assert message in body
    assert 'id="price-error"' in body
    assert products() == []


@pytest.mark.parametrize(("rate", "bp"), [("0", 0), ("0.25", 25), ("3", 300), ("5", 500), ("12", 1200), ("28", 2800)])
def test_gst_slabs(client, signed_in, rate, bp):
    client.post("/app/products/new", data={**NEW, "gst_rate": rate})
    assert products()[0].gst_rate_bp == bp


@pytest.mark.parametrize("rate", ["7", "inf", "nan", "-5", "18.0", "", "1e400", "18%", "101"])
def test_gst_rates_outside_the_list_are_rejected(client, signed_in, rate):
    response = client.post("/app/products/new", data={**NEW, "gst_rate": rate})
    assert response.status_code == 200
    assert "GST rate" in text(response)
    assert products() == []


def test_missing_gst_field_is_rejected(client, signed_in):
    data = {key: value for key, value in NEW.items() if key != "gst_rate"}
    assert "Choose a GST rate" in text(client.post("/app/products/new", data=data))
    assert products() == []


def test_unexpected_or_repeated_form_keys_do_not_break_the_form(client, signed_in):
    data = {**NEW, "price[]": "1", "gst_rate[0]": "x", "unknown": "1", "is_active": "garbage"}
    response = client.post("/app/products/new", data=data)
    assert response.status_code == 302
    product = products()[0]
    assert product.price_paise == 4250 and product.is_active
    # A repeated field uses its first value
    repeated = MultiDict([*NEW.items(), ("price", "99")])
    repeated["sku"] = "CTN-002"
    assert client.post("/app/products/new", data=repeated).status_code == 302
    assert products()[1].price_paise == 4250


def test_nul_bytes_are_refused(client, signed_in):
    assert client.post("/app/products/new", data={**NEW, "name": "Bad\x00name"}).status_code == 400
    assert products() == []


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"name": "   "}, "Enter the product name."),
        ({"name": "x" * 161}, "Keep the name to 160 characters or fewer."),
        ({"sku": ""}, "Enter a SKU"),
        ({"sku": "CTN#1"}, "SKUs can only use letters A-Z"),
        ({"sku": "ÇTN-1"}, "SKUs can only use letters A-Z"),
        ({"sku": "ＣＴＮ１"}, "SKUs can only use letters A-Z"),
        ({"sku": "A" * 65}, "Keep the SKU to 64 characters or fewer."),
        ({"category": "c" * 81}, "Keep the category to 80 characters or fewer."),
        ({"unit": "u" * 21}, "Keep the unit to 20 characters or fewer."),
        ({"description": "d" * 2001}, "Keep the description to 2,000 characters or fewer."),
    ],
)
def test_validation_errors_keep_what_was_typed(client, signed_in, changes, message):
    data = {**NEW, "name": "Typed name to keep", **changes}
    response = client.post("/app/products/new", data=data)
    body = text(response)
    assert response.status_code == 200
    assert message in body
    assert 'class="field__error"' in body and 'aria-invalid="true"' in body
    assert 'value="42.50"' in body
    for name, typed in (("name", "Typed name to keep"), ("description", "Brown kraft")):
        if name not in changes:
            assert typed in body
    assert products() == []


def test_longest_allowed_values_are_saved(client, signed_in):
    data = {
        **NEW,
        "name": "n" * 160,
        "sku": "S" * 64,
        "category": "c" * 80,
        "unit": "u" * 20,
        "description": "d" * 2000,
    }
    assert client.post("/app/products/new", data=data).status_code == 302
    assert len(products()[0].sku) == 64


def test_allowed_sku_symbols(client, signed_in):
    client.post("/app/products/new", data={**NEW, "sku": "a/b.c_d-1"})
    assert products()[0].sku == "A/B.C_D-1"


@pytest.mark.parametrize("variant", ["CTN-001", "ctn-001", " Ctn 001 ", "ctn\t001", "CTN  001"])
def test_duplicate_skus_are_caught_whatever_the_case_or_spacing(client, signed_in, company, variant):
    factories.make_product(company, sku="CTN-001", name="Existing carton")
    response = client.post("/app/products/new", data={**NEW, "sku": variant})
    assert response.status_code == 200
    assert "CTN-001 is already the SKU for “Existing carton”" in text(response)
    assert len(products()) == 1


def test_duplicate_check_ignores_case_in_stored_skus(client, signed_in, company):
    factories.make_product(company, sku="old-sku", name="Legacy item")
    response = client.post("/app/products/new", data={**NEW, "sku": "OLD-SKU"})
    assert "already the SKU for “Legacy item”" in text(response)


def test_same_sku_in_another_company_is_fine(client, signed_in, other_company):
    client.post("/app/products/new", data={**NEW, "sku": "rival-1"})
    assert len(products()) == 2


def test_sku_race_is_reported_on_the_field(client, signed_in, company, monkeypatch):
    factories.make_product(company, sku="CTN-001")
    monkeypatch.setattr(catalog, "find_product_by_sku", lambda company_id, sku: None)
    response = client.post("/app/products/new", data=NEW)
    assert response.status_code == 200
    body = text(response)
    assert "CTN-001 was just used for another product" in body
    assert "Corrugated carton, 5-ply" in body  # what they typed is still there
    assert len(products()) == 1


# Edit --------------------------------------------------------------------------


def test_edit_form_is_prefilled_exactly(client, signed_in, company):
    product = factories.make_product(
        company, name="Rag", sku="RAG-1", price_paise=1999, gst_rate_bp=500, category="odd / Legacy cat", unit="kg"
    )
    body = text(client.get(f"/app/products/{product.id}/edit"))
    assert 'value="19.99"' in body
    assert '<option selected value="5">5%</option>' in body
    assert 'value="odd / Legacy cat"' in body and 'value="kg"' in body
    assert "None" not in body


def test_edit_page_shows_details_and_order_count(client, signed_in, company):
    product = factories.make_product(company, name="Rag", sku="RAG-1")
    body = text(client.get(f"/app/products/{product.id}/edit"))
    assert "RAG-1" in body and "0 lines" in body and "Hide from clients" in body


def test_repeated_saves_keep_price_and_category(client, signed_in, company):
    product = factories.make_product(company, price_paise=29, gst_rate_bp=1800, category="Misc (old)")
    for _ in range(3):
        page = text(client.get(f"/app/products/{product.id}/edit"))
        assert 'value="0.29"' in page and 'value="Misc (old)"' in page
        client.post(
            f"/app/products/{product.id}/edit",
            data={**NEW, "sku": product.sku, "name": "Renamed", "price": "0.29", "category": "Misc (old)"},
        )
    product = db.session.get(Product, product.id)
    assert product.price_paise == 29 and product.category == "Misc (old)" and product.name == "Renamed"


def test_saving_unchanged_keeps_a_legacy_category_spelling(client, signed_in, company):
    factories.make_product(company, category="Packaging")
    legacy = factories.make_product(company, name="Old", sku="OLD-1", price_paise=100, category="packaging")
    data = {**NEW, "name": "Old", "sku": "OLD-1", "price": "1.00", "category": "packaging", "description": ""}
    response = client.post(f"/app/products/{legacy.id}/edit", data={**data, "unit": "unit"}, follow_redirects=True)
    assert "No changes to save for “Old”." in text(response)
    assert db.session.get(Product, legacy.id).category == "packaging"


def test_unusual_gst_rate_is_kept_on_edit(client, signed_in, company):
    product = factories.make_product(company, gst_rate_bp=150, name="Gold coin", sku="GC-1")
    body = text(client.get(f"/app/products/{product.id}/edit"))
    assert '<option selected value="1.5">1.5%</option>' in body
    client.post(
        f"/app/products/{product.id}/edit",
        data={**NEW, "sku": "GC-1", "name": "Gold coin", "gst_rate": "1.5", "price": "100.00"},
    )
    assert db.session.get(Product, product.id).gst_rate_bp == 150
    # but other products can't pick it
    response = client.post("/app/products/new", data={**NEW, "gst_rate": "1.5"})
    assert "Choose one of these GST rates" in text(response)


def test_saving_without_changes_says_so(client, signed_in, company):
    product = factories.make_product(company, name="Tape", sku="TP-1", price_paise=4250, unit="roll")
    data = {
        "name": "Tape",
        "sku": "tp-1",
        "category": "",
        "unit": "roll",
        "price": "42.5",
        "gst_rate": "18",
        "description": "",
        "is_active": "y",
    }
    response = client.post(f"/app/products/{product.id}/edit", data=data, follow_redirects=True)
    assert "No changes to save for “Tape”." in text(response)
    assert "Saved" not in text(response)
    response = client.post(f"/app/products/{product.id}/edit", data={**data, "price": "43"}, follow_redirects=True)
    assert "Saved “Tape”." in text(response)
    assert db.session.get(Product, product.id).price_paise == 4300


def test_edit_can_hide_a_product(client, signed_in, company):
    product = factories.make_product(company, name="Tape", sku="TP-1")
    data = {key: value for key, value in NEW.items() if key != "is_active"}
    client.post(f"/app/products/{product.id}/edit", data={**data, "sku": "TP-1"})
    assert db.session.get(Product, product.id).is_active is False


def test_edit_rejects_another_products_sku(client, signed_in, company):
    factories.make_product(company, sku="A-1", name="First")
    second = factories.make_product(company, sku="B-1", name="Second")
    response = client.post(f"/app/products/{second.id}/edit", data={**NEW, "sku": "a 1"})
    assert "A-1 is already the SKU for “First”" in text(response)
    assert db.session.get(Product, second.id).sku == "B-1"


def test_edit_keeps_its_own_sku_in_any_case(client, signed_in, company):
    product = factories.make_product(company, sku="B-1", name="Second")
    response = client.post(f"/app/products/{product.id}/edit", data={**NEW, "sku": "b 1"})
    assert response.status_code == 302
    assert db.session.get(Product, product.id).sku == "B-1"


def test_edit_sku_race_is_reported(client, signed_in, company, monkeypatch):
    factories.make_product(company, sku="A-1")
    second = factories.make_product(company, sku="B-1", name="Second")
    monkeypatch.setattr(catalog, "find_product_by_sku", lambda company_id, sku: None)
    response = client.post(f"/app/products/{second.id}/edit", data={**NEW, "sku": "A-1"})
    assert response.status_code == 200
    body = text(response)
    assert "A-1 was just used for another product" in body and "Second" in body
    assert db.session.get(Product, second.id).sku == "B-1"


def test_edit_validation_error_keeps_input(client, signed_in, company):
    product = factories.make_product(company, name="Tape")
    response = client.post(f"/app/products/{product.id}/edit", data={**NEW, "name": "New tape name", "price": "-1"})
    body = text(response)
    assert "Price must be more than ₹0." in body and "New tape name" in body and 'value="-1"' in body
    assert db.session.get(Product, product.id).name == "Tape"


def test_editing_a_product_never_changes_past_orders(client, signed_in, company, buyer):
    product = factories.make_product(company, name="Tape", sku="TP-1", price_paise=5000, gst_rate_bp=1800)
    order, _ = place_order(buyer, {product.id: 3})
    client.post(
        f"/app/products/{product.id}/edit",
        data={**NEW, "name": "Tape v2", "sku": "TP-2", "price": "99", "gst_rate": "5", "category": "New"},
    )
    item = db.session.scalar(db.select(OrderItem).where(OrderItem.order_id == order.id))
    assert (item.name, item.sku, item.unit_price_paise, item.gst_rate_bp) == ("Tape", "TP-1", 5000, 1800)
    assert item.line_total_paise == 17700
    assert "1 line" in text(client.get(f"/app/products/{product.id}/edit"))


# Show / hide -------------------------------------------------------------------


def test_hide_and_show_are_explicit_and_idempotent(client, signed_in, company):
    product = factories.make_product(company, name="Tape")
    url = f"/app/products/{product.id}/status"

    response = client.post(url, data={"active": "0"}, follow_redirects=True)
    assert "“Tape” is hidden." in text(response)
    assert db.session.get(Product, product.id).is_active is False

    # A double click sends the same request again: it must not flip back
    response = client.post(url, data={"active": "0"}, follow_redirects=True)
    assert "“Tape” is already hidden from clients." in text(response)
    assert db.session.get(Product, product.id).is_active is False

    response = client.post(url, data={"active": "1"}, follow_redirects=True)
    assert "“Tape” is back in the client catalog." in text(response)
    assert db.session.get(Product, product.id).is_active is True
    response = client.post(url, data={"active": "1"}, follow_redirects=True)
    assert "already visible to clients" in text(response)
    assert db.session.get(Product, product.id).is_active is True


@pytest.mark.parametrize("data", [{}, {"active": "2"}, {"active": "yes"}, {"active": ""}, {"active": "١"}])
def test_status_needs_an_explicit_target(client, signed_in, company, data):
    product = factories.make_product(company)
    assert client.post(f"/app/products/{product.id}/status", data=data).status_code == 400
    assert db.session.get(Product, product.id).is_active


def test_status_change_requires_post(client, signed_in, company):
    product = factories.make_product(company)
    assert client.get(f"/app/products/{product.id}/status?active=0").status_code == 405
    assert db.session.get(Product, product.id).is_active


def test_status_returns_to_a_safe_next_page(client, signed_in, company):
    product = factories.make_product(company)
    url = f"/app/products/{product.id}/status"
    response = client.post(url, data={"active": "0", "next": "/app/products?q=tape&page=2"})
    assert response.headers["Location"] == "/app/products?q=tape&page=2"
    response = client.post(url, data={"active": "1", "next": "https://evil.example/"})
    assert response.headers["Location"] == "/app/products"
    response = client.post(url, data={"active": "0", "next": "//evil.example/"})
    assert response.headers["Location"] == "/app/products"


def test_hidden_product_edit_page_offers_to_show_it(client, signed_in, company):
    product = factories.make_product(company, is_active=False)
    body = text(client.get(f"/app/products/{product.id}/edit"))
    assert "Show to clients" in body and 'name="active" value="1"' in body
