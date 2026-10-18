"""The public pages: landing page, contact form, legal pages and error pages."""

import re

import pytest

from orderflow import create_app
from orderflow.extensions import db
from orderflow.models import Lead
from tests.design_helpers import Page, page

LANDING_SECTIONS = ("features", "how-it-works", "pricing", "faq", "contact")


# --------------------------------------------------------------------------
# Landing page
# --------------------------------------------------------------------------


def test_landing_page_has_every_section(client):
    response = client.get("/")
    assert response.status_code == 200
    html = page(response)
    for section in LANDING_SECTIONS:
        assert section in html.ids, f"missing #{section}"
    text = html.text
    assert "Wholesale orders, placed online and approved in a click." in text
    for audience in ("Distributors", "Manufacturers", "Wholesalers", "HoReCa suppliers"):
        assert audience in text
    assert "Up and running in three steps" in text
    assert "Free during early access" in text
    assert "Questions, answered" in text
    assert "Talk to us" in text
    assert "Give your clients a better way to order." in text


def test_landing_page_calls_to_action(client):
    html = page(client.get("/"))
    start_links = [a for a in html.find_all("a") if a.get("href") == "/signup"]
    # Header, hero, pricing and the closing panel all lead to sign-up
    assert len(start_links) >= 4
    hero = html.with_class("hero__actions")
    assert hero, "hero actions missing"
    assert "#how-it-works" in html.hrefs()


def test_landing_page_describes_only_real_features(client):
    text = page(client.get("/")).text
    for feature in (
        "private catalog",
        "Reorder a past order",
        "proforma invoice",
        "reject",
        "GST on every line",
        "Email at every step",
        "top products and your top clients",
        "CSV",
        "Invite-only accounts",
        "rate limited",
        "HTTPS",
    ):
        assert feature in text, feature
    # No invented prices, testimonials or statistics
    assert "/month" not in text and "per month" not in text
    assert "testimonial" not in text.lower()
    assert "None" not in text


def test_landing_page_money_uses_rupees_with_indian_grouping(client):
    text = page(client.get("/")).text
    assert "₹6,869.96" in text  # total of the example order
    assert "₹1,047.96" in text  # its 18% GST
    assert "₹57,918.00" in text
    assert "$" not in text and "Rs." not in text


def test_landing_faq_uses_details_and_one_is_open(client):
    html = page(client.get("/"))
    details = html.find_all("details")
    assert len(details) >= 5
    assert sum("open" in d for d in details) == 1
    assert len(html.find_all("summary")) == len(details)


def test_landing_page_has_meta_description_and_open_graph(client):
    html = page(client.get("/"))
    description = html.find_all("meta", name="description")
    assert description and "private catalog" in description[0]["content"]
    og = {m["property"]: m["content"] for m in html.find_all("meta") if m.get("property", "").startswith("og:")}
    assert og["og:type"] == "website"
    assert og["og:title"].startswith("OrderFlow")
    assert og["og:url"] == "http://localhost/"
    assert og["og:image"] == "http://localhost/static/img/icon-512.png"
    assert html.find_all("link", rel="canonical")[0]["href"] == "http://localhost/"
    title = re.search(r"<title>(.*?)</title>", client.get("/").get_data(as_text=True)).group(1)
    assert title == "Wholesale ordering for suppliers · OrderFlow"


def test_landing_page_in_page_links_all_have_targets(client):
    html = page(client.get("/"))
    for href in html.hrefs():
        if href.startswith("#") or href.startswith("/#"):
            target = href.split("#", 1)[1]
            assert target in html.ids, f"{href} points nowhere"


def test_landing_page_internal_links_work(client):
    html = page(client.get("/"))
    paths = {href.split("#")[0] for href in html.hrefs() if href.startswith("/") and not href.startswith("//")}
    for path in paths - {""}:
        assert client.get(path).status_code in (200, 302), path


def test_hero_example_order_reads_as_finished_order_without_animation(client):
    html = page(client.get("/"))
    # Only the final, fulfilled badge is exposed to assistive technology
    states = html.with_class("hero-order__state")
    assert len(states) == 3
    assert [s.get("aria-hidden") for s in states] == ["true", "true", None]
    assert "Fulfilled by Arjun Das" in html.text
    caption = html.find_all("figcaption")
    assert caption and caption[0]["id"] == "hero-visual-caption"


def test_landing_shows_open_app_link_when_signed_in(client, login, owner):
    login(owner)
    html = page(client.get("/"))
    assert "/start" in html.hrefs()
    assert "Open OrderFlow" in html.text
    assert not html.with_class("site-nav__signin")


def test_landing_header_offers_sign_in_when_signed_out(client):
    html = page(client.get("/"))
    assert [a["href"] for a in html.with_class("site-nav__signin")] == ["/login"]
    assert "Open OrderFlow" not in html.text


# --------------------------------------------------------------------------
# Contact form
# --------------------------------------------------------------------------


