import pytest

from tests import factories


@pytest.fixture
def signed_in(login, owner):
    login(owner)
    return owner


def text(response):
    return response.get_data(as_text=True)


def test_anonymous_users_are_sent_to_sign_in(client):
    response = client.get("/app/products?q=tape")
    assert response.status_code == 302
    assert response.headers["Location"] == "/login?next=/app/products?q%3Dtape"


def test_clients_are_sent_back_to_their_portal(client, login, buyer):
    login(buyer)
    response = client.get("/app/products")
    assert response.status_code == 302 and response.headers["Location"] == "/portal/"


def test_unverified_staff_must_confirm_their_email_first(client, login, company):
    unverified = factories.make_owner(company, email="new@kaveri.example", verified=False)
    login(unverified)
    assert client.get("/app/products").headers["Location"] == "/verify"


def test_admins_see_the_catalog(client, login, company, admin):
    factories.make_product(company, name="Adhesive tape")
    login(admin)
    response = client.get("/app/products")
    assert response.status_code == 200 and "Adhesive tape" in text(response)


def test_empty_catalog_explains_what_to_do(client, signed_in):
    body = text(client.get("/app/products"))
    assert "Add your first product" in body
    assert "/app/products/new" in body and "/app/products/import" in body
    assert "Export CSV" not in body
    assert 'aria-current="page"' in body and "Catalog" in body


def test_list_shows_products_sorted_by_name_with_details(client, signed_in, company, other_company):
    factories.make_product(company, name="zinc washers", sku="ZW-1", price_paise=999, gst_rate_bp=1200)
    factories.make_product(company, name="Adhesive tape", sku="AT-1", category="Packaging", unit="roll")
    factories.make_product(company, name="Mop", sku="MOP-1", is_active=False, gst_rate_bp=25)
    body = text(client.get("/app/products"))
    assert body.index("Adhesive tape") < body.index("Mop") < body.index("zinc washers")
    assert "₹9.99" in body and "12%" in body and "0.25%" in body and "₹100.00" in body
    assert 'class="table__secondary mono">AT-1' in body
    assert "Packaging" in body and "No category" in body and ">roll<" in body
    assert "badge--success" in body and "Hidden" in body
    assert "RIVAL-1" not in body
    assert "None" not in body
    assert "Export CSV" in body and "Import CSV" in body and "New product" in body
    assert 'scope="col"' in body
    assert "Showing 1–3 of 3 products" in body


def test_list_links_to_edit_and_status_actions(client, signed_in, company):
    shown = factories.make_product(company, name="Shown")
    hidden = factories.make_product(company, name="Gone", is_active=False)
    body = text(client.get("/app/products"))
    assert f"/app/products/{shown.id}/edit" in body and f"/app/products/{hidden.id}/edit" in body
    assert f'action="/app/products/{shown.id}/status"' in body
    assert f'action="/app/products/{hidden.id}/status"' in body
    assert 'name="active" value="0"' in body and 'name="active" value="1"' in body


def test_search_matches_name_sku_and_description(client, signed_in, company):
    factories.make_product(company, name="Carton box", sku="BX-1")
    factories.make_product(company, name="Bolt", sku="CTN-BOLT")
    factories.make_product(company, name="Nut", sku="NUT-1", description="Fits carton machines")
    factories.make_product(company, name="Washer", sku="WS-1")
    body = text(client.get("/app/products?q=ctn"))
    assert "Bolt" in body and "Carton box" not in body
    body = text(client.get("/app/products?q=  CARTON  "))
    assert "Carton box" in body and "Nut" in body and "Washer" not in body and "Bolt" not in body
    assert 'value="CARTON"' in body


def test_search_never_shows_other_companies(client, signed_in, company, other_company):
    factories.make_product(company, name="Our carton", sku="OC-1")
    factories.make_product(other_company["company"], name="Rival carton", sku="RC-9")
    body = text(client.get("/app/products?q=carton"))
    assert "Our carton" in body and "Rival carton" not in body
    assert 'All <span class="muted">1</span>' in body


