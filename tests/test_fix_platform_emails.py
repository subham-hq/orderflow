"""The plain-text part of every email keeps its line breaks and blank lines."""

from pathlib import Path

import pytest

from orderflow.mail import send_email
from orderflow.models import Lead
from orderflow.services.accounts import invite_user
from orderflow.services.orders import place_order, transition_order
from tests import factories

EMAILS = Path(__file__).resolve().parent.parent / "orderflow" / "templates" / "emails"


@pytest.mark.parametrize("template", sorted(EMAILS.glob("*.txt")), ids=lambda path: path.name)
def test_no_text_line_ends_in_a_block_tag(template):
    # trim_blocks drops the newline after a block tag, so "sentence.{% endif %}"
    # at the end of a line runs into the next line. Block tags go on their own lines.
    for number, line in enumerate(template.read_text().splitlines(), 1):
        if line.rstrip().endswith("%}"):
            stripped = line.strip()
            assert stripped.startswith("{%") and stripped.count("{%") == 1, f"{template.name}:{number}: {line}"


@pytest.fixture
def order(company, owner, buyer, mail):
    carton = factories.make_product(company, sku="CTN-001", name="Carton", price_paise=4_250)
    rice = factories.make_product(company, sku="RCE", name="Rice", price_paise=1_45_000, gst_rate_bp=500)
    order, _ = place_order(buyer, {carton.id: 2, rice.id: 1}, notes="Gate 2\nCall Ravi")
    return order


def test_order_received_emails(order, mail):
    to_client, to_staff = mail[-2], mail[-1]
    assert to_client.text == (
        "Hi Ananya,\n\n"
        "Kaveri Supplies has received your order ORD-000001 and will review it shortly.\n\n"
        "- 2 x Carton (CTN-001): ₹85.00\n"
        "- 1 x Rice (RCE): ₹1,450.00\n\n"
        "Subtotal: ₹1,535.00\n"
        "GST: ₹87.80\n"
        "Total: ₹1,622.80\n\n"
        "View the order: http://localhost/portal/orders/ORD-000001"
    )
    assert to_staff.text == (
        "New order ORD-000001 from Ananya Test (Buyer Pvt Ltd).\n\n"
        "- 2 x Carton (CTN-001): ₹85.00\n"
        "- 1 x Rice (RCE): ₹1,450.00\n\n"
        "Total: ₹1,622.80\n\n"
        "Notes:\nGate 2\nCall Ravi\n\n"
        "Review it: http://localhost/app/orders/ORD-000001"
    )


def test_status_emails_keep_the_note_on_its_own_lines(order, owner, mail):
    transition_order(order, owner, "approved", "Packed today")
    assert mail[-1].text == (
        "Hi Ananya,\n\n"
        "Kaveri Supplies approved your order ORD-000001 for ₹1,622.80.\n\n"
        "Note from Kaveri Supplies:\nPacked today\n\n"
        "View the order: http://localhost/portal/orders/ORD-000001"
    )
    transition_order(order, owner, "fulfilled")
    assert mail[-1].text == (
        "Hi Ananya,\n\n"
        "Kaveri Supplies has fulfilled your order ORD-000001.\n\n"
        "View the order: http://localhost/portal/orders/ORD-000001"
    )


def test_rejection_email(order, owner, mail):
    transition_order(order, owner, "rejected", "Out of stock until Monday")
    assert mail[-1].text == (
        "Hi Ananya,\n\n"
        "Kaveri Supplies couldn't accept your order ORD-000001.\n\n"
        "Note from Kaveri Supplies:\nOut of stock until Monday\n\n"
        "View the order: http://localhost/portal/orders/ORD-000001"
    )


def test_cancelled_by_client_email(order, buyer, mail):
    transition_order(order, buyer, "cancelled")
    assert mail[-1].text == (
        "Ananya Test (Buyer Pvt Ltd) cancelled order ORD-000001 for ₹1,622.80 before it was reviewed.\n\n"
        "View the order: http://localhost/app/orders/ORD-000001"
    )


def test_invite_emails(app, company, owner, mail):
    invite_user(company, owner, "ravi@stores.example", "client", "Ravi", buyer_company="Ravi Stores")
    text = mail[-1].text
    assert text.startswith(
        "Hi Ravi,\n\n"
        "Priya Test at Kaveri Supplies has set up an OrderFlow account for Ravi Stores. You can browse their "
        "catalog, place purchase orders and track them in one place.\n\n"
        "Accept the invitation and choose a password here:\n\n"
        "http://localhost/invite/"
    )
    assert text.endswith("\n\nThis link works for 7 days.")

    invite_user(company, owner, "deepa@kaveri.example", "admin", "Deepa")
    assert mail[-1].text.startswith(
        "Hi Deepa,\n\n"
        "Priya Test invited you to help manage orders for Kaveri Supplies on OrderFlow.\n\n"
        "Accept the invitation and choose a password here:\n\n"
    )


def test_lead_email(app, mail):
    with app.test_request_context():
        lead = Lead(name="Kiran", email="kiran@shop.example", company="Kiran Stores", message="Hello\nWe sell boxes.")
        send_email("sales@orderflow.test", "New enquiry", "lead", lead=lead)
        assert mail[-1].text == (
            "New enquiry from the OrderFlow website\n\n"
            "Name: Kiran\nEmail: kiran@shop.example\nCompany: Kiran Stores\n\n"
            "Hello\nWe sell boxes.\n"
        )
        send_email("sales@orderflow.test", "New enquiry", "lead", lead=Lead(name="Kiran", email="kiran@shop.example"))
        assert mail[-1].text == "New enquiry from the OrderFlow website\n\nName: Kiran\nEmail: kiran@shop.example\n"
