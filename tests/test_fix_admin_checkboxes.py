"""The product and bulletin checkboxes read "0", "off" and "no" as unticked."""

import pytest

from orderflow.extensions import db
from orderflow.models import Bulletin, Product
from tests import factories

PRODUCT = {"name": "Carton", "sku": "CTN-9", "unit": "box", "price": "42.50", "gst_rate": "18"}
BULLETIN = {"title": "Holiday notice", "body": "Closed on Monday."}
UNTICKED = ["0", "false", "FALSE", "off", "Off", "no", "NO", "n"]
TICKED = ["y", "1", "on", "yes", "true", "anything"]


@pytest.fixture
def signed_in(login, owner):
    login(owner)
    return owner


def only(model):
    rows = db.session.scalars(db.select(model)).all()
    assert len(rows) == 1
    return rows[0]


@pytest.mark.parametrize("value", UNTICKED)
def test_unticked_values_save_a_draft_bulletin(client, signed_in, value):
    response = client.post("/app/bulletins/new", data={**BULLETIN, "publish": value})
    assert response.status_code == 302
    assert only(Bulletin).is_published is False


@pytest.mark.parametrize("value", TICKED)
def test_ticked_values_publish_the_bulletin(client, signed_in, value):
    client.post("/app/bulletins/new", data={**BULLETIN, "publish": value})
    assert only(Bulletin).is_published is True


def test_publish_zero_on_edit_unpublishes(client, signed_in, company):
    bulletin = Bulletin(company_id=company.id, title="Live", body="Hello", is_published=True)
    db.session.add(bulletin)
    db.session.commit()
    client.post(f"/app/bulletins/{bulletin.id}/edit", data={**BULLETIN, "publish": "0"})
    assert db.session.get(Bulletin, bulletin.id).is_published is False


@pytest.mark.parametrize("value", UNTICKED)
def test_unticked_values_add_a_hidden_product(client, signed_in, value):
    response = client.post("/app/products/new", data={**PRODUCT, "is_active": value})
    assert response.status_code == 302
    assert only(Product).is_active is False


@pytest.mark.parametrize("value", TICKED)
def test_ticked_values_add_a_visible_product(client, signed_in, value):
    client.post("/app/products/new", data={**PRODUCT, "is_active": value})
    assert only(Product).is_active is True


def test_is_active_zero_on_edit_hides_the_product(client, signed_in, company):
    product = factories.make_product(company, sku="CTN-9", name="Carton")
    client.post(f"/app/products/{product.id}/edit", data={**PRODUCT, "is_active": "0"})
    assert db.session.get(Product, product.id).is_active is False


def test_the_forms_still_default_to_ticked(client, signed_in):
    for path, name in (("/app/products/new", "is_active"), ("/app/bulletins/new", "publish")):
        html = client.get(path).get_data(as_text=True)
        assert f'checked class="check__input" id="{name}" name="{name}" type="checkbox" value="y"' in html
