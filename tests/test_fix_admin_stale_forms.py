"""A product or bulletin edit form opened before its visibility changed
doesn't quietly undo that change when it's saved."""

from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

import pytest

from orderflow import timeutil
from orderflow.extensions import db
from orderflow.models import Bulletin, Product
from tests import factories

NOW = datetime(2026, 10, 18, 3, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(timeutil, "utcnow", lambda: NOW)


@pytest.fixture
def signed_in(login, owner):
    login(owner)
    return owner


class FormValues(HTMLParser):
    """What a browser would submit for the main (method=post, no action) form on a page."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.values = {}
        self._in_form = False
        self._textarea = None
        self._select = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("method") == "post" and "action" not in attrs:
            self._in_form = True
        if not self._in_form:
            return
        name = attrs.get("name")
        if tag == "input" and name:
            kind = attrs.get("type", "text")
            if kind == "checkbox":
                if "checked" in attrs:
                    self.values[name] = attrs.get("value", "on")
            elif kind not in ("submit", "button"):
                self.values[name] = attrs.get("value", "")
        elif tag == "textarea" and name:
            self._textarea = name
            self.values[name] = ""
        elif tag == "select" and name:
            self._select = name
        elif tag == "option" and self._select and "selected" in attrs:
            self.values[self._select] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self._in_form = False
        elif tag == "textarea":
            self._textarea = None
        elif tag == "select":
            self._select = None

    def handle_data(self, data):
        if self._textarea:
            self.values[self._textarea] += data.lstrip("\n") if not self.values[self._textarea] else data


def open_form(client, path):
    response = client.get(path)
    assert response.status_code == 200
    return FormValues(response.get_data(as_text=True)).values


def text(response):
    return response.get_data(as_text=True)


# Products ----------------------------------------------------------------------


@pytest.fixture
def product(company):
    return factories.make_product(company, sku="TP-1", name="Tape", unit="roll", price_paise=5_000)


def test_the_edit_form_carries_the_visibility_it_was_opened_with(client, signed_in, product):
    form = open_form(client, f"/app/products/{product.id}/edit")
    assert form["is_active"] == "y" and form["is_active_was"] == "1"

    product.is_active = False
    db.session.commit()
    form = open_form(client, f"/app/products/{product.id}/edit")
    assert "is_active" not in form and form["is_active_was"] == "0"


def test_a_stale_form_keeps_a_product_hidden_since_it_was_opened(client, signed_in, product):
    stale = open_form(client, f"/app/products/{product.id}/edit")
    client.post(f"/app/products/{product.id}/status", data={"active": "0"})
    assert db.session.get(Product, product.id).is_active is False

    response = client.post(
        f"/app/products/{product.id}/edit", data={**stale, "name": "Stale renamed"}, follow_redirects=True
    )
    saved = db.session.get(Product, product.id)
    assert saved.name == "Stale renamed"
    assert saved.is_active is False  # not shown to clients again
    assert "It was hidden from clients while you had this page open, so it&#39;s still hidden." in text(response)


def test_a_stale_form_keeps_a_product_visible_since_it_was_opened(client, signed_in, product):
    product.is_active = False
    db.session.commit()
    stale = open_form(client, f"/app/products/{product.id}/edit")
    client.post(f"/app/products/{product.id}/status", data={"active": "1"})

    response = client.post(f"/app/products/{product.id}/edit", data=stale, follow_redirects=True)
    assert db.session.get(Product, product.id).is_active is True
    assert "No changes to save for “Tape”." in text(response)
    assert "It was shown to clients while you had this page open, so it&#39;s still visible." in text(response)


def test_changing_the_checkbox_still_hides_and_shows(client, signed_in, product):
    form = open_form(client, f"/app/products/{product.id}/edit")
    form.pop("is_active")
    response = client.post(f"/app/products/{product.id}/edit", data=form, follow_redirects=True)
    assert db.session.get(Product, product.id).is_active is False
    assert "It&#39;s hidden from clients now." in text(response)
    assert "while you had this page open" not in text(response)

    form = open_form(client, f"/app/products/{product.id}/edit")
    form["is_active"] = "y"
    response = client.post(f"/app/products/{product.id}/edit", data=form, follow_redirects=True)
    assert db.session.get(Product, product.id).is_active is True
    assert "Clients can order it now." in text(response)


def test_hiding_in_a_stale_form_hides_a_product_shown_meanwhile(client, signed_in, product):
    """The person unticked the box, so that is what they want, whatever happened meanwhile."""
    stale = open_form(client, f"/app/products/{product.id}/edit")
    stale.pop("is_active")
    client.post(f"/app/products/{product.id}/status", data={"active": "0"})
    client.post(f"/app/products/{product.id}/status", data={"active": "1"})
    client.post(f"/app/products/{product.id}/edit", data=stale)
    assert db.session.get(Product, product.id).is_active is False


def test_a_form_without_the_hidden_copy_saves_the_checkbox_as_sent(client, signed_in, product):
    data = {"name": "Tape", "sku": "TP-1", "unit": "roll", "price": "50", "gst_rate": "18"}
    client.post(f"/app/products/{product.id}/edit", data=data)
    assert db.session.get(Product, product.id).is_active is False
    client.post(f"/app/products/{product.id}/edit", data={**data, "is_active": "y", "is_active_was": "junk"})
    assert db.session.get(Product, product.id).is_active is True


def test_a_rejected_stale_form_keeps_its_original_copy(client, signed_in, product):
    stale = open_form(client, f"/app/products/{product.id}/edit")
    client.post(f"/app/products/{product.id}/status", data={"active": "0"})
    response = client.post(f"/app/products/{product.id}/edit", data={**stale, "price": "abc"})
    again = FormValues(text(response)).values
    assert again["is_active_was"] == "1" and again["is_active"] == "y"
    client.post(f"/app/products/{product.id}/edit", data={**again, "price": "51"})
    saved = db.session.get(Product, product.id)
    assert saved.price_paise == 5_100 and saved.is_active is False


# Bulletins ---------------------------------------------------------------------


def make_bulletin(company, published=True, expires_at=None):
    bulletin = Bulletin(
        company_id=company.id, title="Sale", body="Ends soon", is_published=published, expires_at=expires_at
    )
    db.session.add(bulletin)
    db.session.commit()
    return bulletin


def test_a_stale_form_keeps_a_bulletin_unpublished_since_it_was_opened(client, signed_in, company):
    bulletin = make_bulletin(company)
    stale = open_form(client, f"/app/bulletins/{bulletin.id}/edit")
    assert stale["publish"] == "y" and stale["publish_was"] == "1"
    client.post(f"/app/bulletins/{bulletin.id}/status", data={"published": "0"})

    response = client.post(
        f"/app/bulletins/{bulletin.id}/edit", data={**stale, "title": "Sale extended"}, follow_redirects=True
    )
    saved = db.session.get(Bulletin, bulletin.id)
    assert saved.title == "Sale extended" and saved.is_published is False
    assert "It was unpublished while you had this page open, so we left it as a draft." in text(response)
    assert "It&#39;s a draft, so clients won&#39;t see it until you publish it." in text(response)


def test_a_stale_form_keeps_a_bulletin_published_since_it_was_opened(client, signed_in, company):
    bulletin = make_bulletin(company, published=False)
    stale = open_form(client, f"/app/bulletins/{bulletin.id}/edit")
    assert "publish" not in stale and stale["publish_was"] == "0"
    client.post(f"/app/bulletins/{bulletin.id}/status", data={"published": "1"})

    response = client.post(f"/app/bulletins/{bulletin.id}/edit", data=stale, follow_redirects=True)
    assert db.session.get(Bulletin, bulletin.id).is_published is True
    assert "No changes to save for “Sale”." in text(response)
    assert "It was published while you had this page open, so we left it published." in text(response)


def test_a_kept_draft_may_keep_an_end_time_that_has_passed(client, signed_in, company):
    bulletin = make_bulletin(company, expires_at=NOW + timedelta(hours=1))
    stale = open_form(client, f"/app/bulletins/{bulletin.id}/edit")
    client.post(f"/app/bulletins/{bulletin.id}/status", data={"published": "0"})
    past = timeutil.local_input_value(NOW - timedelta(hours=2), "Asia/Kolkata")
    response = client.post(f"/app/bulletins/{bulletin.id}/edit", data={**stale, "expires_at": past})
    assert response.status_code == 302
    saved = db.session.get(Bulletin, bulletin.id)
    assert saved.is_published is False and timeutil.as_utc(saved.expires_at) == NOW - timedelta(hours=2)


def test_unticking_publish_still_unpublishes(client, signed_in, company):
    bulletin = make_bulletin(company)
    form = open_form(client, f"/app/bulletins/{bulletin.id}/edit")
    form.pop("publish")
    client.post(f"/app/bulletins/{bulletin.id}/edit", data=form)
    assert db.session.get(Bulletin, bulletin.id).is_published is False

    form = open_form(client, f"/app/bulletins/{bulletin.id}/edit")
    form["publish"] = "y"
    client.post(f"/app/bulletins/{bulletin.id}/edit", data=form)
    assert db.session.get(Bulletin, bulletin.id).is_published is True
