from datetime import timedelta

import pytest

from orderflow.extensions import db
from orderflow.models import APPROVED, CANCELLED, FULFILLED, REJECTED, Order, User
from orderflow.services import clients as client_service
from orderflow.services.orders import place_order
from orderflow.timeutil import utcnow
from tests import factories
from tests.conftest import link_from

INVITE = {
    "first_name": "Meera",
    "last_name": "Iyer",
    "buyer_company": "Iyer Traders",
    "email": "meera@iyertraders.example",
    "phone": "+91 98450 12345",
}


@pytest.fixture
def signed_in(login, owner):
    login(owner)
    return owner


@pytest.fixture
def invited(company, owner):
    """A client who hasn't accepted their invitation yet."""
    return factories.make_client(
        company,
        email="invited@sunrise.example",
        first_name="Ravi",
        buyer_company="Sunrise Retail",
        password=None,
        verified=False,
        invited_by=owner,
        invite_sent_at=utcnow() - timedelta(days=2),
    )


def text(response):
    return response.get_data(as_text=True)


def get_user(user_id):
    return db.session.get(User, user_id)


def clients_named(first_name):
    return db.session.scalars(db.select(User).where(User.first_name == first_name)).all()


def sign_in(app, user):
    """A second browser, signed in as user."""
    browser = app.test_client()
    response = browser.post("/login", data={"email": user.email, "password": factories.PASSWORD})
    assert response.status_code == 302
    return browser


def make_order(buyer, company, status=None, price_paise=10_000, quantity=1):
    product = factories.make_product(company, price_paise=price_paise, gst_rate_bp=0)
    order, _ = place_order(buyer, {product.id: quantity})
    if status:
        order.status = status
        db.session.commit()
    return order


# Access ----------------------------------------------------------------------

ID_ENDPOINTS = [
    ("get", "/app/clients/{id}"),
    ("get", "/app/clients/{id}/edit"),
    ("post", "/app/clients/{id}/edit"),
    ("post", "/app/clients/{id}/status"),
    ("post", "/app/clients/{id}/resend-invite"),
    ("post", "/app/clients/{id}/delete"),
]
ENDPOINTS = ID_ENDPOINTS
CHANGES = {**INVITE, "active": "0"}


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_anonymous_users_are_sent_to_sign_in(client, buyer, mail, method, path):
    response = getattr(client, method)(path.format(id=buyer.id), data=CHANGES)
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    buyer = get_user(buyer.id)
    assert buyer.active and buyer.first_name == "Ananya" and mail == []


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_clients_are_sent_back_to_their_portal(client, login, company, buyer, mail, method, path):
    other = factories.make_client(company, email="other@buyer.example", first_name="Other")
    login(buyer)
    response = getattr(client, method)(path.format(id=other.id), data=CHANGES)
    assert response.status_code == 302 and response.headers["Location"] == "/portal/"
    other = get_user(other.id)
    assert other.active and other.first_name == "Other" and mail == []
    assert clients_named("Meera") == []


@pytest.mark.parametrize(("method", "path"), ID_ENDPOINTS)
def test_clients_of_other_companies_are_not_found(client, signed_in, other_company, mail, method, path):
    rival = other_company["client"]
    response = getattr(client, method)(path.format(id=rival.id), data=CHANGES)
    assert response.status_code == 404
    rival = get_user(rival.id)
    assert rival is not None and rival.active and rival.first_name != "Meera" and mail == []


@pytest.mark.parametrize(("method", "path"), ID_ENDPOINTS)
def test_staff_accounts_are_not_managed_here(client, signed_in, admin, mail, method, path):
    for staff in (admin, signed_in):
        response = getattr(client, method)(path.format(id=staff.id), data=CHANGES)
        assert response.status_code == 404
    admin = get_user(admin.id)
    assert admin is not None and admin.active and admin.first_name == "Arjun"


@pytest.mark.parametrize("path", ["/app/clients/999999", "/app/clients/abc/edit"])
def test_unknown_ids_are_not_found(client, signed_in, path):
    assert client.get(path).status_code == 404


