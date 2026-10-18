import pytest
from flask import get_flashed_messages, session
from flask_login import login_user

from orderflow.extensions import db
from orderflow.money import MAX_QUANTITY
from orderflow.portal.cart import priced_session_cart
from orderflow.services.cart import MAX_CART_LINES
from tests import factories


@pytest.fixture
def catalog(company):
    return {
        "carton": factories.make_product(
            company, sku="CTN-001", name="Corrugated carton", price_paise=4_250, gst_rate_bp=1800, category="Packaging"
        ),
        "rice": factories.make_product(
            company, sku="RCE-25K", name="Sona Masoori rice", price_paise=1_45_000, gst_rate_bp=500
        ),
        "hidden": factories.make_product(company, sku="OLD-1", name="Old tape", is_active=False),
    }


@pytest.fixture
def shopper(client, login, buyer):
    login(buyer)
    return client


def set_cart(client, cart):
    with client.session_transaction() as sess:
        sess["cart"] = {str(key): value for key, value in cart.items()}


def get_cart(client):
    with client.session_transaction() as sess:
        return dict(sess.get("cart") or {})


def flashes(client):
    with client.session_transaction() as sess:
        return [message for _category, message in sess.get("_flashes", [])]


def add(client, product_id, quantity="1", next_url=None):
    data = {"product_id": product_id, "quantity": quantity}
    if next_url is not None:
        data["next"] = next_url
    return client.post("/portal/cart/add", data=data)


# Access


def test_cart_pages_need_a_signed_in_verified_client(client, login, owner, admin, catalog, company):
    for method, path in [("get", "/portal/cart"), ("post", "/portal/cart"), ("post", "/portal/cart/add")]:
        response = getattr(client, method)(path)
        assert response.status_code == 302 and "/login" in response.headers["Location"]

    for staff in (owner, admin):
        login(staff)
        response = client.get("/portal/cart")
        assert response.headers["Location"].endswith("/app/")
        assert add(client, catalog["carton"].id).headers["Location"].endswith("/app/")
        assert get_cart(client) == {}
        client.post("/logout")

    unverified = factories.make_client(company, email="new@buyer.example", verified=False)
    login(unverified)
    assert client.get("/portal/cart").headers["Location"].endswith("/verify")


# Adding


def test_empty_cart_explains_what_to_do(shopper):
    page = shopper.get("/portal/cart").get_data(as_text=True)
    assert "Your cart is empty" in page
    assert 'href="/portal/catalog"' in page


def test_add_to_cart_adds_up_and_returns_to_the_same_catalog_page(shopper, catalog):
    carton = catalog["carton"]
    response = add(shopper, carton.id, "3", next_url="/portal/catalog?q=ctn&category=Packaging&page=2")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/portal/catalog?q=ctn&category=Packaging&page=2#product-{carton.id}"
    assert get_cart(shopper) == {str(carton.id): 3}
    assert "Added 3 × Corrugated carton to your cart." in flashes(shopper)

    add(shopper, carton.id, " 2 ")
    assert get_cart(shopper) == {str(carton.id): 5}


def test_add_to_cart_keeps_a_fragment_already_in_the_return_address(shopper, catalog):
    response = add(shopper, catalog["carton"].id, "1", next_url="/portal/catalog?page=2#top")
    assert response.headers["Location"] == "/portal/catalog?page=2#top"


@pytest.mark.parametrize(
    "next_url", ["https://evil.example/portal/", "//evil.example/", "/app/", "", "javascript:alert(1)"]
)
def test_add_to_cart_only_returns_to_portal_pages(shopper, catalog, next_url):
    response = add(shopper, catalog["carton"].id, "1", next_url=next_url)
    assert response.headers["Location"] == f"/portal/catalog#product-{catalog['carton'].id}"


@pytest.mark.parametrize("product_id", ["abc", "", "0", "-1", "1.5", "²", "٣", "99999999999", "9" * 5000, "999999"])
def test_add_to_cart_ignores_invalid_product_ids(shopper, catalog, product_id):
    response = add(shopper, product_id, "1")
    assert response.status_code == 302
    assert get_cart(shopper) == {}
    assert "That product isn't available any more, so nothing was added to your cart." in flashes(shopper)


def test_add_to_cart_refuses_hidden_and_other_companies_products(shopper, catalog, other_company):
    for product in (catalog["hidden"], other_company["product"]):
        response = add(shopper, product.id, "1")
        assert response.status_code == 302
    assert get_cart(shopper) == {}


