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


CHIP = re.compile(
    r'<a class="chip(?: is-active)?" href="(?P<href>[^"]*)"(?P<current> aria-current="page")?>\s*'
    r'<span class="chip__label">(?P<label>[^<]*)</span>\s*'
    r'<span class="chip__count">(?P<count>[\d,]+)<span class="visually-hidden"> products?</span></span>'
)


def category_chips(page):
    """The category chips as (label, count, link, is current)."""
    return [
        (html.unescape(m["label"]), int(m["count"].replace(",", "")), html.unescape(m["href"]), bool(m["current"]))
        for m in CHIP.finditer(page)
    ]


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
    assert category_chips(page) == [
        ("All", 5, "/portal/catalog", True),
        ("Fasteners", 1, "/portal/catalog?category=Fasteners", False),
        ("Packaging", 2, "/portal/catalog?category=Packaging", False),
    ]
    assert "Rival things" not in page and "Discontinued" not in page


def test_category_filter(shopper, catalog):
    page = get(shopper, category="Packaging")
    assert product_names(page) == ["bopp tape", "Corrugated carton"]
    assert [chip for chip in category_chips(page) if chip[3]] == [
        ("Packaging", 2, "/portal/catalog?category=Packaging", True)
    ]
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
    # The filter on screen matches the results
    assert category_chips(page)[-1] == ("Discontinued", 0, "/portal/catalog?category=Discontinued", True)
    assert [chip[0] for chip in category_chips(page) if chip[3]] == ["Discontinued"]


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


def test_search_and_category_text_is_escaped_when_shown_back(shopper, catalog):
    page = get(shopper, q="<i>x</i>", category='"><script>alert(1)</script>')
    assert "<i>x</i>" not in page and "<script>alert(1)</script>" not in page
    assert "&lt;i&gt;x&lt;/i&gt;" in page


# Category chips


def test_category_chips_are_links_that_work_without_javascript(shopper, catalog):
    page = get(shopper)
    assert "<select" not in page and "data-autosubmit" not in page
    assert '<nav aria-label="Categories">' in page and '<ul class="chips">' in page
    for label, count, href, _current in category_chips(page):
        listed = product_names(shopper.get(href).get_data(as_text=True))
        assert len(listed) == count, label


def test_chip_counts_follow_the_search_and_keep_it(shopper, catalog):
    page = get(shopper, q="tape")
    assert category_chips(page) == [
        ("All", 1, "/portal/catalog?q=tape", True),
        ("Fasteners", 0, "/portal/catalog?q=tape&category=Fasteners", False),
        ("Packaging", 1, "/portal/catalog?q=tape&category=Packaging", False),
    ]
    within = get(shopper, q="tape", category="Packaging")
    assert product_names(within) == ["bopp tape"]
    assert category_chips(within)[0] == ("All", 1, "/portal/catalog?q=tape", False)


def test_searching_keeps_the_category_picked(shopper, catalog):
    page = get(shopper, category="Packaging")
    form = page[page.index('role="search"') :]
    form = form[: form.index("</form>")]
    assert '<input type="hidden" name="category" value="Packaging">' in form
    assert 'name="category"' not in get(shopper).split('role="search"')[1].split("</form>")[0]


def test_one_product_is_read_as_one_product(shopper, catalog):
    page = get(shopper)
    assert '<span class="chip__count">1<span class="visually-hidden"> product</span></span>' in page
    assert '<span class="chip__count">2<span class="visually-hidden"> products</span></span>' in page


def test_no_chips_when_no_product_has_a_category(shopper, company):
    factories.make_product(company, name="Loose item")
    page = get(shopper)
    assert category_chips(page) == []
    assert 'class="chips"' not in page


@pytest.mark.parametrize("name", ["Tea & Coffee", "50% off", "Bolts/Nuts", "Fasteners #2", "मसाले"])
def test_categories_with_any_characters_link_to_themselves(shopper, company, name):
    factories.make_product(company, name="Item", category=name)
    page = get(shopper)
    (_label, count, href, _current) = [chip for chip in category_chips(page) if chip[0] == name][0]
    assert count == 1
    listed = shopper.get(href).get_data(as_text=True)
    assert product_names(listed) == ["Item"]
    assert [chip[0] for chip in category_chips(listed) if chip[3]] == [name]


def test_a_long_category_name_wraps_inside_its_chip():
    from tests.test_design_system import CSS

    chip = CSS[CSS.index("\n.chip {") :]
    assert "max-width: 100%;" in chip[: chip.index("}")]
    label = CSS[CSS.index("\n.chip__label {") :]
    label = label[: label.index("}")]
    assert "white-space: normal;" in label and "overflow-wrap: anywhere;" in label and "min-width: 0;" in label