def test_state_changes_need_post(client, signed_in, buyer):
    for path in ("status", "resend-invite", "delete"):
        assert client.get(f"/app/clients/{buyer.id}/{path}").status_code == 405
    assert get_user(buyer.id).active


def test_admins_can_manage_client_accounts(client, login, admin, buyer):
    login(admin)
    assert client.get(f"/app/clients/{buyer.id}").status_code == 200
    assert client.post(f"/app/clients/{buyer.id}/status", data={"active": "0"}).status_code == 302
    assert get_user(buyer.id).active is False


# Detail ----------------------------------------------------------------------


def test_detail_shows_recent_orders_and_what_they_bought(client, signed_in, company, buyer):
    make_order(buyer, company, price_paise=100_00)
    make_order(buyer, company, APPROVED, price_paise=200_00)
    make_order(buyer, company, FULFILLED, price_paise=400_00)
    make_order(buyer, company, REJECTED, price_paise=800_00)
    make_order(buyer, company, CANCELLED, price_paise=1600_00)
    body = text(client.get(f"/app/clients/{buyer.id}"))
    assert "Buyer Pvt Ltd" in body and "buyer@sunrise.example" in body
    assert "₹600.00" in body  # approved + fulfilled only
    for order in db.session.scalars(db.select(Order)):
        assert f"/app/orders/{order.number}" in body
    assert "badge--fulfilled" in body and "Pending review" in body
    assert "Delete client" not in body and "Clients with orders can't be deleted" in body
    assert "Suspend account" in body and "Resend invitation" not in body
    assert "None" not in body


def test_detail_lists_only_the_latest_ten_orders(client, signed_in, company, buyer):
    orders = [make_order(buyer, company) for _ in range(12)]
    body = text(client.get(f"/app/clients/{buyer.id}"))
    assert "Showing the latest 10 of 12 orders." in body
    assert orders[-1].number in body and orders[0].number not in body


def test_detail_of_a_new_client(client, signed_in, invited):
    body = text(client.get(f"/app/clients/{invited.id}"))
    assert "No orders yet" in body and "Delete client" in body and "Resend invitation" in body
    assert "We last sent it 2 days ago" in body
    assert "Not added" in body and "None" not in body


def test_detail_of_a_suspended_client(client, signed_in, buyer):
    buyer.active = False
    db.session.commit()
    body = text(client.get(f"/app/clients/{buyer.id}"))
    assert "This account is suspended." in body and "Reactivate account" in body
    assert 'name="active" value="1"' in body and "Suspend account" not in body


# Edit ------------------------------------------------------------------------


def test_edit_form_is_prefilled_and_explains_email_changes_after_joining(client, signed_in, buyer):
    body = text(client.get(f"/app/clients/{buyer.id}/edit"))
    assert 'value="Ananya"' in body and 'value="Buyer Pvt Ltd"' in body
    assert 'name="email"' in body and 'value="buyer@sunrise.example"' in body
    assert "They sign in with buyer@sunrise.example. If you change it, we&#39;ll email a link" in body
    assert "None" not in body


def test_edit_saves_details(client, signed_in, buyer):
    data = {"first_name": "Ananya", "last_name": "Rao", "buyer_company": "Sunrise Retail LLP", "phone": "080 4000 1234"}
    data["email"] = buyer.email
    response = client.post(f"/app/clients/{buyer.id}/edit", data=data, follow_redirects=True)
    assert "Saved changes to Ananya Rao (Sunrise Retail LLP)." in text(response)
    buyer = get_user(buyer.id)
    assert (buyer.last_name, buyer.buyer_company, buyer.phone) == ("Rao", "Sunrise Retail LLP", "080 4000 1234")