@pytest.mark.parametrize(
    "quantity, message",
    [
        ("", "Enter a quantity of 1 or more."),
        ("0", "Enter a quantity of 1 or more."),
        ("-1", "Quantities must be whole numbers."),
        ("2.5", "Quantities must be whole numbers."),
        ("²", "Quantities must be whole numbers."),
        ("٣", "Quantities must be whole numbers."),
        ("1e3", "Quantities must be whole numbers."),
        ("abc", "Quantities must be whole numbers."),
        ("100001", "The most you can order of one item is 100,000."),
        ("9" * 5000, "The most you can order of one item is 100,000."),
        ("x" * 5000, "Quantities must be whole numbers."),
    ],
)
def test_add_to_cart_rejects_bad_quantities(shopper, catalog, quantity, message):
    response = add(shopper, catalog["carton"].id, quantity)
    assert response.status_code == 302
    assert get_cart(shopper) == {}
    assert flashes(shopper) == [f"{message} Nothing was added for Corrugated carton."]


def test_add_to_cart_caps_each_line_at_the_maximum(shopper, catalog):
    carton = catalog["carton"]
    add(shopper, carton.id, str(MAX_QUANTITY))
    add(shopper, carton.id, "5")
    assert get_cart(shopper) == {str(carton.id): MAX_QUANTITY}
    assert flashes(shopper)[-1] == "You can order up to 100,000 of Corrugated carton, so your cart now has 100,000."


def test_cart_holds_a_limited_number_of_products_and_the_cookie_stays_small(shopper, company):
    products = [factories.make_product(company) for _ in range(MAX_CART_LINES + 1)]
    set_cart(shopper, {product.id: MAX_QUANTITY for product in products[: MAX_CART_LINES - 1]})

    response = add(shopper, products[MAX_CART_LINES - 1].id, str(MAX_QUANTITY))
    assert len(get_cart(shopper)) == MAX_CART_LINES
    cookie = response.headers["Set-Cookie"]
    assert len(cookie) < 3000, len(cookie)

    add(shopper, products[MAX_CART_LINES].id, "1")
    assert len(get_cart(shopper)) == MAX_CART_LINES
    assert "Your cart can hold up to 100 different products." in flashes(shopper)[-1]


# Showing the cart


def test_cart_page_prices_every_line_with_gst(shopper, catalog):
    set_cart(shopper, {catalog["carton"].id: 10, catalog["rice"].id: 2})
    page = shopper.get("/portal/cart").get_data(as_text=True)
    assert "Corrugated carton" in page and "Sona Masoori rice" in page
    assert "₹42.50" in page  # carton unit price
    assert "₹76.50" in page  # 18% GST on ₹425.00
    assert "₹501.50" in page  # carton line total
    assert "₹3,325.00" in page  # subtotal
    assert "₹221.50" in page  # GST: 76.50 + 145.00
    assert "₹3,546.50" in page  # total
    assert "2 products at today&#39;s prices" in page
    assert f'name="qty-{catalog["carton"].id}" value="10"' in page
    assert "None" not in page


def test_cart_shows_todays_price_after_the_supplier_changes_it(shopper, catalog):
    set_cart(shopper, {catalog["carton"].id: 2})
    assert "₹85.00" in shopper.get("/portal/cart").get_data(as_text=True)
    catalog["carton"].price_paise = 5_000
    db.session.commit()
    page = shopper.get("/portal/cart").get_data(as_text=True)
    assert "₹100.00" in page and "₹85.00" not in page


def test_products_hidden_after_adding_are_taken_out_with_a_message(shopper, catalog):
    set_cart(shopper, {catalog["carton"].id: 2, catalog["rice"].id: 1})
    catalog["rice"].is_active = False
    db.session.commit()

    page = shopper.get("/portal/cart").get_data(as_text=True)
    assert "Sona Masoori rice is no longer available, so we took it out of your cart." in page
    assert get_cart(shopper) == {str(catalog["carton"].id): 2}
    # Only said once
    assert "no longer available" not in shopper.get("/portal/cart").get_data(as_text=True)


def test_deleted_products_are_taken_out_with_a_message(shopper, catalog, company):
    doomed = factories.make_product(company, name="Gone soon")
    set_cart(shopper, {doomed.id: 1})
    db.session.delete(doomed)
    db.session.commit()
    page = shopper.get("/portal/cart").get_data(as_text=True)
    assert "An item in your cart is no longer in the catalog, so we took it out." in page
    assert "Your cart is empty" in page
    assert get_cart(shopper) == {}


def test_a_malformed_session_cart_is_ignored(shopper, catalog):
    with shopper.session_transaction() as sess:
        sess["cart"] = {"abc": 2, str(catalog["carton"].id): "x", "-4": 1}
    page = shopper.get("/portal/cart").get_data(as_text=True)
    assert "Your cart is empty" in page
    assert get_cart(shopper) == {}


