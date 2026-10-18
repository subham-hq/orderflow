"""Request hygiene: control characters in URLs."""

import pytest

from orderflow.services.orders import place_order
from tests import factories


@pytest.fixture
def order(company, buyer):
    product = factories.make_product(company, sku="CTN-001")
    order, _ = place_order(buyer, {product.id: 2})
    return order


@pytest.mark.parametrize("suffix", ["%00", "ORD-000001%00", "a%00b"])
def test_nul_in_a_portal_order_number_is_a_bad_request(client, login, buyer, order, suffix):
    # Postgres refuses NUL in text parameters, so these used to reach the
    # order query and fail with a 500 there.
    login(buyer)
    assert client.get(f"/portal/orders/{suffix}").status_code == 400
    assert client.get(f"/portal/orders/{suffix}/invoice").status_code == 400
    assert client.post(f"/portal/orders/{suffix}/cancel").status_code == 400
    assert client.post(f"/portal/orders/{suffix}/reorder").status_code == 400
    assert client.get(f"/portal/orders/{order.number}").status_code == 200


@pytest.mark.parametrize("path", ["/app/orders/a%00b", "/no-such-page%0A", "/%09", "/privacy%7F", "/verify/%1Fabc"])
def test_control_characters_anywhere_in_the_path_are_refused(client, login, owner, path):
    login(owner)
    assert client.get(path).status_code == 400


def test_nul_in_a_query_key_is_refused(client):
    assert client.get("/?a%00b=1").status_code == 400


def test_ordinary_paths_and_line_breaks_in_values_still_work(client, login, owner):
    login(owner)
    assert client.get("/app/orders?q=a%0Ab").status_code == 200
    assert client.get("/privacy").status_code == 200
    assert client.get("/no-such-page").status_code == 404
