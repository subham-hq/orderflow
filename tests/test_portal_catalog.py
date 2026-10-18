import html
import re

import pytest

from orderflow.extensions import db
from tests import factories


@pytest.fixture
def shopper(client, login, buyer):
    login(buyer)
    return client


@pytest.fixture
def catalog(company):
    return {
        "carton": factories.make_product(
            company,
            sku="CTN-001",
            name="Corrugated carton",
            category="Packaging",
            unit="box",
            price_paise=4_250,
            gst_rate_bp=1800,
            description="Five-ply, 18 x 12 x 12 in. Strong enough for 25 kg.",
        ),
        "tape": factories.make_product(
            company, sku="TAP-010", name="bopp tape", category="Packaging", unit="roll", price_paise=6_800
        ),
        "bolt": factories.make_product(
            company, sku="BLT-M10", name="Hex bolt M10", category="Fasteners", unit="pack of 100", price_paise=52_000
        ),
        "rice": factories.make_product(
            company, sku="RCE_25K", name="Sona Masoori rice", unit="bag", price_paise=1_45_000, gst_rate_bp=500
        ),
        "hidden": factories.make_product(
            company, sku="OLD-1", name="Old stretch film", category="Discontinued", is_active=False
        ),
    }


def product_names(page):
    return [html.unescape(name) for name in re.findall(r'class="product-card__name"[^>]*>([^<]+)</h2>', page)]


def get(client, **params):
    response = client.get("/portal/catalog", query_string=params)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_catalog_is_only_for_signed_in_clients(client, login, owner):
    assert "/login" in client.get("/portal/catalog").headers["Location"]
    login(owner)
    assert client.get("/portal/catalog").headers["Location"].endswith("/app/")


def test_an_empty_catalog_says_products_are_coming(shopper, company):
    factories.make_product(company, is_active=False)
    page = get(shopper)
    assert "Kaveri Supplies hasn&#39;t added any products yet" in page
    assert 'id="catalog-search"' not in page
    assert "None" not in page


def test_catalog_lists_active_products_of_this_supplier_a_to_z(shopper, catalog, other_company):
    page = get(shopper)
    assert product_names(page) == ["bopp tape", "Corrugated carton", "Hex bolt M10", "Sona Masoori rice"]
    assert "Old stretch film" not in page
    assert other_company["product"].sku not in page
    assert "CTN-001" in page and "Packaging" in page
    assert "₹42.50" in page and "per box" in page and "GST 18%" in page
    assert "₹1,450.00" in page and "per bag" in page and "GST 5%" in page
    assert "Five-ply, 18 x 12 x 12 in." in page
    assert "4 products" in page
    assert "None" not in page


@pytest.mark.parametrize(
    "q, expected",
    [
        ("carton", ["Corrugated carton"]),
        ("CARTON", ["Corrugated carton"]),
        ("ctn-001", ["Corrugated carton"]),
        ("five-ply", ["Corrugated carton"]),  # description
        ("BOPP", ["bopp tape"]),
        ("rce_25", ["Sona Masoori rice"]),
        ("  hex  ", ["Hex bolt M10"]),
        ("e", ["bopp tape", "Corrugated carton", "Hex bolt M10", "Sona Masoori rice"]),
    ],
)
def test_search_matches_name_sku_and_description(shopper, catalog, q, expected):
    assert product_names(get(shopper, q=q)) == expected


@pytest.mark.parametrize("q", ["%", "\\", "nothing like this", "OLD-1", "RIVAL-1", "x" * 5000, "'; --"])
def test_search_with_no_results_says_so(shopper, catalog, other_company, q):
    page = get(shopper, q=q)
    assert product_names(page) == []
    assert "No products match" in page
    assert 'href="/portal/catalog"' in page  # Clear filters


def test_an_underscore_in_a_search_is_taken_literally(shopper, catalog):
    # "_" is a LIKE wildcard: "RCE_25K" must not match "RCEX25K"
    factories.make_product(catalog["rice"].company, sku="RCEX25K", name="Ponni rice")
    assert product_names(get(shopper, q="rce_25k")) == ["Sona Masoori rice"]


