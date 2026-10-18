"""The workspace sidebar shows how many orders are waiting for review."""

from orderflow.services.orders import place_order
from tests import factories


def _count_badge(response):
    body = response.get_data(as_text=True)
    marker = 'class="nav-link__count">'
    if marker not in body:
        return None
    return body.split(marker, 1)[1].split("<", 1)[0]


def test_sidebar_counts_only_this_companys_pending_orders(client, login, company, owner, buyer, other_company):
    product = factories.make_product(company)
    login(owner)
    assert _count_badge(client.get("/app/")) is None  # nothing waiting

    place_order(buyer, {product.id: 1})
    place_order(buyer, {product.id: 2})
    place_order(other_company["client"], {other_company["product"].id: 1})

    assert _count_badge(client.get("/app/")) == "2"
    assert _count_badge(client.get("/app/products")) == "2"
