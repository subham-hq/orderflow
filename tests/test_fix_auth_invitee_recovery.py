"""Someone who was invited but never set a password can still get in."""

from orderflow.extensions import db
from orderflow.services.accounts import invite_user
from tests.conftest import link_from
from tests.test_auth import SIGNUP


def _invite(client, company, owner, mail, email="inv@example.com"):
    with client.application.test_request_context():
        user, sent = invite_user(company, owner, email, "client", "Inv", buyer_company="Inv Stores")
    assert sent
    mail.clear()
    return user


def test_forgot_password_sends_a_pending_invitee_a_fresh_invitation(client, company, owner, mail):
    invited = _invite(client, company, owner, mail)

    response = client.post("/forgot-password", data={"email": "INV@example.com"})
    assert response.status_code == 200
    assert len(mail) == 1 and mail[0].to == [invited.email]
    assert "invited you" in mail[0].subject

    link = link_from(mail[0], "/invite/")
    accepted = client.post(link, data={"password": "tangerine sky 42", "confirm": "tangerine sky 42"})
    assert accepted.headers["Location"].endswith("/portal/")
    db.session.refresh(invited)
    assert invited.has_accepted_invite and invited.invite_sent_at is not None


def test_the_page_is_the_same_for_invitees_and_unknown_addresses(client, company, owner, mail):
    invited = _invite(client, company, owner, mail)
    pending = client.post("/forgot-password", data={"email": invited.email}).get_data(as_text=True)
    unknown = client.post("/forgot-password", data={"email": "nobody@nowhere.example"}).get_data(as_text=True)
    assert pending.replace(invited.email, "X") == unknown.replace("nobody@nowhere.example", "X")


def test_a_withdrawn_invitation_is_not_sent_again(client, company, owner, mail):
    invited = _invite(client, company, owner, mail)
    invited.active = False
    db.session.commit()
    client.post("/forgot-password", data={"email": invited.email})
    assert mail == []


def test_signup_with_an_existing_address_points_to_forgot_password(client, company, owner, mail):
    invited = _invite(client, company, owner, mail)
    response = client.post("/signup", data={**SIGNUP, "email": invited.email})
    body = response.get_data(as_text=True)
    assert "An account with this email already exists." in body
    assert "use Forgot password if you don&#39;t know the password" in body
