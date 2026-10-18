from datetime import UTC, datetime

import pytest

from orderflow.extensions import db
from orderflow.models import Bulletin, Company, User
from orderflow.services.orders import place_order
from tests import factories

SETTINGS = {
    "name": "Kaveri Supplies",
    "legal_name": "Kaveri Supplies Private Limited",
    "gstin": "29ABCDE1234F1Z5",
    "address": "14, 2nd Cross\nPeenya Industrial Area",
    "phone": "+91 80 4000 1234",
    "support_email": "orders@kaveri.example",
    "timezone": "Asia/Kolkata",
    "order_prefix": "ORD",
}


@pytest.fixture
def signed_in(login, owner):
    login(owner)
    return owner


def text(response):
    return response.get_data(as_text=True)


def get_company(company):
    return db.session.get(Company, company.id)


ENDPOINTS = [("get", "/app/settings"), ("post", "/app/settings")]


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_anonymous_users_are_sent_to_sign_in(client, company, method, path):
    response = getattr(client, method)(path, data={**SETTINGS, "name": "Hijacked", "first_name": "Hijacked"})
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    assert get_company(company).name == "Kaveri Supplies"


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_clients_are_sent_back_to_their_portal(client, login, company, buyer, method, path):
    login(buyer)
    response = getattr(client, method)(path, data={**SETTINGS, "name": "Hijacked", "first_name": "Hijacked"})
    assert response.status_code == 302 and response.headers["Location"] == "/portal/"
    assert get_company(company).name == "Kaveri Supplies"
    assert db.session.get(User, buyer.id).first_name == "Ananya"


# Company settings: owner ------------------------------------------------------


def test_settings_form_is_prefilled(client, signed_in, company):
    company.gstin = "29ABCDE1234F1Z5"
    company.next_order_seq = 42
    db.session.commit()
    body = text(client.get("/app/settings"))
    assert 'value="Kaveri Supplies"' in body and 'value="29ABCDE1234F1Z5"' in body
    assert '<option selected value="Asia/Kolkata">Asia/Kolkata (UTC+05:30)</option>' in body
    assert "America/New_York (UTC-0" in body and "the next one will be ORD-000042" in body
    assert 'aria-current="page">Company</a>' in body and 'href="/app/settings/profile"' in body
    assert "None" not in body


def test_owner_saves_settings_with_values_cleaned_up(client, signed_in, company):
    data = {
        **SETTINGS,
        "name": "  Kaveri   Supplies Co ",
        "gstin": " 29abcde 1234f1z5 ",
        "support_email": " Orders@Kaveri.Example ",
        "order_prefix": " ks 24 ",
        "address": "  Line one\r\nLine two  ",
        "timezone": "Asia/Dubai",
    }
    response = client.post("/app/settings", data=data, follow_redirects=True)
    body = text(response)
    assert "Saved your company settings." in body
    assert "New orders will be numbered like KS24-000001" in body
    assert "Dates and times are now shown in Asia/Dubai time." in body
    company = get_company(company)
    assert company.name == "Kaveri Supplies Co" and company.gstin == "29ABCDE1234F1Z5"
    assert company.support_email == "orders@kaveri.example" and company.order_prefix == "KS24"
    assert company.address == "Line one\nLine two" and company.timezone == "Asia/Dubai"


def test_optional_fields_can_be_cleared(client, signed_in, company):
    client.post("/app/settings", data=SETTINGS)
    blank = {**SETTINGS, "legal_name": "", "gstin": " ", "address": "", "phone": "", "support_email": ""}
    client.post("/app/settings", data=blank)
    company = get_company(company)
    assert (company.legal_name, company.gstin, company.address, company.phone, company.support_email) == (
        None,
        None,
        None,
        None,
        None,
    )


def test_saving_without_changes_says_so(client, signed_in, company):
    client.post("/app/settings", data=SETTINGS, follow_redirects=True)
    response = client.post("/app/settings", data={**SETTINGS, "gstin": "29abcde1234f1z5"}, follow_redirects=True)
    assert "No changes to save." in text(response) and "Saved your company settings." not in text(response)


@pytest.mark.parametrize(
    "gstin", ["29ABCDE1234F1Z", "29ABCDE1234F1Z55", "29ABCDE1234F0Z5", "29ABCDE1234F1X5", "AB12345", "२९ABCDE1234F1Z5"]
)
def test_invalid_gstins_are_rejected(client, signed_in, company, gstin):
    response = client.post("/app/settings", data={**SETTINGS, "gstin": gstin, "name": "Typed name"})
    body = text(response)
    assert response.status_code == 200 and "Enter a 15-character GSTIN, like 29ABCDE1234F1Z5." in body
    assert 'value="Typed name"' in body
    assert get_company(company).gstin is None