def test_search_treats_wildcards_literally(client, signed_in, company):
    factories.make_product(company, name="100% cotton rag", sku="RAG-1")
    factories.make_product(company, name="Plain rag", sku="RAG_2")
    body = text(client.get("/app/products?q=100%25"))
    assert "100% cotton rag" in body and "Plain rag" not in body
    body = text(client.get("/app/products?q=_"))
    assert "Plain rag" in body and "100% cotton rag" not in body
    body = text(client.get("/app/products?q=%5C"))
    assert "No products match" in body


def test_very_long_searches_are_cut_short(client, signed_in, company):
    factories.make_product(company, name="Tape")
    response = client.get("/app/products?q=" + "x" * 5000)
    assert response.status_code == 200 and "x" * 101 not in text(response)


def test_category_and_status_filters(client, signed_in, company):
    factories.make_product(company, name="Tape", category="Packaging")
    factories.make_product(company, name="Hidden film", category="Packaging", is_active=False)
    factories.make_product(company, name="Gloves", category="Safety")
    body = text(client.get("/app/products?category=Packaging"))
    assert "Tape" in body and "Hidden film" in body and "Gloves" not in body
    assert '<option value="Packaging" selected>Packaging</option>' in body
    body = text(client.get("/app/products?category=Packaging&status=hidden"))
    assert "Hidden film" in body and "Tape" not in body
    assert 'aria-current="page">Hidden <span class="muted">1</span>' in body
    assert 'name="status" value="hidden"' in body  # search keeps the status filter
    body = text(client.get("/app/products?status=active"))
    assert "Tape" in body and "Gloves" in body and "Hidden film" not in body
    assert 'aria-current="page">Active <span class="muted">2</span>' in body
    # Unknown values are ignored rather than breaking the page
    body = text(client.get("/app/products?status=deleted"))
    assert "Hidden film" in body and "Gloves" in body
    assert 'aria-current="page">All <span class="muted">3</span>' in body


def test_tab_counts_follow_the_search(client, signed_in, company):
    factories.make_product(company, name="Tape roll")
    factories.make_product(company, name="Tape hidden", is_active=False)
    factories.make_product(company, name="Gloves")
    body = text(client.get("/app/products?q=tape"))
    assert 'All <span class="muted">2</span>' in body and 'Hidden <span class="muted">1</span>' in body
    assert "/app/products?q=tape&amp;status=hidden" in body


def test_filters_with_no_results_offer_to_clear_them(client, signed_in, company):
    factories.make_product(company, name="Tape")
    body = text(client.get("/app/products?q=nothing-like-this"))
    assert "No products match" in body and "Clear filters" in body
    body = text(client.get("/app/products?category=Not+a+category"))
    assert "No products match" in body and '<option value="Not a category" selected>' in body


def test_list_paginates_by_25(client, signed_in, company):
    for n in range(30):
        factories.make_product(company, name=f"Item {n:02d}", sku=f"IT-{n:02d}")
    first = text(client.get("/app/products"))
    assert "Item 24" in first and "Item 25" not in first
    assert "Page 1 of 2" in first and "Showing 1–25 of 30 products" in first
    second = text(client.get("/app/products?page=2"))
    assert "Item 25" in second and "Item 29" in second and "Item 24" not in second
    assert "Showing 26–30 of 30 products" in second
    # Bad or out-of-range page numbers never error
    assert "Item 00" in text(client.get("/app/products?page=abc"))
    assert "Item 00" in text(client.get("/app/products?page=-3"))
    assert "Item 29" in text(client.get("/app/products?page=99999999999999999999"))
    assert "Item 29" in text(client.get("/app/products?page=3"))


def test_page_links_keep_the_filters(client, signed_in, company):
    for n in range(26):
        factories.make_product(company, name=f"Tape {n:02d}", category="Packaging")
    body = text(client.get("/app/products?category=Packaging&q=tape"))
    assert "category=Packaging" in body and "page=2" in body and "q=tape" in body


def test_status_buttons_return_to_the_same_list(client, signed_in, company):
    product = factories.make_product(company, name="Tape")
    body = text(client.get("/app/products?q=tape&page=1"))
    assert 'name="next" value="/app/products?q=tape&amp;page=1"' in body
    response = client.post(f"/app/products/{product.id}/status", data={"active": "0", "next": "/app/products?q=tape"})
    assert response.headers["Location"] == "/app/products?q=tape"