def test_contact_form_renders_every_field(client):
    html = page(client.get("/"))
    forms = [f for f in html.find_all("form") if f.get("action") == "/contact"]
    assert len(forms) == 1 and forms[0]["method"] == "post"
    for name in ("name", "email", "company", "message"):
        assert html.find_all(id=name), name
    labels = {label.get("for") for label in html.find_all("label")}
    assert {"name", "email", "company", "message"} <= labels
    # Browser checks catch mistakes before the form is sent
    assert "required" in html.find_all("input", id="name")[0]
    assert html.find_all("input", id="email")[0]["type"] == "email"
    assert html.find_all("input", id="name")[0]["maxlength"] == "120"
    assert html.find_all("textarea", id="message")[0]["maxlength"] == "2000"


@pytest.fixture
def csrf_app():
    app = create_app("testing", WTF_CSRF_ENABLED=True)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_contact_form_posts_with_its_csrf_token(csrf_app):
    client = csrf_app.test_client()
    html = page(client.get("/"))
    token = [i for i in html.find_all("input", name="csrf_token") if i.get("id") == "csrf_token"]
    assert token, "the contact form has no CSRF token"
    data = {"name": "Meera Iyer", "email": "meera@iyertraders.example", "company": "", "message": "Packaging"}

    refused = client.post("/contact", data=data)
    assert refused.status_code == 400
    assert db.session.scalar(db.select(db.func.count(Lead.id))) == 0

    response = client.post("/contact", data={**data, "csrf_token": token[0]["value"]})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/#contact")
    lead = db.session.scalar(db.select(Lead))
    assert lead.name == "Meera Iyer" and lead.company is None


def test_contact_messages_appear_next_to_the_form(client, mail):
    response = client.post(
        "/contact", data={"name": "Meera", "email": "meera@iyertraders.example"}, follow_redirects=True
    )
    body = response.get_data(as_text=True)
    contact = body[body.index('id="contact"') :]
    assert "We&#39;ll reply to you by email" in contact
    # ...and not also at the top of the page
    assert body.count("We&#39;ll reply to you by email") == 1
    assert len(mail) == 1 and mail[0].to == ["sales@orderflow.test"]


def test_contact_validation_errors_show_next_to_fields_and_keep_input(client):
    response = client.post(
        "/contact",
        data={"name": "", "email": "not-an-email", "company": "Kiran Stores", "message": "40 retail stores"},
    )
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    contact = body[body.index('id="contact"') :]
    assert contact.count("field__error") == 2  # name and email
    assert 'value="Kiran Stores"' in contact and "40 retail stores" in contact
    assert 'aria-invalid="true"' in contact
    assert db.session.scalar(db.select(db.func.count(Lead.id))) == 0


# --------------------------------------------------------------------------
# Legal pages
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "heading", "phrase"),
    [
        ("/privacy", "Privacy Policy", "We don't sell personal information"),
        ("/terms", "Terms of Service", "You keep ownership of the data"),
    ],
)
def test_legal_pages_render_as_prose(client, path, heading, phrase):
    response = client.get(path)
    assert response.status_code == 200
    html = page(response)
    assert html.with_class("prose")
    assert heading in html.text and phrase in html.text
    assert "Last updated 18 October 2026" in html.text
    assert "[contact email]" not in html.text
    assert "mailto:sales@orderflow.test" in html.hrefs()


def test_legal_pages_point_to_the_contact_form_without_a_contact_email(app, client):
    app.config["CONTACT_EMAIL"] = None
    for path in ("/privacy", "/terms"):
        html = page(client.get(path))
        assert "/#contact" in html.hrefs()
        assert "the contact form on our website" in html.text
        assert "None" not in html.text


# --------------------------------------------------------------------------
# Error pages
# --------------------------------------------------------------------------


def test_404_page(client):
    response = client.get("/no-such-page")
    assert response.status_code == 404
    html = page(response)
    assert "Error 404" in html.text
    assert "We couldn't find that page" in html.text
    assert "/" in html.hrefs() and "/login" in html.hrefs()


def test_405_page(client):
    response = client.get("/logout")
    assert response.status_code == 405
    assert "That action isn't allowed here" in page(response).text


def test_expired_form_page(csrf_app):
    response = csrf_app.test_client().post("/login", data={"email": "a@example.com", "password": "x"})
    assert response.status_code == 400
    assert "This form has expired" in page(response).text


def test_500_page_is_friendly(app):
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.route("/__boom")
    def boom():
        raise RuntimeError("boom")

    response = app.test_client().get("/__boom")
    assert response.status_code == 500
    html = page(response)
    assert "Something went wrong on our side" in html.text
    assert "boom" not in html.text


def test_error_page_sends_signed_in_people_back_to_their_home(client, login, owner, buyer):
    login(owner)
    html = page(client.get("/no-such-page"))
    assert "Back to your workspace" in html.text and "/start" in html.hrefs()
    client.post("/logout")
    login(buyer)
    assert "Back to your portal" in page(client.get("/no-such-page")).text


def test_page_parser_helper_handles_scripts_and_classes():
    html = Page('<div class="a b" id="x"><script>var s = "<p>";</script><p>Hi</p></div>')
    assert html.text == "Hi"
    assert html.ids == {"x"}
    assert html.with_class("b")
