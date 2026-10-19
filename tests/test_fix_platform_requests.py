"""Request hygiene: control characters in URLs, and query strings in page links."""

import re

import pytest

from orderflow.extensions import db
from orderflow.models import Bulletin
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


# Pagination links ------------------------------------------------------------

# Query keys that are also url_for's own arguments
RESERVED = ["endpoint=x", "_method=POST", "_anchor=top", "_scheme=https", "_external=1", "page=1&endpoint=x"]


@pytest.fixture
def long_lists(company, owner, buyer):
    """More than one page of every paginated list."""
    products = [factories.make_product(company, sku=f"BOX-{n:02d}", name=f"Box {n:02d}") for n in range(30)]
    for n in range(26):
        factories.make_client(company, email=f"client{n:02d}@buyer.example", password=None)
    for product in products[:26]:
        place_order(buyer, {product.id: 1})
    db.session.add_all(Bulletin(company=company, author=owner, title=f"Notice {n}", body="Hi") for n in range(26))
    db.session.commit()


def next_link(response):
    assert response.status_code == 200
    match = re.search(r'<a class="pagination__link" href="([^"]*)" rel="next">', response.get_data(as_text=True))
    assert match, "no Next link"
    return match.group(1).replace("&amp;", "&")


def test_page_links_ignore_url_for_arguments_on_every_list(client, login, owner, buyer, long_lists):
    every_reserved_key = "&".join(RESERVED)
    login(owner)
    for path in ("/app/orders", "/app/products", "/app/clients", "/app/bulletins"):
        assert next_link(client.get(f"{path}?{every_reserved_key}")) == f"{path}?page=2", path
    for query in RESERVED:
        assert next_link(client.get(f"/app/products?{query}")) == "/app/products?page=2", query

    client.post("/logout")
    login(buyer)
    for path in ("/portal/catalog", "/portal/orders"):
        assert next_link(client.get(f"{path}?{every_reserved_key}")) == f"{path}?page=2", path
    for query in RESERVED:
        assert next_link(client.get(f"/portal/catalog?{query}")) == "/portal/catalog?page=2", query


def test_page_links_keep_the_real_filters_in_order(client, login, owner, buyer, long_lists):
    login(owner)
    response = client.get("/app/orders?status=pending&_anchor=x&q=buyer&utm_source=mail&from=2020-01-01")
    assert next_link(response) == "/app/orders?status=pending&q=buyer&from=2020-01-01&page=2"
    assert next_link(client.get("/app/products?q=box&endpoint=x")) == "/app/products?q=box&page=2"

    client.post("/logout")
    login(buyer)
    response = client.get("/portal/catalog?q=box&_method=POST&category=")
    assert next_link(response) == "/portal/catalog?q=box&category=&page=2"