def test_an_accepted_clients_email_waits_for_them_to_confirm(client, signed_in, buyer, mail):
    data = {"first_name": "Ananya", "buyer_company": "Buyer Pvt Ltd", "email": "hijack@evil.example"}
    client.post(f"/app/clients/{buyer.id}/edit", data=data)
    assert get_user(buyer.id).email == "buyer@sunrise.example"  # until the client confirms it, signed in
    assert [message.to for message in mail] == [["hijack@evil.example"]]


def test_saving_without_changes_says_so(client, signed_in, buyer):
    data = {
        "first_name": " Ananya ",
        "last_name": "Test",
        "buyer_company": "Buyer Pvt Ltd",
        "phone": "",
        "email": buyer.email,
    }
    response = client.post(f"/app/clients/{buyer.id}/edit", data=data, follow_redirects=True)
    body = text(response)
    assert "No changes to save for Ananya Test (Buyer Pvt Ltd)." in body and "Saved changes" not in body


def test_edit_validation_keeps_input(client, signed_in, buyer):
    data = {"first_name": "", "last_name": "Typed", "buyer_company": "Typed Co", "phone": "abc"}
    body = text(client.post(f"/app/clients/{buyer.id}/edit", data=data))
    assert "Enter their first name." in body and "Enter a phone number" in body
    assert 'value="Typed Co"' in body and 'value="abc"' in body
    assert get_user(buyer.id).buyer_company == "Buyer Pvt Ltd"


def test_invited_clients_email_can_be_corrected_and_is_reinvited(app, client, signed_in, invited, mail):
    body = text(client.get(f"/app/clients/{invited.id}/edit"))
    assert 'name="email"' in body and 'value="invited@sunrise.example"' in body
    old_link = None
    client.post(f"/app/clients/{invited.id}/resend-invite")  # cooldown is over: invite was sent 2 days ago
    old_link = link_from(mail[0], "/invite/")
    mail.clear()
    data = {"first_name": "Ravi", "buyer_company": "Sunrise Retail", "email": "Ravi@Sunrise.example"}
    response = client.post(f"/app/clients/{invited.id}/edit", data=data, follow_redirects=True)
    assert "We sent a new invitation to ravi@sunrise.example" in text(response)
    assert get_user(invited.id).email == "ravi@sunrise.example"
    assert len(mail) == 1 and mail[0].to == ["ravi@sunrise.example"]
    browser = app.test_client()
    assert browser.get(old_link).status_code == 400  # the link sent to the wrong address stops working
    assert browser.get(link_from(mail[0], "/invite/")).status_code == 200


def test_invited_email_change_to_a_taken_address_is_refused(client, signed_in, invited, buyer, mail):
    data = {"first_name": "Ravi", "buyer_company": "Sunrise Retail", "email": buyer.email}
    body = text(client.post(f"/app/clients/{invited.id}/edit", data=data))
    assert "already one of your clients" in body
    assert get_user(invited.id).email == "invited@sunrise.example" and mail == []


def test_email_change_for_a_suspended_invitee_does_not_send(client, signed_in, invited, mail):
    invited.active = False
    db.session.commit()
    data = {"first_name": "Ravi", "buyer_company": "Sunrise Retail", "email": "ravi@sunrise.example"}
    response = client.post(f"/app/clients/{invited.id}/edit", data=data, follow_redirects=True)
    assert "They&#39;re suspended, so we haven&#39;t sent an invitation" in text(response)
    assert get_user(invited.id).email == "ravi@sunrise.example" and mail == []


def test_email_change_when_mail_fails(client, signed_in, invited, monkeypatch):
    from orderflow.admin import clients as views

    monkeypatch.setattr(views, "send_invite_email", lambda user: False)
    data = {"first_name": "Ravi", "buyer_company": "Sunrise Retail", "email": "ravi@sunrise.example"}
    response = client.post(f"/app/clients/{invited.id}/edit", data=data, follow_redirects=True)
    assert "couldn&#39;t email the new invitation to ravi@sunrise.example" in text(response)
    assert get_user(invited.id).email == "ravi@sunrise.example"


