import html
import re

import pytest

from orderflow.extensions import db
from tests import factories


@pytest.fixture
def shopper(client, login, buyer):
    login(buyer)
    return client


def flashes(client):
    with client.session_transaction() as sess:
        return [message for _category, message in sess.get("_flashes", [])]


def save(client, **fields):
    data = {"first_name": "Ananya", "last_name": "Rao", "phone": ""}
    data.update(fields)
    return client.post("/portal/account", data=data)


def test_account_is_only_for_signed_in_clients(client, login, owner):
    assert "/login" in client.get("/portal/account").headers["Location"]
    assert "/login" in client.post("/portal/account", data={"first_name": "X"}).headers["Location"]
    login(owner)
    assert client.get("/portal/account").headers["Location"].endswith("/app/")
    response = client.post("/portal/account", data={"first_name": "Hacked"})
    assert response.headers["Location"].endswith("/app/")
    assert owner.first_name == "Priya"


def test_account_page_shows_the_clients_details_and_the_supplier(shopper, buyer, company):
    company.legal_name = "Kaveri Supplies Private Limited"
    company.support_email = "orders@kaveri.example"
    company.phone = "+91 80 4000 1234"
    company.address = "14, 2nd Cross, Peenya\nBengaluru 560058"
    company.gstin = "29ABCDE1234F1Z5"
    db.session.commit()

    page = shopper.get("/portal/account").get_data(as_text=True)
    assert 'name="first_name"' in page and 'value="Ananya"' in page
    assert 'maxlength="80"' in page and 'maxlength="32"' in page
    assert buyer.email in page and "Buyer Pvt Ltd" in page
    assert "To change your email address or business name, contact Kaveri Supplies" in page
    assert 'href="/account/password"' in page
    assert "Kaveri Supplies Private Limited" in page
    assert 'href="mailto:orders@kaveri.example"' in page
    assert 'href="tel:+918040001234"' in page
    assert '<dd class="kv__value text-pre-line">14, 2nd Cross, Peenya\nBengaluru 560058</dd>' in page
    assert "29ABCDE1234F1Z5" in page
    assert "None" not in page


def test_a_supplier_without_contact_details_is_handled(shopper, company):
    page = shopper.get("/portal/account").get_data(as_text=True)
    assert "Kaveri Supplies hasn't added contact details here yet." in page
    assert "mailto:" not in page
    assert "None" not in page


def test_updating_details(shopper, buyer):
    response = save(shopper, first_name="  Ananya  ", last_name=" Rao ", phone=" +91 98765 43210 ")
    assert response.headers["Location"] == "/portal/account"
    db.session.refresh(buyer)
    assert (buyer.first_name, buyer.last_name, buyer.phone) == ("Ananya", "Rao", "+91 98765 43210")
    assert flashes(shopper) == ["Your details are saved."]


def test_saving_without_changes_says_nothing_changed(shopper, buyer):
    save(shopper, last_name="Rao", phone="080 4000 1234")
    shopper.get("/portal/account")  # show the first message
    save(shopper, last_name="Rao", phone="080 4000 1234")
    assert flashes(shopper) == ["Nothing to save: your details are the same as before."]


def test_clearing_optional_fields_stores_nothing(shopper, buyer):
    buyer.phone = "080 4000 1234"
    db.session.commit()
    save(shopper, last_name="   ", phone="  ")
    db.session.refresh(buyer)
    assert buyer.last_name is None and buyer.phone is None


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"first_name": ""}, "Enter your first name."),
        ({"first_name": "    "}, "Enter your first name."),
        ({"first_name": "A" * 81}, "Keep your first name to 80 characters or fewer."),
        ({"last_name": "B" * 81}, "Keep your last name to 80 characters or fewer."),
        ({"phone": "1" * 33}, "Keep the phone number to 32 characters or fewer."),
        ({"phone": "call me"}, "Enter a phone number with 7 to 15 digits, like +91 98765 43210."),
        ({"phone": "12345"}, "Enter a phone number with 7 to 15 digits, like +91 98765 43210."),
        ({"phone": "+91 98765 43210 99999"}, "Enter a phone number with 7 to 15 digits, like +91 98765 43210."),
        ({"phone": "٩٨٧٦٥٤٣٢١٠"}, "Enter a phone number with 7 to 15 digits, like +91 98765 43210."),
        ({"phone": "98765²43210"}, "Enter a phone number with 7 to 15 digits, like +91 98765 43210."),
        ({"phone": "98765 43210\n+1"}, "Enter a phone number with 7 to 15 digits, like +91 98765 43210."),
    ],
)
def test_invalid_details_are_explained_and_what_was_typed_is_kept(shopper, buyer, fields, message):
    typed = {"first_name": "Ananya", "last_name": "Typed surname", **fields}
    response = save(shopper, **typed)
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert message in page
    assert 'aria-invalid="true"' in page
    # What was typed is still in the form (after trimming)
    for name in ("first_name", "last_name", "phone"):
        value = html.escape(typed.get(name, "").strip())
        assert re.search(rf'name="{name}"[^>]*value="{re.escape(value)}"', page), name
    db.session.refresh(buyer)
    assert buyer.first_name == "Ananya" and buyer.last_name == "Test"


@pytest.mark.parametrize("phone", ["(080) 4000 1234", "+91-98765-43210", "080.4000.1234", "9876543210"])
def test_common_phone_formats_are_accepted(shopper, buyer, phone):
    assert save(shopper, phone=phone).status_code == 302
    db.session.refresh(buyer)
    assert buyer.phone == phone


def test_names_in_any_script_are_accepted(shopper, buyer):
    save(shopper, first_name="अनन्या", last_name="राव")
    db.session.refresh(buyer)
    assert buyer.full_name == "अनन्या राव"


def test_email_and_business_cannot_be_changed_here(shopper, buyer):
    save(shopper, email="new@evil.example", buyer_company="Someone Else", role="owner", company_id="999")
    db.session.refresh(buyer)
    assert buyer.email == "buyer@sunrise.example"
    assert buyer.buyer_company == "Buyer Pvt Ltd"
    assert buyer.role == "client"


def test_a_client_only_ever_edits_their_own_account(shopper, buyer, company):
    neighbour = factories.make_client(company, email="neighbour@buyer.example", first_name="Vikram")
    save(shopper, first_name="Changed")
    db.session.refresh(neighbour)
    assert neighbour.first_name == "Vikram"
