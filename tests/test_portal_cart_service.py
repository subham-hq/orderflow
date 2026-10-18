import pytest
from flask import session

from orderflow.extensions import db
from orderflow.money import MAX_QUANTITY
from orderflow.services import cart as cart_service
from orderflow.services.cart import (
    MAX_CART_LINES,
    CartError,
    SyncResult,
    add_quantity,
    cart_version,
    join_names,
    parse_id,
    sanitize,
    sync_cart,
    sync_message,
)
from orderflow.services.orders import price_cart
from tests import factories


@pytest.mark.parametrize("value, expected", [("12", 12), (" 7 ", 7), (3, 3), ("2147483647", 2147483647)])
def test_parse_id_accepts_plain_ids(value, expected):
    assert parse_id(value) == expected


@pytest.mark.parametrize(
    "value", [None, "", "0", "-1", "abc", "1.5", "²", "٣", "1e3", "2147483648", "9" * 40, "12abc", " "]
)
def test_parse_id_rejects_anything_else(value):
    assert parse_id(value) is None


def test_sanitize_drops_malformed_entries_and_caps_quantities():
    raw = {
        "1": 3,
        "007": 2,  # normalised to "7"
        "abc": 1,
        "٣": 1,
        "4": 0,
        "5": -2,
        "6": "3",
        "8": True,
        "9": 2.5,
        "10": MAX_QUANTITY + 50,
        "99999999999": 1,
    }
    assert sanitize(raw) == {"1": 3, "7": 2, "10": MAX_QUANTITY}
    assert sanitize(None) == {}
    assert sanitize(["1", 2]) == {}
    assert sanitize("cart") == {}


def test_sanitize_keeps_at_most_the_line_limit():
    raw = {str(n): 1 for n in range(1, MAX_CART_LINES + 20)}
    assert len(sanitize(raw)) == MAX_CART_LINES


def test_add_quantity_adds_and_caps():
    cart = {}
    assert add_quantity(cart, 5, 3) == (3, False)
    assert add_quantity(cart, 5, 4) == (7, False)
    assert add_quantity(cart, 5, MAX_QUANTITY) == (MAX_QUANTITY, True)
    assert cart == {"5": MAX_QUANTITY}


def test_add_quantity_refuses_a_new_line_when_the_cart_is_full():
    cart = {str(n): 1 for n in range(1, MAX_CART_LINES + 1)}
    with pytest.raises(CartError, match="up to 100 different products"):
        add_quantity(cart, MAX_CART_LINES + 1, 1)
    # Adding more of a product already in the cart still works
    assert add_quantity(cart, 1, 1) == (2, False)


def test_get_save_and_clear_cart_use_the_session(app):
    with app.test_request_context():
        assert cart_service.get_cart() == {}
        cart_service.save_cart({"3": 2, "bad": 1})
        assert session["cart"] == {"3": 2}
        assert cart_service.get_cart() == {"3": 2}
        cart_service.save_cart({})
        assert "cart" not in session
        cart_service.save_cart({"3": 2})
        cart_service.clear_cart()
        assert "cart" not in session


def test_active_products_only_returns_active_products_of_the_company(company, other_company):
    live = factories.make_product(company)
    hidden = factories.make_product(company, is_active=False)
    found = cart_service.active_products(company.id, [live.id, hidden.id, other_company["product"].id])
    assert list(found) == [live.id]
    assert cart_service.active_products(company.id, []) == {}


def test_sync_cart_removes_hidden_deleted_and_foreign_products(app, company, other_company):
    live = factories.make_product(company, name="Carton")
    hidden = factories.make_product(company, name="Old tape", is_active=False)
    doomed = factories.make_product(company, name="Gone")
    doomed_id = doomed.id
    db.session.delete(doomed)
    db.session.commit()

    with app.test_request_context():
        session["cart"] = {
            str(live.id): 2,
            str(hidden.id): 1,
            str(doomed_id): 4,
            str(other_company["product"].id): 1,
        }
        result = sync_cart(company.id)
        assert result.removed_names == ["Old tape"]
        assert result.unknown_count == 2
        assert session["cart"] == {str(live.id): 2}

        # Nothing to change the second time
        assert not sync_cart(company.id).changed
        assert session["cart"] == {str(live.id): 2}


def test_sync_cart_handles_missing_and_malformed_carts(app, company):
    with app.test_request_context():
        assert not sync_cart(company.id).changed
        session["cart"] = "not a cart"
        assert not sync_cart(company.id).changed
        assert "cart" not in session


def test_cart_version_changes_with_quantity_and_price(company):
    product = factories.make_product(company, price_paise=1_000)
    first = cart_version(price_cart(company.id, {str(product.id): 2}))
    assert first == cart_version(price_cart(company.id, {str(product.id): 2}))
    assert first != cart_version(price_cart(company.id, {str(product.id): 3}))
    product.price_paise = 1_100
    db.session.commit()
    assert first != cart_version(price_cart(company.id, {str(product.id): 2}))


def test_join_names():
    assert join_names([]) == ""
    assert join_names(["A"]) == "A"
    assert join_names(["A", "B"]) == "A and B"
    assert join_names(["A", "B", "C"]) == "A, B and C"
    assert join_names(list("ABCDEFG")) == "A, B, C, D, E and 2 more"


def test_sync_message_wording():
    assert sync_message(SyncResult()) is None
    assert sync_message(SyncResult(["Tape"])) == "Tape is no longer available, so we took it out of your cart."
    assert sync_message(SyncResult(["Tape", "Film"])) == (
        "Tape and Film are no longer available, so we took them out of your cart."
    )
    assert sync_message(SyncResult(unknown_count=1)) == (
        "An item in your cart is no longer in the catalog, so we took it out."
    )
    assert "2 items in your cart" in sync_message(SyncResult(unknown_count=2))