def test_email_change_race_is_reported(client, signed_in, invited, monkeypatch):
    from orderflow.admin import clients as views

    monkeypatch.setattr(views, "email_taken", lambda email: False)
    factories.make_client(invited.company, email="taken@sunrise.example")
    data = {"first_name": "Ravi", "buyer_company": "Sunrise Retail", "email": "taken@sunrise.example"}
    response = client.post(f"/app/clients/{invited.id}/edit", data=data)
    assert response.status_code == 200
    assert "Someone with this email already has an OrderFlow account." in text(response)
    assert get_user(invited.id).email == "invited@sunrise.example"


# Suspend and reactivate ------------------------------------------------------


def test_suspending_ends_the_clients_sessions_at_once(app, client, signed_in, company, buyer):
    second = factories.make_client(company, email="second@buyer.example")
    buyer_browser = sign_in(app, buyer)
    second_browser = sign_in(app, second)
    assert buyer_browser.get("/portal/").status_code == 200

    response = client.post(f"/app/clients/{buyer.id}/status", data={"active": "0"}, follow_redirects=True)
    assert "Suspended Ananya Test (Buyer Pvt Ltd). They&#39;ve been signed out" in text(response)
    assert get_user(buyer.id).active is False

    assert buyer_browser.get("/portal/").headers["Location"].startswith("/login")
    assert second_browser.get("/portal/").status_code == 200  # other clients are unaffected
    response = buyer_browser.post("/login", data={"email": buyer.email, "password": factories.PASSWORD})
    assert response.status_code == 200 and "suspended" in text(response)


def test_status_is_explicit_and_idempotent(client, signed_in, buyer):
    url = f"/app/clients/{buyer.id}/status"
    client.post(url, data={"active": "0"})
    version = get_user(buyer.id).auth_version
    response = client.post(url, data={"active": "0"}, follow_redirects=True)
    assert "Ananya Test (Buyer Pvt Ltd) is already suspended." in text(response)
    assert get_user(buyer.id).auth_version == version and get_user(buyer.id).active is False

    response = client.post(url, data={"active": "1"}, follow_redirects=True)
    assert "Reactivated Ananya Test (Buyer Pvt Ltd). They can sign in and place orders again." in text(response)
    assert get_user(buyer.id).active is True
    response = client.post(url, data={"active": "1"}, follow_redirects=True)
    assert "is already active." in text(response)


def test_reactivated_client_can_sign_in_again(app, client, signed_in, buyer):
    client.post(f"/app/clients/{buyer.id}/status", data={"active": "0"})
    client.post(f"/app/clients/{buyer.id}/status", data={"active": "1"})
    browser = sign_in(app, buyer)
    assert browser.get("/portal/").status_code == 200


def test_reactivating_an_invitee_reminds_staff_to_resend(client, signed_in, invited):
    client.post(f"/app/clients/{invited.id}/status", data={"active": "0"})
    response = client.post(f"/app/clients/{invited.id}/status", data={"active": "1"}, follow_redirects=True)
    assert "Resend their invitation so they can set a password." in text(response)


@pytest.mark.parametrize("data", [{}, {"active": ""}, {"active": "true"}, {"active": "2"}])
def test_status_needs_an_explicit_target(client, signed_in, buyer, data):
    assert client.post(f"/app/clients/{buyer.id}/status", data=data).status_code == 400
    assert get_user(buyer.id).active


def test_status_returns_to_a_safe_next_page(client, signed_in, buyer):
    url = f"/app/clients/{buyer.id}/status"
    response = client.post(url, data={"active": "0", "next": "/app/clients?status=active"})
    assert response.headers["Location"] == "/app/clients?status=active"
    response = client.post(url, data={"active": "1", "next": "https://evil.example/"})
    assert response.headers["Location"] == f"/app/clients/{buyer.id}"


# Resend invitation -----------------------------------------------------------


