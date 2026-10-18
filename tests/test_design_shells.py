"""The shells every page lives in: base, marketing, auth, workspace and portal."""

import pytest
from flask import render_template_string
from flask_login import login_user

from orderflow.extensions import db
from orderflow.services.accounts import invite_user
from tests import factories
from tests.conftest import link_from
from tests.design_helpers import Page, page

APP_PAGE = (
    '{% extends "layouts/app.html" %}{% block title %}Orders{% endblock %}{% block content %}<p>Body</p>{% endblock %}'
)


def render_raw(app, user, source, **context):
    """Render a template string inside a request, signed in as user."""
    with app.test_request_context("/"):
        login_user(user)
        return render_template_string(source, **context)


def render_as(app, user, source, **context):
    return Page(render_raw(app, user, source, **context))


# --------------------------------------------------------------------------
# Base template
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/login", "/privacy", "/no-such-page"])
def test_every_page_has_skip_link_icons_and_manifest(client, path):
    html = page(client.get(path))
    skip = html.with_class("skip-link")
    assert skip and skip[0]["href"] == "#main"
    assert "main" in html.ids
    links = {(link.get("rel"), link.get("href")) for link in html.find_all("link")}
    assert ("icon", "/static/img/favicon.svg") in links
    assert ("icon", "/static/img/icon-192.png") in links
    assert ("apple-touch-icon", "/static/img/apple-touch-icon.png") in links
    assert ("manifest", "/static/site.webmanifest") in links
    assert html.find_all("html", lang="en")
    scripts = html.find_all("script")
    assert scripts and all(s.get("src", "").startswith("/static/js/app.js") for s in scripts)


def test_page_titles_name_the_page_then_the_product(client):
    body = client.get("/privacy").get_data(as_text=True)
    assert "<title>Privacy Policy · OrderFlow</title>" in body


# --------------------------------------------------------------------------
# Marketing layout
# --------------------------------------------------------------------------


def test_marketing_header_and_footer(client):
    html = page(client.get("/privacy"))
    toggle = html.with_class("site-header__toggle")[0]
    assert toggle["aria-controls"] == "site-nav" and toggle["aria-expanded"] == "false"
    assert "data-nav-toggle" in toggle
    assert "site-nav" in html.ids
    hrefs = html.hrefs()
    for href in ("/#features", "/#how-it-works", "/#pricing", "/#faq", "/#contact", "/privacy", "/terms", "/signup"):
        assert href in hrefs, href
    assert "© " in html.text and "OrderFlow" in html.text


def test_marketing_footer_shows_the_legal_name(app, client):
    app.config["LEGAL_NAME"] = "Kaveri Labs Private Limited"
    assert "Kaveri Labs Private Limited" in page(client.get("/terms")).text


# --------------------------------------------------------------------------
# Sign-in pages
# --------------------------------------------------------------------------


def test_auth_layout_has_brand_panel_and_footer_links(client):
    html = page(client.get("/login"))
    aside = html.with_class("auth__aside")
    assert aside and aside[0]["aria-hidden"] == "true"
    assert html.with_class("auth-visual__order")
    assert {"/privacy", "/terms"} <= set(html.hrefs())
    assert html.with_class("auth__brand")[0]["href"] == "/"


def test_login_page_keeps_its_fields_and_links(client):
    html = page(client.get("/login"))
    assert html.find_all("input", id="email")[0]["autocomplete"] == "email"
    assert html.find_all("input", id="password")[0]["type"] == "password"
    assert html.find_all("input", id="remember")[0]["type"] == "checkbox"
    assert {"/forgot-password", "/signup"} <= set(html.hrefs())
    assert "Keep me signed in on this device" in html.text


def test_login_error_keeps_the_email_and_marks_nothing_as_none(client, owner):
    response = client.post("/login", data={"email": owner.email, "password": "wrong password here"})
    html = page(response)
    assert html.find_all("input", id="email")[0]["value"] == owner.email
    assert "That email and password don't match" in html.text
    assert "None" not in html.text


def test_signup_links_the_terms_and_shows_errors_next_to_fields(client):
    html = page(client.get("/signup"))
    assert {"/terms", "/privacy", "/login"} <= set(html.hrefs())
    response = client.post("/signup", data={"first_name": "Meera", "email": "meera@", "password": "short"})
    html = page(response)
    errors = html.with_class("field__error")
    assert errors, "no field errors shown"
    assert html.find_all("input", id="first_name")[0]["value"] == "Meera"
    assert html.find_all("input", id="email")[0]["aria-invalid"] == "true"


def test_forgot_password_pages(client, owner, mail):
    assert "Reset your password" in page(client.get("/forgot-password")).text
    html = page(client.post("/forgot-password", data={"email": owner.email}))
    assert "Check your email" in html.text and owner.email in html.text


def test_invalid_links_get_a_clear_page(client):
    for path in ("/reset-password/not-a-token", "/invite/not-a-token"):
        response = client.get(path)
        assert response.status_code in (200, 400, 404)
        html = page(response)
        assert html.with_class("auth__icon--warning") or html.with_class("error-page")


def test_accept_invite_page(client, owner, mail):
    invited = invite_user(owner.company, owner, "new@sunrise.example", "client", "Ravi", buyer_company="Ravi Stores")
    html = page(client.get(link_from(mail[-1], "/invite/")))
    assert "Welcome, Ravi" in html.text
    assert owner.company.name in html.text and invited.email in html.text


def test_verify_pending_page(client):
    user = factories.make_owner(factories.make_company("Iyer Traders"), email="meera@iyer.example", verified=False)
    client.post("/login", data={"email": user.email, "password": factories.PASSWORD})
    html = page(client.get("/verify"))
    assert "Check your inbox" in html.text and user.email in html.text
    assert [f["action"] for f in html.find_all("form")] == ["/verify/resend", "/logout"]