def test_pricing_drops_anything_the_sync_missed(app, company, buyer, catalog):
    doomed = factories.make_product(company)
    doomed_id = doomed.id
    db.session.delete(doomed)
    db.session.commit()
    with app.test_request_context():
        login_user(buyer)
        session["cart"] = {str(catalog["carton"].id): 2, str(catalog["hidden"].id): 1, str(doomed_id): 3}
        priced = priced_session_cart()
        assert [line.product.sku for line in priced.lines] == ["CTN-001"]
        assert session["cart"] == {str(catalog["carton"].id): 2}
        assert get_flashed_messages() == [
            "Old tape is no longer available, so we took it out of your cart. "
            "An item in your cart is no longer in the catalog, so we took it out."
        ]


# Updating


def test_update_changes_quantities_and_removes_zero_lines(shopper, catalog):
    carton, rice = catalog["carton"], catalog["rice"]
    set_cart(shopper, {carton.id: 10, rice.id: 2})
    response = shopper.post("/portal/cart", data={f"qty-{carton.id}": "4", f"qty-{rice.id}": "0"})
    assert response.headers["Location"] == "/portal/cart"
    assert get_cart(shopper) == {str(carton.id): 4}
    assert flashes(shopper) == [
        "Removed Sona Masoori rice from your cart.",
        "Quantities updated. The totals below use today's prices.",
    ]


def test_an_empty_quantity_removes_the_line(shopper, catalog):
    set_cart(shopper, {catalog["carton"].id: 3})
    shopper.post("/portal/cart", data={f"qty-{catalog['carton'].id}": "  "})
    assert get_cart(shopper) == {}


def test_remove_button_removes_only_that_line_and_keeps_other_edits(shopper, catalog):
    carton, rice = catalog["carton"], catalog["rice"]
    set_cart(shopper, {carton.id: 10, rice.id: 2})
    shopper.post("/portal/cart", data={f"qty-{carton.id}": "12", f"qty-{rice.id}": "2", "remove": str(rice.id)})
    assert get_cart(shopper) == {str(carton.id): 12}


def test_removing_a_line_with_a_bad_quantity_still_works(shopper, catalog):
    carton, rice = catalog["carton"], catalog["rice"]
    set_cart(shopper, {carton.id: 10, rice.id: 2})
    response = shopper.post(
        "/portal/cart", data={f"qty-{carton.id}": "10", f"qty-{rice.id}": "abc", "remove": str(rice.id)}
    )
    assert response.status_code == 302
    assert get_cart(shopper) == {str(carton.id): 10}


@pytest.mark.parametrize("quantity", ["-1", "2.5", "²", "٣", "1e3", "abc", "100001", "9" * 5000])
def test_update_with_a_bad_quantity_shows_the_error_and_keeps_what_was_typed(shopper, catalog, quantity):
    carton, rice = catalog["carton"], catalog["rice"]
    set_cart(shopper, {carton.id: 10, rice.id: 2})
    response = shopper.post("/portal/cart", data={f"qty-{carton.id}": "7", f"qty-{rice.id}": quantity})
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Some quantities need fixing" in page
    assert f'id="qty-{rice.id}-error"' in page
    assert f'name="qty-{carton.id}" value="7"' in page  # the valid edit is still on screen
    assert get_cart(shopper) == {str(carton.id): 10, str(rice.id): 2}  # nothing saved


def test_update_ignores_malformed_stale_and_foreign_fields(shopper, catalog, other_company):
    carton = catalog["carton"]
    set_cart(shopper, {carton.id: 10})
    data = {
        "qty-abc": "3",
        "qty-": "1",
        "qty-٣": "1",
        "qty-99999999999999": "2",
        f"qty-{other_company['product'].id}": "5",
        f"qty-{catalog['hidden'].id}": "5",
        f"qty-{catalog['rice'].id}": "5",  # not in the cart: a stale tab can't add it back
        "remove": "abc",
        "something": "else",
    }
    response = shopper.post("/portal/cart", data=data)
    assert response.status_code == 302
    assert get_cart(shopper) == {str(carton.id): 10}
    assert flashes(shopper) == ["Your cart is already up to date."]


def test_update_on_an_empty_cart_says_so(shopper, catalog):
    shopper.post("/portal/cart", data={f"qty-{catalog['carton'].id}": "4"})
    assert get_cart(shopper) == {}
    assert flashes(shopper) == ["Your cart is empty, so there was nothing to update."]