def test_resend_sends_a_fresh_invitation(client, signed_in, invited, mail):
    before = invited.invite_sent_at
    response = client.post(f"/app/clients/{invited.id}/resend-invite", follow_redirects=True)
    assert "Sent a new invitation to invited@sunrise.example." in text(response)
    assert len(mail) == 1 and mail[0].to == ["invited@sunrise.example"]
    assert get_user(invited.id).invite_sent_at > before

    # A double click doesn't send a second email
    response = client.post(f"/app/clients/{invited.id}/resend-invite", follow_redirects=True)
    assert "less than a minute ago" in text(response)
    assert len(mail) == 1


def test_resend_is_not_needed_after_joining(client, signed_in, buyer, mail):
    response = client.post(f"/app/clients/{buyer.id}/resend-invite", follow_redirects=True)
    assert "has already accepted their invitation" in text(response) and mail == []


def test_resend_is_refused_while_suspended(client, signed_in, invited, mail):
    invited.active = False
    db.session.commit()
    response = client.post(f"/app/clients/{invited.id}/resend-invite", follow_redirects=True)
    assert "is suspended. Reactivate them before sending a new invitation." in text(response) and mail == []


def test_resend_when_mail_fails(client, signed_in, invited, monkeypatch):
    before = invited.invite_sent_at
    monkeypatch.setattr(client_service, "send_invite_email", lambda user: False)
    response = client.post(f"/app/clients/{invited.id}/resend-invite", follow_redirects=True)
    assert "We couldn&#39;t send the invitation" in text(response)
    assert get_user(invited.id).invite_sent_at == before


# Delete ----------------------------------------------------------------------


def test_client_without_orders_can_be_deleted(client, signed_in, invited):
    invited_id = invited.id
    response = client.post(f"/app/clients/{invited_id}/delete")
    assert response.headers["Location"] == "/app/clients"
    assert get_user(invited_id) is None
    assert "Deleted the account for Ravi Test (Sunrise Retail) (invited@sunrise.example)." in text(
        client.get("/app/clients")
    )


def test_client_with_orders_cannot_be_deleted(client, signed_in, company, buyer):
    make_order(buyer, company, CANCELLED)
    response = client.post(f"/app/clients/{buyer.id}/delete", follow_redirects=True)
    assert "has placed orders, so their account can&#39;t be deleted. Suspend it instead" in text(response)
    assert get_user(buyer.id) is not None


def test_delete_race_with_a_new_order_is_reported(client, signed_in, company, buyer, monkeypatch):
    make_order(buyer, company)
    monkeypatch.setattr(client_service, "order_count", lambda user: 0)
    response = client.post(f"/app/clients/{buyer.id}/delete", follow_redirects=True)
    assert "has just placed an order" in text(response)
    assert get_user(buyer.id) is not None


def test_delete_keeps_records_that_mention_the_client(client, signed_in, company, invited):
    from orderflow.models import Bulletin

    bulletin = Bulletin(company_id=company.id, author_id=invited.id, title="Hello", body="Body")
    teammate = factories.make_client(company, email="later@x.example", invited_by=invited)
    db.session.add(bulletin)
    db.session.commit()
    invited_id = invited.id
    client.post(f"/app/clients/{invited_id}/delete")
    assert get_user(invited_id) is None
    assert db.session.get(Bulletin, bulletin.id).author_id is None
    assert get_user(teammate.id).invited_by_id is None


def test_delete_service_refuses_staff(app, owner):
    with pytest.raises(client_service.ClientError):
        client_service.delete_client(owner)


def test_unexpected_database_errors_on_edit_are_not_hidden(client, signed_in, buyer, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    def failing_commit():
        raise IntegrityError("UPDATE", {}, Exception("unexpected"))

    monkeypatch.setattr(db.session, "commit", failing_commit)
    data = {"first_name": "Renamed", "buyer_company": "Buyer Pvt Ltd", "email": buyer.email}
    # Tests propagate errors; in production this is the logged 500 page
    with pytest.raises(IntegrityError, match="unexpected"):
        client.post(f"/app/clients/{buyer.id}/edit", data=data)
    monkeypatch.undo()
    db.session.rollback()
    assert get_user(buyer.id).first_name == "Ananya"
