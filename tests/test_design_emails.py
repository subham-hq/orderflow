"""The HTML emails: one branded, light, table-based layout for every message."""

import re

import pytest

from orderflow.mail import send_email
from orderflow.models import Lead
from orderflow.services.accounts import invite_user, send_verification_email
from orderflow.services.orders import place_order, transition_order
from tests import factories
from tests.design_helpers import Page


def check_layout(message):
    """Things every HTML email must get right."""
    html = message.html
    assert "<style" not in html and "<script" not in html and 'rel="stylesheet"' not in html
    assert 'role="presentation"' in html
    assert 'src="http://localhost/static/img/icon-192.png"' in html
    assert ">OrderFlow</td>" in html
    assert 'name="color-scheme" content="light"' in html
    assert f"<title>{message.subject}</title>" in html.replace("&#39;", "'")
    text = Page(html).text
    assert "None" not in text
    assert "{{" not in html and "{%" not in html
    return Page(html)


def preheader(message):
    return re.search(r'<div style="display:none;[^"]*">(.*?)</div>', message.html, re.S).group(1)


@pytest.fixture
def product(company):
    return factories.make_product(
        company, sku="CTN-001", name="Corrugated carton, 5-ply", unit="box", price_paise=4_250, gst_rate_bp=1800
    )


def test_verification_email(app, owner, mail):
    send_verification_email(owner)
    html = check_layout(mail[-1])
    assert "Confirm your email" in html.text
    assert "One click to finish setting up Kaveri Supplies" in preheader(mail[-1])
    button = [a for a in html.find_all("a") if "/verify/" in a.get("href", "")]
    assert len(button) == 2  # the button and the plain link under it


def test_invite_emails_for_clients_and_staff(app, owner, mail):
    invite_user(owner.company, owner, "ravi@stores.example", "client", "Ravi", buyer_company="Ravi Stores")
    html = check_layout(mail[-1])
    assert "Kaveri Supplies invited you to order online" in html.text
    assert "Ravi Stores" in html.text
    assert "Browse the Kaveri Supplies catalog" in preheader(mail[-1])

    invite_user(owner.company, owner, "deepa@kaveri.example", "admin", "Deepa")
    html = check_layout(mail[-1])
    assert "Join Kaveri Supplies on OrderFlow" in html.text
    assert "Help manage orders" in preheader(mail[-1])


def test_reset_password_email(client, owner, mail):
    client.post("/forgot-password", data={"email": owner.email})
    html = check_layout(mail[-1])
    assert "Reset your password" in html.text and "works for 1 hour" in html.text


def test_order_emails_show_items_gst_and_totals(app, owner, buyer, product, mail):
    order, _ = place_order(buyer, {product.id: 40}, notes="Deliver to the back gate.\nCall Ravi.")
    to_client, to_staff = mail[-2], mail[-1]

    html = check_layout(to_client)
    assert f"Order {order.number} received" in html.text
    assert "Corrugated carton, 5-ply" in html.text
    assert "40 box × ₹42.50 + 18% GST" in html.text
    assert "₹1,700.00" in html.text and "₹306.00" in html.text and "₹2,006.00" in html.text

    html = check_layout(to_staff)
    assert f"New order {order.number}" in html.text
    assert "Deliver to the back gate." in html.text  # the buyer's note
    assert "ordered 1 item for ₹2,006.00" in preheader(to_staff)
    assert "on the Kaveri Supplies team" in html.text


@pytest.mark.parametrize(
    ("to_status", "label", "colours"),
    [
        ("approved", "Approved", ("#e4e8f9", "#24378a")),
        ("rejected", "Rejected", ("#fbe6e6", "#a1232b")),
    ],
)
def test_status_emails_carry_a_coloured_status(app, owner, buyer, product, mail, to_status, label, colours):
    order, _ = place_order(buyer, {product.id: 2})
    reason = "Out of stock on the 5-ply cartons this week." if to_status == "rejected" else None
    transition_order(order, owner, to_status, reason)
    message = mail[-1]
    html = check_layout(message)
    assert f"background-color:{colours[0]};color:{colours[1]}" in message.html
    assert f">{label}</span>" in message.html
    assert f"is now {label.lower()}" in preheader(message)
    if reason:
        assert "Note from Kaveri Supplies" in html.text and reason in html.text
    else:
        assert "Note from" not in html.text


def test_fulfilled_and_cancelled_status_emails(app, owner, buyer, product, mail):
    order, _ = place_order(buyer, {product.id: 2})
    transition_order(order, owner, "approved")
    transition_order(order, owner, "fulfilled", "Sent with Blue Dart, docket 12345.")
    html = check_layout(mail[-1])
    assert "has fulfilled your order" in html.text and "docket 12345" in html.text

    order, _ = place_order(buyer, {product.id: 3})
    transition_order(order, owner, "approved")
    transition_order(order, owner, "cancelled", "The buyer asked us to cancel.")
    html = check_layout(mail[-1])
    assert "was cancelled by Kaveri Supplies" in html.text


def test_client_cancellation_email_to_staff(app, owner, buyer, product, mail):
    order, _ = place_order(buyer, {product.id: 2})
    transition_order(order, buyer, "cancelled")
    html = check_layout(mail[-1])
    assert f"Order {order.number} was cancelled" in html.text
    assert "There's nothing you need to do." in html.text


def test_lead_email(app, mail):
    lead = Lead(name="Meera Iyer", email="meera@iyertraders.example", company=None, message="Packaging\nfor 40 stores")
    send_email("sales@orderflow.test", "New enquiry from Meera Iyer", "lead", lead=lead, reply_to=lead.email)
    html = check_layout(mail[-1])
    assert "mailto:meera@iyertraders.example" in html.hrefs()
    assert "Company" not in html.text  # no empty row for a missing company
    assert "Packaging" in html.text
    assert "Meera Iyer sent a message" in preheader(mail[-1])