def test_categories_list_only_those_with_active_products(shopper, catalog, other_company, company):
    other_company["product"].category = "Rival things"
    factories.make_product(company, name="Blank category", category="   ")
    db.session.commit()
    page = get(shopper)
    options = re.findall(r'<option value="([^"]*)"', page)
    assert options == ["", "Fasteners", "Packaging"]
    assert "data-autosubmit" in page


def test_category_filter(shopper, catalog):
    page = get(shopper, category="Packaging")
    assert product_names(page) == ["bopp tape", "Corrugated carton"]
    assert '<option value="Packaging" selected>' in page
    assert "2 products in Packaging" in page
    assert "Clear filters" in page


def test_category_and_search_together(shopper, catalog):
    assert product_names(get(shopper, category="Packaging", q="tape")) == ["bopp tape"]
    page = get(shopper, category="Fasteners", q="tape")
    assert product_names(page) == []
    assert "No products match “tape” in Fasteners" in page


def test_an_unknown_category_shows_no_products_and_says_which(shopper, catalog):
    page = get(shopper, category="Discontinued")
    assert product_names(page) == []
    assert "No products in Discontinued right now" in page
    assert '<option value="Discontinued" selected>' in page  # the filter on screen matches the results


def test_long_or_odd_parameters_never_break_the_page(shopper, catalog):
    for params in ({"category": "x" * 5000}, {"page": "9" * 5000}, {"page": "-1"}, {"q": "٣", "page": "²"}):
        get(shopper, **params)


def test_pagination_shows_24_per_page_and_keeps_the_filters(shopper, company):
    for n in range(30):
        factories.make_product(company, sku=f"BOX-{n:02d}", name=f"Box {n:02d}", category="Boxes")
    first = get(shopper, q="box", category="Boxes")
    assert product_names(first) == [f"Box {n:02d}" for n in range(24)]
    assert "30 products matching “box” in Boxes · showing 1–24" in first
    assert 'href="/portal/catalog?q=box&amp;category=Boxes&amp;page=2"' in first

    second = get(shopper, q="box", category="Boxes", page="2")
    assert product_names(second) == [f"Box {n:02d}" for n in range(24, 30)]
    assert product_names(get(shopper, page="50")) == [f"Box {n:02d}" for n in range(24, 30)]


def test_each_product_has_an_add_to_cart_form_that_comes_back_to_this_page(shopper, catalog):
    carton = catalog["carton"]
    page = get(shopper, q="carton", category="Packaging", page="1")
    card = page[page.index(f'id="product-{carton.id}"') :]
    card = card[: card.index("</article>")]
    assert 'action="/portal/cart/add"' in card
    assert f'name="product_id" value="{carton.id}"' in card
    assert 'name="next" value="/portal/catalog?q=carton&amp;category=Packaging&amp;page=1"' in card
    assert "data-stepper" in card and 'name="quantity" value="1"' in card
    assert 'max="100000"' in card

    next_url = html.unescape(re.search(r'name="next" value="([^"]+)"', card).group(1))
    response = shopper.post("/portal/cart/add", data={"product_id": carton.id, "quantity": "4", "next": next_url})
    assert response.headers["Location"] == f"{next_url}#product-{carton.id}"


def test_products_already_in_the_cart_say_how_many(shopper, catalog):
    shopper.post("/portal/cart/add", data={"product_id": catalog["carton"].id, "quantity": "1200"})
    page = get(shopper)
    card = page[page.index(f'id="product-{catalog["carton"].id}"') :]
    assert "In cart: 1,200" in card[: card.index("</article>")]
    assert page.count("In cart:") == 1
    assert "Review cart (1,200)" in page


def test_no_cart_link_when_the_cart_is_empty(shopper, catalog):
    page = get(shopper)
    assert "Review cart" not in page and "In cart:" not in page


def test_a_product_hidden_by_the_supplier_leaves_the_catalog_and_the_cart(shopper, catalog):
    shopper.post("/portal/cart/add", data={"product_id": catalog["tape"].id, "quantity": "2"})
    catalog["tape"].is_active = False
    db.session.commit()
    page = get(shopper)
    assert "bopp tape" not in product_names(page)
    assert "bopp tape is no longer available, so we took it out of your cart." in page
