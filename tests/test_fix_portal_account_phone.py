"""Clients and staff use one phone rule, so a number staff saved never blocks the client.

Before, staff could save numbers such as "123456" or "080-4000-1234-" that the
client's account form refused, so a client who only changed their last name
got an error on a phone field they never touched.
"""

import re

import pytest

from orderflow.admin.forms import ClientForm
from orderflow.extensions import db
from orderflow.portal.forms import AccountForm
from tests import factories

# Numbers the staff client form accepts that the old client rule refused
STAFF_ONLY_BEFORE = ["123456", "080-4000-1234-", "98450 12345.", "+91 +98450 12345", "-080 4000 1234", "98450 12345 ()"]

CORPUS = [
    *STAFF_ONLY_BEFORE,
    "+91 98765 43210",
    "(080) 4000 1234",
    "080.4000.1234",
    "9876543210",
    "+91  98765\t43210",
    "98765 43210\n+1",
    "12345",
    "1" * 16,
    "call me",
    "+91 (80) abc",
    "٩٨٧٦٥٤٣٢١٠",
    "１２３４５６７",
    "98765²43210",
    "98765 43210 ext 5",
]


def stored(user):
    db.session.refresh(user)
    return user


def sign_in(app, user):
    browser = app.test_client()
    response = browser.post("/login", data={"email": user.email, "password": factories.PASSWORD})
    assert response.status_code == 302
    return browser


def prefilled_phone(page):
    match = re.search(r'<input[^>]*name="phone"[^>]*>', page)
    value = re.search(r'value="([^"]*)"', match.group(0))
    return value.group(1) if value else ""


@pytest.mark.parametrize("phone", STAFF_ONLY_BEFORE)
def test_a_number_staff_saved_never_stops_the_client_saving_their_details(app, owner, buyer, phone):
    staff = sign_in(app, owner)
    edit = {
        "first_name": "Ananya",
        "last_name": "",
        "buyer_company": "Buyer Pvt Ltd",
        "email": buyer.email,
        "phone": phone,
    }
    assert staff.post(f"/app/clients/{buyer.id}/edit", data=edit).status_code == 302
    assert stored(buyer).phone == phone

    shopper = sign_in(app, buyer)
    page = shopper.get("/portal/account").get_data(as_text=True)
    on_screen = prefilled_phone(page)
    assert on_screen == phone
    response = shopper.post("/portal/account", data={"first_name": "Ananya", "last_name": "Rao", "phone": on_screen})
    assert response.status_code == 302, response.get_data(as_text=True)
    saved = stored(buyer)
    assert (saved.last_name, saved.phone) == ("Rao", phone)


@pytest.mark.parametrize("phone", STAFF_ONLY_BEFORE)
def test_the_client_can_type_any_number_staff_could(shopper_client, buyer, phone):
    response = shopper_client.post("/portal/account", data={"first_name": "Ananya", "phone": phone})
    assert response.status_code == 302
    assert stored(buyer).phone == phone


@pytest.fixture
def shopper_client(client, login, buyer):
    login(buyer)
    return client


def accepts(form_class, app, phone, **fields):
    data = {"first_name": "Ananya", "buyer_company": "Buyer Pvt Ltd", "email": "a@b.example", "phone": phone}
    with app.test_request_context(method="POST", data={**data, **fields}):
        form = form_class()
        return form.validate(), form.phone.data


@pytest.mark.parametrize("phone", CORPUS)
def test_client_and_staff_forms_accept_and_store_the_same_numbers(app, phone):
    staff_ok, staff_value = accepts(ClientForm, app, phone)
    client_ok, client_value = accepts(AccountForm, app, phone)
    assert client_ok == staff_ok, phone
    assert client_value == staff_value  # trimmed and spaced the same way


def test_an_untouched_number_on_file_is_kept_even_if_no_rule_allows_it(shopper_client, buyer):
    # For example a number saved before a rule changed
    buyer.phone = "ext. 12"
    db.session.commit()
    page = shopper_client.get("/portal/account").get_data(as_text=True)
    assert prefilled_phone(page) == "ext. 12"
    response = shopper_client.post(
        "/portal/account", data={"first_name": "Ananya", "last_name": "Rao", "phone": "ext. 12"}
    )
    assert response.status_code == 302
    saved = stored(buyer)
    assert (saved.last_name, saved.phone) == ("Rao", "ext. 12")


def test_changing_the_number_is_still_checked(shopper_client, buyer):
    buyer.phone = "ext. 12"
    db.session.commit()
    response = shopper_client.post("/portal/account", data={"first_name": "Ananya", "phone": "ext. 13"})
    assert response.status_code == 200
    assert "Enter a phone number" in response.get_data(as_text=True)
    assert stored(buyer).phone == "ext. 12"


def test_an_untouched_number_with_odd_spacing_is_kept_too(shopper_client, buyer):
    buyer.phone = "ext.  12"
    db.session.commit()
    response = shopper_client.post("/portal/account", data={"first_name": "Ananya", "phone": "ext.  12"})
    assert response.status_code == 302
    assert stored(buyer).phone == "ext. 12"