@pytest.mark.parametrize(("who", "shell"), [("owner", "sidebar"), ("buyer", "portal-header")])
def test_change_password_uses_the_right_shell(client, login, request, who, shell):
    login(request.getfixturevalue(who))
    html = page(client.get("/account/password"))
    assert html.with_class(shell)
    active = html.find_all("a", **{"aria-current": "page"})
    assert active and active[0]["href"] in ("/app/settings", "/portal/account")


# --------------------------------------------------------------------------
# Workspace (staff) layout
# --------------------------------------------------------------------------


def test_workspace_navigation_for_owner_and_admin(client, login, owner, admin):
    login(owner)
    html = page(client.get("/app/"))
    labels = [a["href"] for a in html.with_class("nav-link") if a.get("href")]
    assert labels == [
        "/app/",
        "/app/orders",
        "/app/products",
        "/app/clients",
        "/app/bulletins",
        "/app/team",
        "/app/settings",
    ]
    assert owner.company.name in html.text and "Owner" in html.text
    client.post("/logout")
    login(admin)
    html = page(client.get("/app/"))
    assert "/app/team" not in html.hrefs()
    assert "Admin" in html.text


def test_workspace_drawer_toggles_point_at_the_sidebar(client, login, owner):
    login(owner)
    html = page(client.get("/app/"))
    toggles = [e for e in html.with_class("menu-button") if "data-sidebar-toggle" in e]
    assert len(toggles) == 2
    assert {t["aria-controls"] for t in toggles} == {"sidebar"}
    assert {t["aria-expanded"] for t in toggles} == {"false"}
    assert "sidebar" in html.ids
    sign_out = [f for f in html.find_all("form") if f.get("action") == "/logout"]
    assert sign_out and sign_out[0]["method"] == "post"


def test_topbar_title_falls_back_to_the_page_title(app, owner):
    assert '<div class="topbar__title">Orders</div>' in render_raw(app, owner, APP_PAGE)
    source = APP_PAGE.replace(
        "{% block content %}", "{% block topbar_title %}All orders{% endblock %}{% block content %}"
    )
    assert '<div class="topbar__title">All orders</div>' in render_raw(app, owner, source)


def test_active_nav_item_is_marked(app, owner):
    html = render_as(app, owner, APP_PAGE, active_nav="orders")
    current = html.find_all("a", **{"aria-current": "page"})
    assert [a["href"] for a in current] == ["/app/orders"]
    assert "is-active" in current[0]["class"]


def test_nav_badges_show_counts(app, owner):
    html = render_as(app, owner, APP_PAGE, nav_badges={"orders": 4, "clients": 0, "products": "lots"})
    counts = html.with_class("nav-link__count")
    assert len(counts) == 1
    assert "4 waiting" in html.text
    html = render_as(app, owner, APP_PAGE, nav_badges={"orders": 12_345})
    assert "999+" in html.text
    # Without nav_badges nothing is shown, and nothing breaks
    assert not render_as(app, owner, APP_PAGE).with_class("nav-link__count")
    assert not render_as(app, owner, APP_PAGE, nav_badges=["orders"]).with_class("nav-link__count")


# --------------------------------------------------------------------------
# Client portal layout
# --------------------------------------------------------------------------


def test_portal_header_shows_the_supplier(client, login, buyer):
    login(buyer)
    html = page(client.get("/portal/"))
    assert html.with_class("brand__monogram")
    assert "Kaveri Supplies" in html.text and "Ordering portal" in html.text
    assert [a["href"] for a in html.with_class("nav-link") if a.get("href")] == [
        "/portal/",
        "/portal/catalog",
        "/portal/orders",
        "/portal/account",
    ]
    toggle = html.with_class("portal-header__toggle")[0]
    assert toggle["aria-controls"] == "portal-nav"
    assert html.with_class("cart-link")[0]["href"] == "/portal/cart"
    assert "Powered by OrderFlow" in html.text


@pytest.mark.parametrize(
    ("cart", "shown"),
    [
        ({"1": 40, "3": 24, "8": 5}, "69"),
        ({"1": 1}, "1"),
        ({"1": 2500}, "999+"),
        ({}, None),
        (None, None),
        (["1", "2"], None),
        ({"1": "many"}, None),
    ],
)
def test_portal_cart_count(client, login, buyer, cart, shown):
    login(buyer)
    with client.session_transaction() as session:
        session["cart"] = cart
    response = client.get("/portal/")
    assert response.status_code == 200
    counts = page(response).with_class("cart-link__count")
    if shown is None:
        assert not counts
    else:
        assert counts
        assert f'class="cart-link__count">{shown}<' in response.get_data(as_text=True)


def test_portal_footer_lists_only_the_contact_details_that_exist(client, login, buyer):
    login(buyer)
    html = page(client.get("/portal/"))
    assert "None" not in html.text
    assert not [h for h in html.hrefs() if h.startswith(("mailto:", "tel:"))]

    buyer.company.support_email = "orders@kaveri.example"
    buyer.company.phone = "+91 80 4000 1234"
    db.session.commit()
    html = page(client.get("/portal/"))
    assert "mailto:orders@kaveri.example" in html.hrefs()
    assert "tel:+918040001234" in html.hrefs()


def test_portal_active_cart_link_is_marked(app, buyer):
    source = '{% extends "layouts/portal.html" %}{% block content %}x{% endblock %}'
    html = render_as(app, buyer, source, active_nav="cart")
    cart = html.with_class("cart-link")[0]
    assert cart["aria-current"] == "page" and "is-active" in cart["class"]
