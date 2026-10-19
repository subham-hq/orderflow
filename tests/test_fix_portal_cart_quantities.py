"""A typo in a cart quantity box is shown as an error and never removes the line.

Browsers send a number input they can't read ("5-", "1e", a letter in
Firefox or Safari) as an empty value, so a blank quantity must not be taken
as 0. Only an explicit 0 or the Remove button takes a line out.
"""

import pytest

from orderflow.portal.cart import BLANK_QUANTITY_MESSAGE
from tests import factories
from tests.test_portal_cart import flashes, get_cart, set_cart
from tests.test_portal_checkout import checkout_form, order_count


@pytest.fixture
def catalog(company):
    return {
        "carton": factories.make_product(company, sku="CTN-001", name="Corrugated carton", price_paise=4_250),
        "rice": factories.make_product(company, sku="RCE-25K", name="Sona Masoori rice", price_paise=1_45_000),
    }


@pytest.fixture
def shopper(client, login, buyer):
    login(buyer)
    return client


TYPOS = ["", "   ", "\t", "5-", "1e", "12.", "1.5", "abc", "٣", "１２", "+5", "1,000"]


@pytest.mark.parametrize("typed", TYPOS)
def test_a_typo_on_update_keeps_every_line_and_says_what_to_fix(shopper, catalog, typed):
    carton, rice = catalog["carton"], catalog["rice"]
    set_cart(shopper, {carton.id: 5, rice.id: 2})
    response = shopper.post("/portal/cart", data={f"qty-{carton.id}": typed, f"qty-{rice.id}": "3"})
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Some quantities need fixing" in page
    assert f'id="qty-{carton.id}-error"' in page
    assert f'id="qty-{carton.id}"' in page  # the line is still on screen to fix
    assert f'name="qty-{rice.id}" value="3"' in page  # the other edit isn't lost
    assert get_cart(shopper) == {str(carton.id): 5, str(rice.id): 2}  # nothing saved, nothing removed
    assert not any(message.startswith("Removed") for message in flashes(shopper))


@pytest.mark.parametrize("typed", ["", "  "])
def test_a_blank_quantity_gets_its_own_message(shopper, catalog, typed):
    carton = catalog["carton"]
    set_cart(shopper, {carton.id: 5})
    page = shopper.post("/portal/cart", data={f"qty-{carton.id}": typed}).get_data(as_text=True)
    assert f'<p class="field__error" id="qty-{carton.id}-error">{BLANK_QUANTITY_MESSAGE}</p>' in page
    assert f'aria-describedby="qty-{carton.id}-error"' in page


@pytest.mark.parametrize("typed", TYPOS)
def test_a_typo_at_checkout_places_nothing_and_keeps_the_line(shopper, catalog, typed):
    carton, rice = catalog["carton"], catalog["rice"]
    set_cart(shopper, {carton.id: 5, rice.id: 2})
    data = checkout_form(shopper, **{f"qty-{carton.id}": typed, "notes": "PO 77"})
    response = shopper.post("/portal/checkout", data=data)
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Some quantities need fixing" in page
    assert f'id="qty-{carton.id}-error"' in page
    assert "PO 77" in page  # the notes typed so far stay on screen
    assert order_count() == 0
    assert get_cart(shopper) == {str(carton.id): 5, str(rice.id): 2}


def test_a_blank_quantity_on_a_one_line_cart_does_not_empty_it(shopper, catalog):
    carton = catalog["carton"]
    set_cart(shopper, {carton.id: 5})
    response = shopper.post("/portal/checkout", data=checkout_form(shopper, **{f"qty-{carton.id}": ""}))
    assert response.status_code == 200
    assert "Your cart is empty" not in response.get_data(as_text=True)
    assert get_cart(shopper) == {str(carton.id): 5}


def test_zero_still_removes_a_line(shopper, catalog):
    carton, rice = catalog["carton"], catalog["rice"]
    set_cart(shopper, {carton.id: 5, rice.id: 2})
    response = shopper.post("/portal/cart", data={f"qty-{carton.id}": "0", f"qty-{rice.id}": "2"})
    assert response.status_code == 302
    assert get_cart(shopper) == {str(rice.id): 2}
    assert "Removed Corrugated carton from your cart." in flashes(shopper)


def test_the_remove_button_works_on_a_line_left_blank(shopper, catalog):
    carton, rice = catalog["carton"], catalog["rice"]
    set_cart(shopper, {carton.id: 5, rice.id: 2})
    data = {f"qty-{carton.id}": "", f"qty-{rice.id}": "2", "remove": str(carton.id)}
    response = shopper.post("/portal/cart", data=data)
    assert response.status_code == 302
    assert get_cart(shopper) == {str(rice.id): 2}


def test_a_blank_on_another_line_stops_the_remove_button(shopper, catalog):
    # The blank line needs fixing first; nothing changes until it is
    carton, rice = catalog["carton"], catalog["rice"]
    set_cart(shopper, {carton.id: 5, rice.id: 2})
    data = {f"qty-{carton.id}": "", f"qty-{rice.id}": "2", "remove": str(rice.id)}
    response = shopper.post("/portal/cart", data=data)
    assert response.status_code == 200
    assert get_cart(shopper) == {str(carton.id): 5, str(rice.id): 2}


def test_a_quantity_error_wraps_inside_its_row():
    # On phones the quantity cell is sized to its content; without a cap a
    # long error ran past the edge of the card and was cut off
    from tests.test_design_system import CSS

    phone = CSS[CSS.index("@media screen and (max-width: 639px) {\n  .table-wrap > .table--stack") :]
    cell = phone[phone.index(".table--cart td:nth-child(3) {") :]
    assert "max-width: 100%;" in cell[: cell.index("}")]
    wide = CSS[CSS.index("@media (min-width: 640px) {\n  .table--cart .field__error {") :]
    assert "max-width: 13rem;" in wide[: wide.index("}")]
