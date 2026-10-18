"""The ?next= link after sign-in only ever leads to a page on this site."""

import pytest

from orderflow.permissions import is_safe_next_url
from tests import factories

OFF_SITE = [
    "https://evil.example/",
    "http://evil.example",
    "//evil.example",
    "///evil.example",
    "/\\evil.example",
    "\\\\evil.example",
    "/\\/evil.example",
    # One slash after the scheme: the redirect writes it as https:///evil.example,
    # which browsers open as https://evil.example
    "https:/evil.example/phish",
    "http:/evil.example/phish",
    "HTTPS:/evil.example",
    "https:///evil.example/phish",
    "https:evil.example",
    "javascript:alert(1)",
    "data:text/html,hello",
    "evil.example",
    "app/orders",
    # Browsers drop tabs and newlines, which would leave //evil.example
    "/\t/evil.example",
    "/\n/evil.example",
    "/\r/evil.example",
    "\t//evil.example",
    " //evil.example",
    "/\x7f/evil.example",
    "",
    None,
]

ON_SITE = [
    "/app/orders",
    "/app/products?q=tape&page=2",
    "/portal/catalog?page=2",
    "/app/orders?q=a:b",
    "/app/orders/ORD-000001",
]


@pytest.mark.parametrize("target", OFF_SITE)
def test_off_site_targets_are_refused(app, target):
    with app.test_request_context(base_url="https://orderflow.example"):
        assert not is_safe_next_url(target)


@pytest.mark.parametrize("target", ON_SITE)
def test_paths_on_this_site_are_allowed(app, target):
    with app.test_request_context(base_url="https://orderflow.example"):
        assert is_safe_next_url(target)


@pytest.mark.parametrize("base_url", ["http://localhost", "https://orderflow.example"])
@pytest.mark.parametrize("target", [t for t in OFF_SITE if t])
def test_sign_in_never_redirects_off_site(client, owner, base_url, target):
    response = client.post(
        "/login",
        query_string={"next": target},
        data={"email": owner.email, "password": factories.PASSWORD},
        base_url=base_url,
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/app/"


def test_sign_in_still_returns_to_a_page_on_this_site(client, owner):
    response = client.post(
        "/login",
        query_string={"next": "/app/products?q=tape&page=2"},
        data={"email": owner.email, "password": factories.PASSWORD},
        base_url="https://orderflow.example",
    )
    assert response.headers["Location"] == "/app/products?q=tape&page=2"
