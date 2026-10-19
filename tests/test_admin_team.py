from datetime import timedelta

import pytest

from orderflow.extensions import db
from orderflow.models import ADMIN, OWNER, User
from orderflow.services import clients as client_service
from orderflow.timeutil import utcnow
from tests import factories
from tests.conftest import link_from

INVITE = {"first_name": "Kavya", "last_name": "Menon", "email": "kavya@kaveri.example"}


@pytest.fixture
def signed_in(login, owner):
    login(owner)
    return owner


@pytest.fixture
def invited_admin(company, owner):
    return factories.make_admin(
        company,
        email="new.admin@kaveri.example",
        first_name="Nikhil",
        password=None,
        verified=False,
        invited_by=owner,
        invite_sent_at=utcnow() - timedelta(days=1),
    )


def text(response):
    return response.get_data(as_text=True)


def get_user(user_id):
    return db.session.get(User, user_id)


def sign_in(app, user):
    browser = app.test_client()
    response = browser.post("/login", data={"email": user.email, "password": factories.PASSWORD})
    assert response.status_code == 302
    return browser


# Access ----------------------------------------------------------------------

ENDPOINTS = [
    ("get", "/app/team"),
    ("post", "/app/team/invite"),
    ("post", "/app/team/{id}/resend-invite"),
    ("post", "/app/team/{id}/status"),
    ("post", "/app/team/{id}/delete"),
]
DATA = {**INVITE, "active": "0"}


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_anonymous_users_are_sent_to_sign_in(client, invited_admin, mail, method, path):
    response = getattr(client, method)(path.format(id=invited_admin.id), data=DATA)
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    assert get_user(invited_admin.id).active and mail == []


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_clients_are_sent_back_to_their_portal(client, login, buyer, invited_admin, mail, method, path):
    login(buyer)
    response = getattr(client, method)(path.format(id=invited_admin.id), data=DATA)
    assert response.status_code == 302 and response.headers["Location"] == "/portal/"
    assert get_user(invited_admin.id).active and mail == []


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_admins_cannot_manage_the_team(client, login, admin, invited_admin, mail, method, path):
    login(admin)
    response = getattr(client, method)(path.format(id=invited_admin.id), data=DATA)
    assert response.status_code == 302 and response.headers["Location"] == "/app/"
    assert "That page isn&#39;t available for your account." in text(client.get("/app/"))
    assert get_user(invited_admin.id) is not None and get_user(invited_admin.id).active
    assert db.session.scalar(db.select(User).where(User.email == INVITE["email"])) is None
    assert mail == []


def test_admins_cannot_suspend_the_owner_or_each_other(client, login, owner, admin, company):
    other_admin = factories.make_admin(company, email="other.admin@kaveri.example")
    login(admin)
    for target in (owner, other_admin, admin):
        client.post(f"/app/team/{target.id}/status", data={"active": "0"})
        client.post(f"/app/team/{target.id}/delete")
    assert all(get_user(user.id).active for user in (owner, admin, other_admin))


@pytest.mark.parametrize(
    ("method", "path"),
    [("post", "/app/team/{id}/resend-invite"), ("post", "/app/team/{id}/status"), ("post", "/app/team/{id}/delete")],
)
def test_other_companies_and_clients_are_not_found(client, signed_in, buyer, other_company, method, path):
    for target in (other_company["owner"], buyer, other_company["client"]):
        response = getattr(client, method)(path.format(id=target.id), data={"active": "0"})
        assert response.status_code == 404
        assert get_user(target.id).active


def test_unknown_ids_and_get_requests(client, signed_in, invited_admin):
    assert client.post("/app/team/999999/status", data={"active": "0"}).status_code == 404
    for path in ("status", "delete", "resend-invite"):
        assert client.get(f"/app/team/{invited_admin.id}/{path}").status_code == 405
    assert client.get("/app/team/invite").status_code == 405


# List ------------------------------------------------------------------------


def test_team_lists_the_owner_first_then_admins(client, signed_in, company, admin, invited_admin, buyer, other_company):
    suspended = factories.make_admin(company, email="away@kaveri.example", first_name="Zoya", active=False)
    admin.last_login_at = utcnow() - timedelta(hours=5)
    db.session.commit()
    body = text(client.get("/app/team"))
    assert (
        body.index("owner@kaveri.example") < body.index("ops@kaveri.example") < body.index("new.admin@kaveri.example")
    )
    assert 'Priya Test <span class="muted">(you)</span>' in body and "Account owner" in body
    assert ">Active</span>" in body and ">Invited</span>" in body and ">Suspended</span>" in body
    assert "5 hr ago" in body and "Never" in body and "Invited 1 day ago" in body
    assert suspended.email in body
    assert buyer.email not in body and other_company["owner"].email not in body
    assert f'action="/app/team/{invited_admin.id}/resend-invite"' in body
    assert f'action="/app/team/{invited_admin.id}/delete"' in body
    assert f'action="/app/team/{admin.id}/delete"' not in body  # joined admins can only be suspended
    assert f'action="/app/team/{signed_in.id}/status"' not in body
    assert 'name="active" value="1"' in body and 'name="active" value="0"' in body
    assert "None" not in body and 'aria-current="page"' in body


