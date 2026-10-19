"""Empty lists say whether the search found nothing or only this tab is empty."""

import re

import pytest

from orderflow.extensions import db
from orderflow.models import APPROVED, FULFILLED, PENDING
from tests import factories
from tests.test_admin_orders_support import ist, make_order


@pytest.fixture
def signed_in(login, owner):
    login(owner)
    return owner


def get(client, path):
    response = client.get(path)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def empty_state(html):
    match = re.search(r'<div class="empty-state[^"]*">(.*?)</div>\s*</div>', html, re.S)
    assert match, "no empty state on the page"
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(1))).strip(), match.group(1)


def assert_clean(html):
    assert 'style="' not in html and "<style" not in html
    assert "None" not in re.sub(r"<[^>]+>", " ", html)
    assert "&amp;#39;" not in html


# Orders -----------------------------------------------------------------------


@pytest.fixture
def mehta_orders(company, owner):
    product = factories.make_product(company, sku="WID")
    mehta = factories.make_client(company, email="ravi@mehta.example", first_name="Ravi", buyer_company="Mehta & Sons")
    for day, status in enumerate((PENDING, APPROVED, FULFILLED, FULFILLED), start=1):
        make_order(company, mehta, [(product, 1)], status, ist(2026, 10, day, 10, 0), owner)
    return mehta


def test_an_empty_tab_with_matches_elsewhere_says_so(client, signed_in, mehta_orders):
    html = get(client, "/app/orders?q=mehta&status=rejected")
    text, markup = empty_state(html)
    assert text.startswith("No rejected orders match your search")
    assert "Your search matches 4 orders, but none of them are rejected." in text
    assert "Check the spelling" not in text
    assert 'href="/app/orders?q=mehta"' in markup and "Show all 4 matching orders" in text
    assert_clean(html)


def test_one_match_elsewhere_reads_naturally(client, signed_in, mehta_orders):
    text, markup = empty_state(get(client, "/app/orders?q=mehta&status=cancelled&from=2026-10-01&to=2026-10-01"))
    assert "Your search matches 1 order, but it isn't cancelled." in text.replace("&#39;", "'")
    assert "Show the matching order" in text
    assert 'href="/app/orders?q=mehta&amp;from=2026-10-01&amp;to=2026-10-01"' in markup


def test_a_search_with_no_matches_anywhere_still_suggests_checking_it(client, signed_in, mehta_orders):
    text, _ = empty_state(get(client, "/app/orders?q=nobody&status=rejected"))
    assert text.startswith("No orders match your search") and "Check the spelling" in text


def test_an_empty_tab_without_a_search_explains_the_tab(client, signed_in, mehta_orders):
    text, _ = empty_state(get(client, "/app/orders?status=rejected"))
    assert text.startswith("No rejected orders") and "match your search" not in text
    assert "Orders you reject will be listed here" in text


# Products ----------------------------------------------------------------------


def test_the_hidden_tab_with_nothing_hidden_doesnt_mention_a_search(client, signed_in, company):
    factories.make_product(company, name="Carton")
    html = get(client, "/app/products?status=hidden")
    text, markup = empty_state(html)
    assert text.startswith("No hidden products")
    assert "Every product in your catalog is visible to clients." in text.replace("&#39;", "'")
    assert "search" not in text.lower() and "No products match" not in text
    assert 'href="/app/products"' in markup and "Show all products" in text
    assert_clean(html)


def test_the_active_tab_with_everything_hidden_points_to_the_hidden_tab(client, signed_in, company):
    factories.make_product(company, name="Carton", is_active=False)
    text, markup = empty_state(get(client, "/app/products?status=active"))
    assert text.startswith("No active products") and "Every product is hidden from clients" in text
    assert 'href="/app/products?status=hidden"' in markup


def test_a_search_that_matches_only_the_other_status(client, signed_in, company):
    factories.make_product(company, name="Carton, 5-ply")
    factories.make_product(company, name="Carton, 3-ply")
    text, markup = empty_state(get(client, "/app/products?q=carton&status=hidden"))
    assert text.startswith("No hidden products match your search")
    assert "Your search matches 2 products, but none of them are hidden." in text
    assert 'href="/app/products?q=carton"' in markup


def test_a_category_that_matches_only_the_other_status(client, signed_in, company):
    factories.make_product(company, name="Carton", category="Packaging", is_active=False)
    factories.make_product(company, name="Rice", category="Staples")
    text, markup = empty_state(get(client, "/app/products?category=Packaging&status=active"))
    assert text.startswith("No active products in “Packaging”")
    assert "“Packaging” has 1 product, but it isn't active." in text.replace("&#39;", "'")
    assert 'href="/app/products?category=Packaging"' in markup


def test_a_search_with_no_products_at_all_still_offers_to_clear_it(client, signed_in, company):
    factories.make_product(company, name="Carton")
    text, _ = empty_state(get(client, "/app/products?q=nothing&status=hidden"))
    assert text.startswith("No products match") and "Try a different search" in text


# Clients -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("invited", "Everyone you've invited has accepted."),
        ("suspended", "No one is suspended."),
    ],
)
def test_an_empty_client_tab_explains_the_tab(client, signed_in, buyer, status, expected):
    html = get(client, f"/app/clients?status={status}")
    text, markup = empty_state(html)
    assert text.startswith(f"No {status} clients") and "match" not in text
    assert expected in text.replace("&#39;", "'")
    assert 'href="/app/clients"' in markup
    assert_clean(html)


def test_the_active_tab_before_anyone_accepts(client, signed_in, company):
    factories.make_client(company, email="new@shop.example", password=None, verified=False)
    text, _ = empty_state(get(client, "/app/clients?status=active"))
    assert text.startswith("No active clients") and "No one has accepted an invitation yet." in text


def test_a_client_search_that_matches_only_other_statuses(client, signed_in, buyer, company):
    factories.make_client(company, email="second@sunrise.example", buyer_company="Sunrise Two")
    text, markup = empty_state(get(client, "/app/clients?q=sunrise&status=suspended"))
    assert text.startswith("No suspended clients match your search")
    assert "Your search matches 2 clients, but none of them are suspended." in text
    assert 'href="/app/clients?q=sunrise"' in markup


def test_a_client_search_with_no_matches_at_all(client, signed_in, buyer):
    text, _ = empty_state(get(client, "/app/clients?q=nobody&status=invited"))
    assert text.startswith("No clients match")


def test_tabs_with_rows_are_unchanged(client, signed_in, buyer, company):
    buyer.active = False
    db.session.commit()
    html = get(client, "/app/clients?status=suspended")
    assert "empty-state" not in html and buyer.email in html