@pytest.mark.parametrize("prefix", ["A", "TOOLONGPREFIX", "OR-D", "ORD/", "ओआरडी", ""])
def test_invalid_order_prefixes_are_rejected(client, signed_in, company, prefix):
    body = text(client.post("/app/settings", data={**SETTINGS, "order_prefix": prefix}))
    assert "Use 2 to 8 letters or numbers" in body or "Enter a prefix for order numbers" in body
    assert get_company(company).order_prefix == "ORD"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"name": " "}, "Enter your company name."),
        ({"name": "n" * 121}, "Keep the name to 120 characters or fewer."),
        ({"legal_name": "l" * 161}, "Keep the legal name to 160 characters or fewer."),
        ({"address": "a" * 501}, "Keep the address to 500 characters or fewer."),
        ({"phone": "phone me"}, "Enter a phone number"),
        ({"support_email": "orders@"}, "Enter a valid email address"),
        ({"timezone": "Mars/Phobos"}, "Choose a time zone from the list."),
        ({"timezone": "Africa/Nairobi"}, "Choose a time zone from the list."),
        ({"timezone": ""}, "Choose a time zone."),
    ],
)
def test_validation_errors_keep_input(client, signed_in, company, changes, message):
    data = {**SETTINGS, "legal_name": "Typed legal name", **changes}
    response = client.post("/app/settings", data=data)
    body = text(response)
    assert response.status_code == 200 and message in body and 'aria-invalid="true"' in body
    if "legal_name" not in changes:
        assert 'value="Typed legal name"' in body
    assert get_company(company).legal_name is None


def test_an_unlisted_current_time_zone_is_kept(client, signed_in, company):
    company.timezone = "Africa/Nairobi"
    db.session.commit()
    body = text(client.get("/app/settings"))
    assert '<option selected value="Africa/Nairobi">Africa/Nairobi (UTC+03:00)</option>' in body
    response = client.post("/app/settings", data={**SETTINGS, "timezone": "Africa/Nairobi"})
    assert response.status_code == 302
    assert get_company(company).timezone == "Africa/Nairobi"


def test_new_prefix_applies_to_new_orders_only(client, signed_in, company, buyer):
    product = factories.make_product(company)
    first, _ = place_order(buyer, {product.id: 1})
    client.post("/app/settings", data={**SETTINGS, "order_prefix": "KS"})
    second, _ = place_order(buyer, {product.id: 1})
    assert first.number == "ORD-000001" and second.number == "KS-000002"


def test_time_zone_change_changes_displayed_times(client, signed_in, company):
    db.session.add(
        Bulletin(
            company_id=company.id, title="Closing", body="Early", expires_at=datetime(2030, 1, 1, 9, 0, tzinfo=UTC)
        )
    )
    db.session.commit()
    assert "Ends 1 Jan 2030, 2:30 PM" in text(client.get("/app/bulletins"))
    client.post("/app/settings", data={**SETTINGS, "timezone": "America/Los_Angeles"})
    body = text(client.get("/app/bulletins"))
    assert "Ends 1 Jan 2030, 1:00 AM" in body and "Times are in America/Los_Angeles." in body


def test_support_email_shows_in_the_client_portal_footer(client, login, signed_in, company, buyer):
    client.post("/app/settings", data={**SETTINGS, "support_email": "help@kaveri.example"})
    client.post("/logout")
    login(buyer)
    assert "mailto:help@kaveri.example" in text(client.get("/portal/"))


def test_settings_only_change_your_own_company(client, signed_in, company, other_company):
    client.post("/app/settings", data={**SETTINGS, "name": "Renamed"})
    assert get_company(company).name == "Renamed"
    assert get_company(other_company["company"]).name == "Rival Traders"


# Company settings: admins ------------------------------------------------------


def test_admins_see_settings_read_only(client, login, company, owner, admin):
    company.gstin = "29ABCDE1234F1Z5"
    company.address = "Line one\nLine two"
    db.session.commit()
    login(admin)
    body = text(client.get("/app/settings"))
    assert "Only the account owner (Priya Test) can change company settings." in body
    assert 'name="name"' not in body and "Save settings" not in body
    assert "29ABCDE1234F1Z5" in body and "Line one<br>Line two" in body
    assert "Not set" in body and "ORD-000001" in body and "None" not in body


def test_admins_cannot_change_settings(client, login, company, admin):
    login(admin)
    response = client.post("/app/settings", data={**SETTINGS, "name": "Hijacked"}, follow_redirects=True)
    assert "Only the account owner can change company settings." in text(response)
    assert get_company(company).name == "Kaveri Supplies"