def test_a_team_of_one_suggests_inviting_someone(client, signed_in):
    body = text(client.get("/app/team"))
    assert "It's just you so far" in body
    assert 'name="first_name"' in body and 'name="email"' in body


# Invite ----------------------------------------------------------------------


def test_inviting_a_teammate(app, client, signed_in, company, mail):
    response = client.post("/app/team/invite", data={**INVITE, "email": " Kavya@Kaveri.Example "})
    assert response.status_code == 302 and response.headers["Location"] == "/app/team"
    kavya = db.session.scalar(db.select(User).where(User.email == "kavya@kaveri.example"))
    assert (kavya.role, kavya.company_id, kavya.invited_by_id) == (ADMIN, company.id, signed_in.id)
    assert not kavya.has_accepted_invite and kavya.invite_sent_at is not None
    assert len(mail) == 1 and mail[0].to == ["kavya@kaveri.example"]
    assert "invited you to join Kaveri Supplies" in mail[0].subject
    body = text(client.get("/app/team"))
    assert "Invitation sent to kavya@kaveri.example." in body and "Kavya Menon" in body

    browser = app.test_client()
    link = link_from(mail[0], "/invite/")
    response = browser.post(link, data={"password": "a brand new phrase", "confirm": "a brand new phrase"})
    assert response.headers["Location"] == "/app/"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"first_name": ""}, "Enter their first name."),
        ({"email": ""}, "Enter their work email address."),
        ({"email": "kavya at kaveri"}, "Enter a valid email address"),
        ({"last_name": "x" * 81}, "Keep the name to 80 characters or fewer."),
    ],
)
def test_invite_validation_keeps_input_on_the_team_page(client, signed_in, mail, changes, message):
    response = client.post("/app/team/invite", data={**INVITE, **changes})
    body = text(response)
    assert response.status_code == 200 and message in body
    assert "Priya Test" in body  # the team list is still shown
    for name, value in INVITE.items():
        if name not in changes:
            assert f'value="{value}"' in body
    assert mail == []


def test_invite_explains_taken_emails(client, signed_in, admin, buyer, other_company, mail):
    body = text(client.post("/app/team/invite", data={**INVITE, "email": admin.email}))
    assert "ops@kaveri.example is already on your team." in body
    body = text(client.post("/app/team/invite", data={**INVITE, "email": buyer.email}))
    assert "belongs to one of your clients" in body
    body = text(client.post("/app/team/invite", data={**INVITE, "email": other_company["owner"].email}))
    assert "Someone with this email already has an OrderFlow account." in body
    assert get_user(buyer.id).role == "client" and mail == []


def test_invite_race_is_reported(client, signed_in, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from orderflow.admin import team as views

    def racing(*args, **kwargs):
        raise IntegrityError("INSERT", {}, Exception("duplicate"))

    monkeypatch.setattr(views, "invite_user", racing)
    assert "Someone with this email already has an OrderFlow account." in text(
        client.post("/app/team/invite", data=INVITE)
    )


# Resend ----------------------------------------------------------------------


def test_resend_invitation(client, signed_in, invited_admin, admin, mail):
    response = client.post(f"/app/team/{invited_admin.id}/resend-invite", follow_redirects=True)
    assert "Sent a new invitation to new.admin@kaveri.example." in text(response)
    assert len(mail) == 1
    response = client.post(f"/app/team/{invited_admin.id}/resend-invite", follow_redirects=True)
    assert "less than a minute ago" in text(response) and len(mail) == 1
    response = client.post(f"/app/team/{admin.id}/resend-invite", follow_redirects=True)
    assert "has already accepted their invitation" in text(response) and len(mail) == 1


def test_resend_refused_while_suspended(client, signed_in, invited_admin, mail):
    invited_admin.active = False
    db.session.commit()
    response = client.post(f"/app/team/{invited_admin.id}/resend-invite", follow_redirects=True)
    assert "is suspended" in text(response) and mail == []


# Suspend and reactivate ------------------------------------------------------


def test_suspending_an_admin_signs_them_out_everywhere(app, client, signed_in, admin):
    browser = sign_in(app, admin)
    assert browser.get("/app/products").status_code == 200
    response = client.post(f"/app/team/{admin.id}/status", data={"active": "0"}, follow_redirects=True)
    assert "Suspended Arjun Test. They&#39;ve been signed out" in text(response)
    assert get_user(admin.id).active is False
    assert browser.get("/app/products").headers["Location"].startswith("/login")
    assert client.get("/app/team").status_code == 200  # the owner is unaffected


def test_status_is_explicit_and_idempotent(app, client, signed_in, admin):
    url = f"/app/team/{admin.id}/status"
    client.post(url, data={"active": "0"})
    version = get_user(admin.id).auth_version
    response = client.post(url, data={"active": "0"}, follow_redirects=True)
    assert "Arjun Test is already suspended." in text(response)
    assert get_user(admin.id).auth_version == version
    response = client.post(url, data={"active": "1"}, follow_redirects=True)
    assert "Reactivated Arjun Test. They can sign in again." in text(response)
    assert get_user(admin.id).active is True
    assert "already active" in text(client.post(url, data={"active": "1"}, follow_redirects=True))
    assert sign_in(app, admin).get("/app/").status_code == 200


def test_reactivating_an_invited_admin_reminds_to_resend(client, signed_in, invited_admin):
    client.post(f"/app/team/{invited_admin.id}/status", data={"active": "0"})
    response = client.post(f"/app/team/{invited_admin.id}/status", data={"active": "1"}, follow_redirects=True)
    assert "Resend their invitation so they can set a password." in text(response)


@pytest.mark.parametrize("data", [{}, {"active": "off"}, {"active": "10"}])
def test_status_needs_an_explicit_target(client, signed_in, admin, data):
    assert client.post(f"/app/team/{admin.id}/status", data=data).status_code == 400
    assert get_user(admin.id).active


def test_owner_cannot_suspend_or_remove_themselves(client, signed_in):
    response = client.post(f"/app/team/{signed_in.id}/status", data={"active": "0"}, follow_redirects=True)
    assert "You can&#39;t suspend your own account." in text(response)
    response = client.post(f"/app/team/{signed_in.id}/delete", follow_redirects=True)
    assert "You can&#39;t remove your own account." in text(response)
    owner = get_user(signed_in.id)
    assert owner.active and owner.role == OWNER


def test_another_owner_cannot_be_suspended_or_removed(client, signed_in, company):
    co_owner = factories.make_owner(company, email="partner@kaveri.example", first_name="Dev", password=None)
    response = client.post(f"/app/team/{co_owner.id}/status", data={"active": "0"}, follow_redirects=True)
    assert "Dev Test is an owner of this account, so they can&#39;t be suspended." in text(response)
    response = client.post(f"/app/team/{co_owner.id}/delete", follow_redirects=True)
    assert "so they can&#39;t be removed." in text(response)
    assert get_user(co_owner.id).active


# Remove ----------------------------------------------------------------------


def test_withdrawing_an_invitation_removes_the_account(app, client, signed_in, invited_admin, mail):
    client.post(f"/app/team/{invited_admin.id}/resend-invite")
    link = link_from(mail[0], "/invite/")
    invited_id = invited_admin.id
    response = client.post(f"/app/team/{invited_id}/delete", follow_redirects=True)
    assert "Withdrew the invitation for new.admin@kaveri.example." in text(response)
    assert get_user(invited_id) is None
    assert app.test_client().get(link).status_code == 400


def test_joined_admins_can_only_be_suspended(client, signed_in, admin):
    response = client.post(f"/app/team/{admin.id}/delete", follow_redirects=True)
    assert "has already joined, so their account can&#39;t be removed. Suspend it instead" in text(response)
    assert get_user(admin.id) is not None


def test_remove_failure_is_reported(client, signed_in, invited_admin, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    def failing_commit():
        raise IntegrityError("DELETE", {}, Exception("still referenced"))

    monkeypatch.setattr(db.session, "commit", failing_commit)
    response = client.post(f"/app/team/{invited_admin.id}/delete")
    monkeypatch.undo()
    assert response.status_code == 302
    assert "We couldn&#39;t remove new.admin@kaveri.example" in text(client.get("/app/team"))
    assert get_user(invited_admin.id) is not None


def test_resend_service_is_shared_with_clients(app, invited_admin):
    category, message = client_service.resend_invitation(invited_admin)
    assert category == "success" and invited_admin.email in message
